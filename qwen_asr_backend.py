# -*- coding: utf-8 -*-
"""qwen_asr_backend.py —— Qwen3-ASR 的 faster-whisper 兼容包装层

为什么需要这一层
================
POC（bench/eval_qwen3asr.py，2026-10-19）已经量化的两件事：

  ① 干净/新闻腔素材上 Qwen3-ASR-0.6B 全面优于 whisper-medium
     （zh CER 0.000 vs 0.417 —— whisper 把简体转成繁体；vi WER 0.000 vs 0.017）；
  ② 难样本（84s 越南语吵架场景）上它**没有 nsp/logp/cr 质量闸信号**，
     整块长音频直接喂会复读（n-gram 复读率 7.26% vs whisper 1.49%）。

所以上线形态必须是「VAD 切句 → 逐段转写 → 文本侧复读闸」，
本模块把这整套包进一个 **whisper 兼容**的 `transcribe()` 里：
pretranslate 的 `iter_chunk_cues()` 一行不改就能用。

接口约定（对齐 faster-whisper 的 WhisperModel.transcribe）
==========================================================
  · 入参：`transcribe(audio, language=..., **whisper 专属参数全收下)`
    —— beam_size / vad_filter / word_timestamps / condition_on_previous_text /
    initial_prompt / hallucination_silence_threshold 等：LLM 系 ASR 没有
    对应概念，**收下但忽略**（不能让调用方 KeyError）。
  · 返回：`(segments 生成器, info)`；segment 有 start/end/text/words，
    word 有 word/start/end —— `_seg_sentences` 只认这三个属性。
  · info.language 用 whisper 的两字母码（"vi"/"zh"/...），
    因为下游 make_translator 的 src_hint 按 whisper 口径走。

与 whisper 的语义差异（都在注释里标了"为什么"）
================================================
  · 词级时间戳是**合成**的：Qwen3-ASR 不出词时间戳，按 token 字符量
    占比把段时长摊给各词。断句因此走「停顿驱动」（VAD 切点），
    这正是调研报告 §2.1 OptiSub 路线的前提，不依赖模型标点。
  · 语言检测：qwen_asr 用语言名（"Vietnamese"）不是码（"vi"），
    两边做映射；自动检测只在第一段做，之后复用（口语里段间跳语言
    是噪声不是事实）。
  · 复读闸 = live_subtitle_v2.is_hallucination（模块级只有 stdlib/numpy，
    import 零副作用，实测安全）+ 相邻段同文判重（LLM 系 ASR 特有的
    整段复读形态）。

模型文件
========
  models/Qwen3-ASR-0.6B   （POC 已下载，1.9GB）
  models/Qwen3-ASR-1.7B   （更高精度，3.4GB，待下载）
  环境变量 QWEN3_ASR_DIR 可整体改指（F 盘/移动硬盘场景）。
"""
from __future__ import annotations
import model_home  # noqa: E402,F401  # 先把模型目录钉到 D 盘（务必在 HF/faster-whisper 之前）

import os
import re
import time
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))

#: whisper 语言码 → qwen_asr 语言名。qwen_asr 收全名。
#: 只列本项目用得到的；不在表里的码 → 传 None 走它自己的 LID。
LANG_NAME = {
    "en": "English", "zh": "Chinese", "vi": "Vietnamese",
    "ja": "Japanese", "ko": "Korean", "yue": "Cantonese",
    "th": "Thai", "id": "Indonesian", "ms": "Malay",
    "ru": "Russian", "fr": "French", "de": "German", "es": "Spanish",
    "pt": "Portuguese", "it": "Italian", "ar": "Arabic", "hi": "Hindi",
}
NAME_TO_CODE = {v: k for k, v in LANG_NAME.items()}

#: CLI 名 → 模型目录名（models/ 下）。
MODEL_DIRS = {
    "qwen3-asr-0.6b": "Qwen3-ASR-0.6B",
    "qwen3-asr-1.7b": "Qwen3-ASR-1.7B",
}

_CJK = re.compile(r"[぀-ヿ一-鿿]")

#: VAD 默认参数 —— 与 pretranslate 现行 whisper 路径完全同款
#: （threshold 0.35 + 500ms 静音 + 200ms 垫片）。同款参数的意义：
#: 以后 A/B 两条后端时，切句差异全归模型，不掺 VAD 配置的变量。
_VAD_DEFAULTS = dict(threshold=0.35, min_silence_duration_ms=500,
                     speech_pad_ms=200)

#: 单次喂给模型的最长语音段（秒）。silero VAD 的 max_speech_duration_s
#: 会在连续说话超长时强制切一刀 —— 没有它，一段 60s 的连续独白
#: 会整段进模型，复读风险回到 POC 观察到的形态。
_MAX_SPEECH_SEC = 30.0


# ── 词级时间戳合成 ─────────────────────────────────────────

def _synthesize_words(text: str, start: float, end: float):
    """把一段无时间戳的文本摊成带时间的 word 列表。

    为什么不做"均匀"摊：拉丁 token 长短差很大（"internationalization"
    vs "a"），按字符量占比摊，词边界和语音节奏的偏差最小化。
    CJK 逐字成词（中文的"词"本来就该逐字切，_seg_sentences 的
    标点断句和 subtitle_layout 的逐字断行都吃这个形态）。

    faster-whisper 的 word.word 自带前导空格（拉丁系），下游
    `"".join(w.word ...)` 直接拼 —— 这里保持同款约定。
    """
    toks: list[str] = []          # 每个 = " x"（拉丁，带前导空格）或 "字"
    cur = ""
    lead = False

    def _flush() -> None:
        nonlocal cur, lead
        if cur:
            toks.append((" " if lead else "") + cur)
        cur = ""
        lead = False

    for ch in text:
        if ch == " ":
            _flush()
            lead = True
        elif _CJK.match(ch):
            _flush()              # CJK 字独立成词，不带前导空格
            toks.append(ch)
        else:
            cur += ch
    _flush()
    if not toks:
        return []

    weights = [max(1, len(t.strip())) for t in toks]
    total = float(sum(weights))
    dur = max(0.05, float(end) - float(start))
    ws, t = [], float(start)
    for tok, w in zip(toks, weights):
        d = dur * w / total
        ws.append(SimpleNamespace(word=tok,
                                  start=round(t, 3),
                                  end=round(min(t + d, end), 3)))
        t += d
    return ws


# ── 后端本体 ───────────────────────────────────────────────

class Qwen3ASRBackend:
    """faster-whisper WhisperModel 的替身。

    `is_qwen3_backend = True` 是给调用方做的鸭子类型标记 ——
    process() 用它跳过 BatchedInferencePipeline（那层是 ctranslate2
    专属，拿本对象会炸）。
    """

    is_qwen3_backend = True

    def __init__(self, model_dir: str, device: str = "cpu", log=print):
        # Qt 之后加载 torch DLL 的坑（本机实测，见 native_preload.py）
        # —— CLI 路径没 Qt，幂等调用无害；将来 overlay 链路复用本类时
        # 这里已经兜住。
        try:
            from native_preload import preload_torch_dlls
            preload_torch_dlls()
        except Exception:
            pass
        import torch
        from qwen_asr import Qwen3ASRModel

        self.device = device
        # 4070 的 fp16 走起来最稳；CPU 只能 fp32（bf16 在老 CPU 上慢一个量级）
        dtype = (torch.float16 if device == "cuda" else torch.float32)
        t0 = time.time()
        self._q = Qwen3ASRModel.from_pretrained(
            model_dir, dtype=dtype, device_map=device,
            max_inference_batch_size=1, max_new_tokens=512)
        log(f"  模型加载 {time.time() - t0:.1f}s（qwen-asr / {device}）")

        # 复读闸：直接复用 live 路线那份判据（单字复读/片段复读/循环串），
        # 不另写一份 —— 两套判据漂移比没有闸更难查。
        from live_subtitle_v2 import is_hallucination
        self._is_hal = is_hallucination

    # -- whisper 兼容入口 ------------------------------------

    def transcribe(self, audio, language=None, beam_size=5,
                   vad_filter=True, word_timestamps=True,
                   vad_parameters=None, condition_on_previous_text=True,
                   initial_prompt=None, hallucination_silence_threshold=None,
                   **_ignored):
        """返回 (segments 生成器, info)。

        命名参数全是 faster-whisper 的 —— 调用方（iter_chunk_cues /
        transcribe_chunk）不用改一行。没有对应概念的参数收下但忽略。
        """
        t0 = time.time()

        # ── ① VAD 前置切句（POC 结论：这步不能省）──────────
        from faster_whisper.vad import get_speech_timestamps, VadOptions
        vp = dict(_VAD_DEFAULTS)
        for k, v in (vad_parameters or {}).items():
            if v is not None:
                vp[k] = v
        opts = VadOptions(
            threshold=float(vp.get("threshold", 0.35)),
            min_silence_duration_ms=int(vp.get("min_silence_duration_ms",
                                                500)),
            speech_pad_ms=int(vp.get("speech_pad_ms", 200)),
            max_speech_duration_s=_MAX_SPEECH_SEC)
        sr = 16000
        # get_speech_timestamps 返回样本下标（silero 原版口径）
        chunks = get_speech_timestamps(audio, opts, sampling_rate=sr)

        # ── ② 语言参数：whisper 码 → qwen 语言名 ──────────
        lang_code = None
        lang_name = None
        if language:
            lang_code = str(language).strip().lower()
            lang_name = LANG_NAME.get(lang_code)
            if lang_name is None and lang_code in NAME_TO_CODE.values():
                lang_name = lang_code        # 调用方直接传了全名
            if lang_name is None:
                lang_code = None             # 未知码 → 走自动检测

        info = SimpleNamespace(
            language=lang_code,
            language_probability=1.0 if lang_code else 0.0,
            duration=len(audio) / sr)

        def _run_chunk(a):
            """转写一段音频 → (text, detected_language_name)。失败返回 ("", None)。"""
            try:
                out = self._q.transcribe(
                    audio=(a, sr),
                    **({"language": lang_name} if lang_name else {}))[0]
            except Exception as e:
                print(f"  ！qwen3-asr 段转写失败"
                      f"（{type(e).__name__}: {e}），跳过一段")
                return "", None
            return ((getattr(out, "text", "") or "").strip(),
                    getattr(out, "language", None))

        # ── ③ 语言检测必须在**返回前**完成 ─────────────────
        # `iter_chunk_cues` 在开始迭代前就读取 info.language（faster-whisper
        # 就是这么用的：它在首次迭代前完成 LID）。如果等生成器跑到第一段
        # 才写 info，调用方永远看到 None → 译器不会被创建 → 只出原文不出
        # 译文（2026-10-19 端到端实测踩到：5 条字幕、0 条译文）。
        # 代价是首段要提前算一次（本来也要算），总耗时不变。
        first_text = ""
        if lang_name is None and chunks:
            nz = next((c for c in chunks
                       if c["end"] - c["start"] >= int(0.2 * sr)), None)
            if nz is not None:
                first_text, det = _run_chunk(audio[nz["start"]:nz["end"]])
                if det:
                    lang_name = str(det)
                    lang_code = NAME_TO_CODE.get(lang_name)
                    info.language = lang_code or lang_name
                    info.language_probability = 0.5   # qwen 不输出置信度
        first_used = False

        def _gen():
            nonlocal first_used, lang_name, lang_code
            kept = skipped = 0
            prev_text = ""
            for ch in chunks:
                a = audio[ch["start"]:ch["end"]]
                cs, ce = ch["start"] / sr, ch["end"] / sr
                if len(a) < int(0.2 * sr):   # <0.2s 的碎语音：噪声为主
                    continue
                if not first_used and first_text:
                    # 首段已经在 LID 阶段转写过了，直接用，别重复算
                    first_used = True
                    text = first_text
                else:
                    text, _det = _run_chunk(a)
                    if text and lang_name is None and _det:
                        lang_name = str(_det)
                        lang_code = NAME_TO_CODE.get(lang_name)
                        info.language = lang_code or lang_name
                if not text:
                    continue

                # ── ④ 文本侧复读闸（LLM 系 ASR 唯一可用的闸）──
                #     整段同文复读是它区别于 whisper 的形态，单独判。
                if self._is_hal(text) or text == prev_text:
                    skipped += 1
                    continue
                prev_text = text

                ws = _synthesize_words(text, cs, ce)
                if not ws:
                    continue
                kept += 1
                yield SimpleNamespace(start=cs, end=ce, text=text, words=ws)

            el = time.time() - t0
            if skipped:
                print(f"  复读闸拦下 {skipped} 段（qwen3-asr）")
            print(f"  qwen3-asr 识别完成：{kept} 段 / "
                  f"{len(audio) / sr:.0f}s，用时 {el:.0f}s")

        return _gen(), info


# ── 加载入口（pretranslate.load_asr 调这里）────────────────

def resolve_model_dir(cli_name: str) -> str:
    """CLI 名（qwen3-asr-0.6b / local:qwen3-asr-1.7b / ...）→ 本地目录。

    查找顺序：QWEN3_ASR_DIR 环境变量 → 项目 models/ 目录。
    找不到不静默降级 —— 缓存键里记的是"用了哪个模型"，偷偷换模型
    会让缓存把 A 模型的产物当成 B 模型的复用。
    """
    key = str(cli_name).strip().lower().replace("_", "-")
    if ":" in key:
        key = key.split(":", 1)[1]
    size = ("0.6b" if "0.6" in key
            else "1.7b" if "1.7" in key else None)
    if size is None:
        raise SystemExit(
            f"无法识别的 Qwen3-ASR 模型名：{cli_name!r}"
            f"（可用：{', '.join(MODEL_DIRS)}）")
    dirname = MODEL_DIRS[f"qwen3-asr-{size}"]

    env = os.environ.get("QWEN3_ASR_DIR")
    if env and os.path.isdir(env):
        return env
    p = os.path.join(HERE, "models", dirname)
    if os.path.isdir(p):
        return p
    raise SystemExit(
        f"缺模型文件：{p}\n"
        f"  下载（任选其一）：\n"
        f"    python -c \"from modelscope import snapshot_download; "
        f"snapshot_download('Qwen/Qwen3-ASR-{size.upper()}', "
        f"local_dir=r'{p}')\"\n"
        f"    huggingface-cli download Qwen/Qwen3-ASR-{size.upper()} "
        f"--local-dir \"{p}\"\n"
        f"  或设 QWEN3_ASR_DIR 指向已有模型目录")


def load_qwen3_asr(cli_name: str, log=print) -> Qwen3ASRBackend:
    """按 CLI 名加载 Qwen3-ASR 后端（设备选择逻辑与 load_asr 同款）。"""
    model_dir = resolve_model_dir(cli_name)
    device = "cpu"
    # 注意：必须用 torch.cuda.is_available() 判定，不能用 gpu_setup.gpu_available()
    # —— 后者反映的是 ctranslate2（whisper）的 CUDA 可用性。本机曾出现
    # ctranslate2 有 CUDA 而 torch 是 +cpu 构建的组合：选了 cuda →
    # from_pretrained 直接 AssertionError("Torch not compiled with CUDA
    # enabled") → 上层报"全部完成：0/1"（2026-10-19 watch.log 实测）。
    try:
        from gpu_setup import enable_cuda_dlls
        enable_cuda_dlls()
        import torch
        if torch.cuda.is_available():
            device = "cuda"
        else:
            log(f"  注意：torch 无 CUDA（{torch.__version__}），"
                f"qwen-asr 回退 CPU；要 GPU 请装 cu 版 torch")
    except Exception:
        pass
    log(f"  识别模型 {cli_name}（qwen-asr / {device}）")
    return Qwen3ASRBackend(model_dir, device=device, log=log)


if __name__ == "__main__":
    # 冒烟：拿 POC 样本过一遍完整后端（VAD → 转写 → 闸 → 合成词戳）
    import numpy as np

    def _load_wav(path):
        import wave
        with wave.open(path, "rb") as w:
            n, ch, rate = w.getnframes(), w.getnchannels(), w.getframerate()
            raw = w.readframes(n)
        a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        if ch > 1:
            a = a.reshape(-1, ch).mean(axis=1)
        return a

    import sys
    wav = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        HERE, "samples", "tts_vi.wav")
    lang = sys.argv[2] if len(sys.argv) > 2 else "vi"
    audio = _load_wav(wav)
    m = load_qwen3_asr("qwen3-asr-0.6b")
    segs, info = m.transcribe(audio, language=(lang or None))
    print(f"info.language = {info.language}")
    for s in segs:
        print(f"  [{s.start:7.2f} → {s.end:7.2f}] {s.text}")
        print(f"    words[0]={s.words[0].word!r} @{s.words[0].start}"
              f"  n={len(s.words)}")
