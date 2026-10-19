# -*- coding: utf-8 -*-
"""local_mt.py —— 本地翻译（HY-MT + llama.cpp，完全离线）

为什么能做
==========
F 盘那个同声传译软件包里，除了 GGUF 模型，**还带了现成的 llama.cpp
Windows Vulkan 构建**（llama-server.exe + ggml-vulkan.dll 等）。
这就是"llama-cpp-python 编译失败"的解法 —— 不需要 CUDA Toolkit，
Vulkan 后端在 RTX 4070 上直接可用。

    实测：模型 1075 MB 上显存，生成 151 token/s

模型：HY-MT（腾讯混元翻译专用）
    base  1.8B，1081 MB      实测 0.15s/段
    large HY-MT1.5，4410 MB  支持 36 语言

接口：**必须用它的 chat 模板**（从 GGUF 的 tokenizer.chat_template 读的）
=====================================================================
    {% if system %}<｜hy_begin▁of▁sentence｜>{{ sys }}<｜hy_place▁holder▁no▁3｜>
    {% else %}    <｜hy_begin▁of▁sentence｜>
    {% endif %}
    user      → <｜hy_User｜>{{ content }}
    assistant → <｜hy_Assistant｜>{{ content }}<｜hy_place▁holder▁no▁2｜>
    生成时追加 <｜hy_Assistant｜>

词表里的实际 token id：
    120000 <｜hy_begin▁of▁sentence｜>   句首
    120007 <｜hy_Assistant｜>             生成起点
    120020 <｜hy_place▁holder▁no▁2｜>    assistant 段结束
    120002 <｜hy_▁pad▁｜>                 pad

**这一步不能省。** 实测：裸提示词（不发这些标记）越南语输出 0% 中文
（`thep, thep, thep...` 这种胡言乱语）；发对标记后 100% 中文。
因为那是**训练分布外的畸形输入**。

质量（实测，真实越南语音频 6 段，对照远端 qwen-mt）
==================================================
    本地  中文占比与远端相当，通顺度略好，专有名词偶尔泛化
          延迟中位 0.15s（比远端快 4 倍），硬失败 0/6
    远端  专有名词更准（"số phét"→"指纹" vs 本地"各种证据"）
    结论  各有胜负，都可接受。本地完全离线、零成本。
"""
from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

# llama.cpp 二进制默认位置（从 F 盘软件包里解出来的）
LLAMA_DIRS = [
    os.environ.get("LLAMA_DIR", ""),
    r"D:\NEW WORK\work\llamacpp",
    os.path.join(HERE, "llamacpp"),
]

# 模型默认位置（F 盘自带的翻译模型）
MODEL_CANDIDATES = [
    os.environ.get("HYMT_MODEL", ""),
    r"F:\hh_si_pro\hh-translate-model\hh-translate-base.gguf",
    r"F:\hh_si_pro\hh-translate-model\hh-translate-small.gguf",
    r"F:\hh_si_pro\hh-translate-model\hh-translate-medium.gguf",
    # 7B（HY-MT1.5-7B-GGUF，Q8 约 4.4GB）—— 之前漏了这条，界面误报
    # "找不到 gguf 模型文件"（文件其实在 F:，只是候选表没收录）
    r"F:\hh_si_pro\hh-translate-model\hh-translate-large.gguf",
    os.path.join(HERE, "models", "hh-translate-base.gguf"),
]

# 项目 models/ 和 F: 的 HY-MT 目录里的所有 gguf 都算候选
# （model_manager 下载到 models/；已有的一代/7B 装在 F:，别写死漏项）
try:
    import glob as _glob
    for _d in (os.path.join(HERE, "models"),
               r"F:\hh_si_pro\hh-translate-model"):
        for _p in sorted(_glob.glob(os.path.join(_d, "*.gguf"))):
            if _p not in MODEL_CANDIDATES:
                MODEL_CANDIDATES.append(_p)
except Exception:
    pass

# ── 两代模型用**不同的 token 体系**，模板必须按模型选 ────────────
# 这是实测踩出来的：我以为 HY-MT 家族只有一个模板，把 1.8B 的标记发给 7B，
# 结果 7B 看起来"更差"（空输出、中英混杂、复读）。实际是**发错了 token**。
#
# 1.8B（hy_ 系列标记）
#     <|hy_begin_of_sentence|><|hy_User|>{user}<|hy_Assistant|>
#     bos=120000  eos=120020  pad=120002
# 7B（startoftext / extra_N 系列）
#     user 轮      → <|startoftext|>{content}<|extra_0|>
#     system 轮    → <|startoftext|>{content}<|extra_4|>
#     assistant 轮 → {content}<|eos|>
#     bos=127958  eos=127960  pad=127961
#
# 两套都是从各自 GGUF 的 tokenizer.chat_template 与词表读出来的。
BOS = "<\uff5chy_begin\u2581of\u2581sentence\uff5c>"      # 1.8B
USER = "<\uff5chy_User\uff5c>"
ASST = "<\uff5chy_Assistant\uff5c>"
SOT = "<|startoftext|>"                                    # 7B
EX0 = "<|extra_0|>"

MODEL_FAMILIES = {
    "hy18": lambda body: BOS + USER + body + ASST,
    "hy7": lambda body: SOT + body + EX0,
}

# 走 /v1/chat/completions 的家族（模板由 llama-server 按 GGUF 内嵌
# tokenizer.chat_template 自动应用，不再手拼 token 标记）：
#   hymt2  Hy-MT2 系列（hunyuan_v1_dense，token 体系与 hy18/hy7 都不同，
#          官方提示词文本与 TPL_ZH 一致，直接复用）
#   gemma  TranslateGemma / Gemma3-it（start_turn/end_turn 体系）
CHAT_FAMILIES = {"hymt2", "gemma"}

#: TranslateGemma 模板里要用的语言名（模板内部自己查表，这里我们拼文本）
_LANG_EN = {
    "zh": "Chinese", "zh-CN": "Chinese", "zh-TW": "Traditional Chinese",
    "en": "English", "vi": "Vietnamese", "ja": "Japanese", "ko": "Korean",
    "th": "Thai", "id": "Indonesian", "ms": "Malay", "ru": "Russian",
    "fr": "French", "de": "German", "es": "Spanish", "pt": "Portuguese",
    "it": "Italian", "ar": "Arabic", "hi": "Hindi", "tr": "Turkish",
}


def _guess_lang_code(text):
    """粗判源语言（只给 Gemma 模板的提示头用，判错不影响可读性）。
    CJK → zh / 日文假名 → ja / 韩文 → ko / 越南语声调符号 → vi / 西里尔 → ru。
    """
    import re as _re
    if _re.search(r"[\u3040-\u30ff]", text):
        return "ja"
    if _re.search(r"[\uac00-\ud7af]", text):
        return "ko"
    if _re.search(r"[\u4e00-\u9fff]", text):
        return "zh"
    if _re.search(r"[\u0400-\u04ff]", text):
        return "ru"
    if _re.search(r"[ăâđêôơưĂÂĐÊÔƠƯ]", text) or _re.search(
            r"[àáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệ]", text):
        return "vi"
    return "en"


def _read_gguf_str(f, vt):
    """读一项字符串型 GGUF 元数据值；非字符串返回 None。"""
    if vt == 8:
        n = struct.unpack("<Q", f.read(8))[0]
        return f.read(n).decode("utf-8", "replace")
    _skip_gguf_value(f, vt)
    return None


def _skip_gguf_value(f, vt):
    """跳过一项 GGUF 元数据值（只为找 bos_token_id，别的不需要）。"""
    sizes = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8,
             12: 8}
    if vt == 8:
        n = struct.unpack("<Q", f.read(8))[0]
        f.seek(n, 1)
    elif vt == 9:
        et = struct.unpack("<I", f.read(4))[0]
        n = struct.unpack("<Q", f.read(8))[0]
        if et == 8:
            for _ in range(n):
                ln = struct.unpack("<Q", f.read(8))[0]
                f.seek(ln, 1)
        else:
            f.seek(n * sizes.get(et, 0), 1)
    else:
        f.seek(sizes.get(vt, 0), 1)


def detect_family(path):
    """判断模型家族，决定用哪套提示词包装。

    优先读 **general.architecture / general.name**（Hy-MT2、TranslateGemma
    这类新模型靠它识别）；读不到再退回 bos token id（hy18/hy7 两代的
    区别是 120000 vs 127958），最后按文件大小兜底。
    """
    arch = name = None
    bos = None
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"GGUF":
                return "hy18"
            f.read(4)
            f.read(8)                              # n_tensors
            n_kv = struct.unpack("<Q", f.read(8))[0]
            for _ in range(n_kv):
                n = struct.unpack("<Q", f.read(8))[0]
                key = f.read(n).decode("utf-8", "replace")
                vt = struct.unpack("<I", f.read(4))[0]
                if key == "tokenizer.ggml.bos_token_id" and vt == 4:
                    bos = struct.unpack("<I", f.read(4))[0]
                elif key in ("general.architecture", "general.name",
                             "general.basename"):
                    v = _read_gguf_str(f, vt)
                    if v:
                        if key == "general.architecture":
                            arch = v.lower()
                        else:
                            name = (name or "") + " " + v.lower()
                else:
                    _skip_gguf_value(f, vt)
    except Exception:
        pass
    if arch and "gemma" in arch:
        return "gemma"
    ident = f"{arch or ''} {name or ''}"
    if "hy-mt2" in ident or "hymt2" in ident or "hunyuan_v1" in ident:
        return "hymt2"
    if bos is not None:
        return "hy7" if bos > 126000 else "hy18"
    try:
        return "hy7" if os.path.getsize(path) > 3_000_000_000 else "hy18"
    except Exception:
        return "hy18"


# ── 官方提示词模板（来源：Tencent-Hunyuan/HY-MT README，非推测）──────
# 官方区分两种，**不能混用**：
#   中↔XX（含中文，用中文提示词）→ 下面这个
#   XX↔XX（不含中文，用英文提示词）→ TPL_XX
# 实测差异（_bench_hymt_official.py，真实越南语 4 段）：
#     我的自造模板 + 我的参数   0.49s
#     官方模板     + 我的参数   0.49s
#     **官方模板 + 官方参数     0.25s（快 2~4 倍）**
TPL_ZH = ("将以下文本翻译为{target}，注意只需要输出翻译后的结果，"
          "不要额外解释：\n\n{src}")
TPL_XX = ("Translate the following segment into {target}, "
          "without additional explanation.\n\n{src}")

# 术语干预（官方预留的能力）。实测**真的生效**：
#     "số phét"     无术语→"各种证据"   注入后→"指纹"        ✓
#     "doãn chung dũng" 无术语→"邓忠义"  注入后→"段忠勇"      ✓
#     "quảng ninh"  无术语→"广宁"      注入后→"广宁省"      ✓
# 注意它救不了**源文本自身就是错的**情况（如 ASR 错字）—— 上游错则下游难救。
TPL_TERM = ("参考下面的翻译：\n{src_term} 翻译成 {tgt_term}\n\n" + TPL_ZH)

# 官方推荐推理参数（README 给的 JSON）。
# 我原来用 top_p=1.0 且没开 repetition_penalty —— 那很可能就是
# 复读（"thep thep thep..."）的来源。实测官方参数还快 2~4 倍。
OFFICIAL_PARAMS = {
    "temperature": 0.7,
    "top_k": 20,
    "top_p": 0.6,
    "repeat_penalty": 1.05,
}
# 兼容旧名（我最初的参数）
DEFAULT_PROMPT = TPL_ZH


def find_binary(name="llama-server.exe"):
    for d in LLAMA_DIRS:
        if d and os.path.isfile(os.path.join(d, name)):
            return os.path.join(d, name)
    return None


def find_model():
    for p in MODEL_CANDIDATES:
        if p and os.path.isfile(p):
            return p
    return None


def resolve_model(model):
    """把多种写法统一成 gguf 实际路径。

    支持：① 已经是存在的文件路径 → 原样返回
          ② 注册表 id（local:hy-mt2-1.8b / local:hh-translate-base / ...）
          ③ 名字片段（"hy-mt2"、"translategemma"、"base"）
    按候选表 basename 包含匹配；找不到返回 None（调用方报错，别静默换模型）。
    """
    if not model:
        return find_model()
    m = str(model)
    if os.path.isfile(m):
        return m
    key = m.split(":", 1)[-1].lower().replace("hh-translate-", "")
    for p in MODEL_CANDIDATES:
        if p and os.path.isfile(p) and key in os.path.basename(p).lower():
            return p
    return None


def available():
    """本地翻译是否可用（二进制 + 模型都在）。返回 (ok, 原因)。"""
    if find_binary() is None:
        return False, "找不到 llama-server.exe（设 LLAMA_DIR 环境变量）"
    if find_model() is None:
        return False, "找不到 HY-MT 模型（设 HYMT_MODEL 环境变量）"
    return True, ""


class LocalMT:
    """管理 llama-server 子进程，并按 HY-MT 的模板发请求。"""

    def __init__(self, model=None, port=8145, n_ctx=1024, n_gpu_layers=99,
                 prompt_template=None, start_timeout=180, target="中文",
                 params=None, terms=None):
        self.model = resolve_model(model)
        self.bin = find_binary()
        self.port = int(port)
        self.n_ctx = int(n_ctx)
        self.ngl = int(n_gpu_layers)
        self.tpl = prompt_template or TPL_ZH
        self.target = target or "中文"
        # 源语言码（Gemma 模板头用）。调用方可 set_src() 覆盖；
        # 不设就按文本粗判（见 _guess_lang_code）。
        self.src = None
        self.params = dict(OFFICIAL_PARAMS)
        if params:
            self.params.update(params)
        # 术语表 [(源词, 目标译法)]。注入时按"源词出现在文本里"逐条加。
        self.terms = list(terms or [])
        # 模型代次决定用哪套 token 标记（见 MODEL_FAMILIES 的说明）
        self.family = detect_family(self.model) if self.model else "hy18"
        self.start_timeout = start_timeout
        self.proc = None
        self.ok = bool(self.bin and self.model)
        self.calls = self.fails = 0
        self.t_last = 0.0
        self.t_start = 0.0
        self.last_err = ""
        self.terms_used = 0

    @property
    def base(self):
        return f"http://127.0.0.1:{self.port}"

    def set_src(self, code):
        """设置源语言码（"vi"/"en"/…）。Gemma 模板头用它。"""
        self.src = (str(code).strip().lower() if code else None)
        return self

    def start(self):
        if not self.ok:
            return False
        if self.proc and self.proc.poll() is None:
            return True
        args = [self.bin, "--model", self.model, "-ngl", str(self.ngl),
                "-c", str(self.n_ctx), "--port", str(self.port),
                "--host", "127.0.0.1"]
        if self.family == "gemma":
            # gemma3 的内嵌 chat_template 要求结构化 content，llama-server
            # 在 init 期会渲染一条假消息做校验 → jinja 抛异常 → 进程退出。
            # 我们本来就自己拼模板走 /completion，不需要它渲染。
            args.append("--no-jinja")
        t0 = time.time()
        self.proc = subprocess.Popen(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd=os.path.dirname(self.bin),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        while time.time() - t0 < self.start_timeout:
            if self.proc.poll() is not None:
                self.last_err = f"llama-server 退出码 {self.proc.returncode}"
                return False
            try:
                with urllib.request.urlopen(self.base + "/health",
                                            timeout=2) as r:
                    if r.status == 200:
                        self.t_start = time.time() - t0
                        return True
            except Exception:
                pass
            time.sleep(0.4)
        self.last_err = f"启动超时（{self.start_timeout}s）"
        return False

    def stop(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=10)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        self.proc = None

    def __call__(self, text, target=None):
        """翻译一段。target 参数只为与远端译器接口对齐（这里忽略）。"""
        text = (text or "").strip()
        if not text or not self.ok:
            return ""
        # 术语干预：只注入**在本文本里出现**的词条（官方模板）
        hits = [(s, t) for s, t in self.terms if s and s.lower() in text.lower()]
        if hits:
            self.terms_used += len(hits)
            body = "\n".join(TPL_TERM.format(src_term=s, tgt_term=t,
                                            target=self.target, src=text)
                             for s, t in hits)
            prompt = body
        else:
            prompt = self.tpl.format(target=self.target, src=text)
        t0 = time.time()
        try:
            if self.family == "gemma":
                # TranslateGemma 的官方模板要求结构化 content（llama-server
                # 的 chat 端点在 init 期就会拿不到 mapping 而报
                # "User role must provide `content`..."）。它本质上就是
                # 一段固定文本，自己拼完走 /completion 最稳。
                out = self._gemma(prompt, text)
            elif self.family in CHAT_FAMILIES:
                out = self._chat(prompt)
            else:
                payload = {"prompt": MODEL_FAMILIES[self.family](prompt),
                           "n_predict": max(64, 6 * len(text)),
                           "stream": False}
                payload.update(self.params)
                body = json.dumps(payload, ensure_ascii=False).encode()
                req = urllib.request.Request(
                    self.base + "/completion", data=body,
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=60) as r:
                    j = json.loads(r.read().decode("utf-8", "replace"))
                out = (j.get("content") or "").strip()
        except Exception as e:
            self.fails += 1
            self.last_err = f"{type(e).__name__}: {str(e)[:100]}"
            return ""
        self.calls += 1
        self.t_last = time.time() - t0
        # 只取第一行（模型偶尔会续写）
        return out.split("\n")[0].strip()

    def _gemma(self, prompt, raw_text):
        """TranslateGemma 官方模板（从 GGUF 的 chat_template 原文复刻）。

        模板要点：单轮 user + 严格格式头 + `<end_of_turn>` + 生成提示。
        源语言码走 self.src（调用方可用 set_src 改；默认按文本猜）。
        """
        src_code = self.src or _guess_lang_code(raw_text)
        src_name = _LANG_EN.get(src_code, src_code)
        dst_code = "zh-CN" if str(self.target).startswith("中文") or \
            str(self.target).lower() in ("zh", "zh-cn") else "en"
        dst_name = _LANG_EN.get(dst_code, dst_code)
        head = (f"You are a professional {src_name} ({src_code}) to "
                f"{dst_name} ({dst_code}) translator. Your goal is to "
                f"accurately convey the meaning and nuances of the original "
                f"{src_name} text while adhering to {dst_name} grammar, "
                f"vocabulary, and cultural sensitivities.\n")
        body = (f"Produce only the {dst_name} translation, without any "
                f"additional explanations or commentary. Please translate "
                f"the following {src_name} text into {dst_name}:\n\n\n"
                f"{raw_text}")
        full = (f"<start_of_turn>user\n{head}{body}<end_of_turn>\n"
                f"<start_of_turn>model\n")
        payload = {"prompt": full,
                   "n_predict": max(64, 6 * len(raw_text)),
                   "stream": False, "temperature": 0.2, "top_p": 0.9,
                   "top_k": 40, "repeat_penalty": 1.05}
        body_json = json.dumps(payload, ensure_ascii=False).encode()
        req = urllib.request.Request(self.base + "/completion",
                                     data=body_json,
                                     headers={"Content-Type":
                                              "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            j = json.loads(r.read().decode("utf-8", "replace"))
        return (j.get("content") or "").strip()

    def _chat(self, prompt):
        """/v1/chat/completions 路径（CHAT_FAMILIES 专用）。"""
        payload = {"messages": [{"role": "user", "content": prompt}],
                   "stream": False,
                   "max_tokens": max(64, 6 * len(prompt) // 4),
                   # 翻译任务要稳定输出：低采样。Hy-MT2 官方推荐参数
                   # 与一代相同（temperature 0.7 / top_p 0.6 / top_k 20）
                   "temperature": self.params.get("temperature", 0.7),
                   "top_p": self.params.get("top_p", 0.6),
                   "top_k": self.params.get("top_k", 20),
                   "repeat_penalty": self.params.get("repeat_penalty", 1.05)}
        body = json.dumps(payload, ensure_ascii=False).encode()
        req = urllib.request.Request(self.base + "/v1/chat/completions",
                                     data=body,
                                     headers={"Content-Type":
                                              "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            j = json.loads(r.read().decode("utf-8", "replace"))
        return (j["choices"][0]["message"].get("content") or "").strip()

    def status(self):
        if not self.ok:
            return "本地翻译=不可用"
        return (f"本地翻译=HY-MT {os.path.basename(self.model)}"
                f"（{self.family}）"
                f"　启动 {self.t_start:.1f}s　调用 {self.calls}"
                f"　失败 {self.fails}　上次 {self.t_last:.2f}s")


def from_args(model=None, port=8145, n_ctx=1024, terms=None):
    ok, why = available()
    if not ok:
        print(f"  本地翻译不可用：{why}")
        return None
    m = LocalMT(model=model, port=port, n_ctx=n_ctx, terms=terms)
    print(f"  启动本地翻译 {os.path.basename(m.model)} …")
    if not m.start():
        print(f"  启动失败：{m.last_err}")
        m.stop()
        return None
    print(f"  {m.status()}")
    return m


if __name__ == "__main__":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    ok, why = available()
    print(f"  available: {ok} {why}")
    print(f"  binary: {find_binary()}")
    print(f"  model : {find_model()}")
    if ok:
        m = from_args()
        if m:
            for s in ("I never thought I would see you again.",
                      "tình quảng ninh đưa ra xét sử bị cáo doãn chung dũng",
                      "Я только на это рассчитываю на самом деле, здесь женщина."):
                out = m(s)
                print(f"    {m.t_last:5.2f}s  {s[:44]}")
                print(f"            → {out[:56]}")
            m.stop()
