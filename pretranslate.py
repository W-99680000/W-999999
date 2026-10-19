# -*- coding: utf-8 -*-
"""pretranslate.py —— 点播视频的"缓存收集翻译"（预翻译）管线。

为什么有它
==========
回环（loopback）实时路线有三个绕不开的物理约束：
  · 音频播到哪才能听到哪 —— 识别窗只有 2~4 秒，句子被切碎
  · 必须保速度 —— 只能用 medium，large-v3 的准确度用不上
  · 首字延迟 = 段长 + 识别 + 翻译 ≈ 1~3 秒，永远存在

对**点播**内容（本地文件 / 网页 VOD），整个音频在播放前就在手里，
这些约束全部消失。所以这里走完全另一条路：

    来源（文件 PyAV 直解 / URL 由 yt-dlp 拉音频流）
      → large-v3 全文识别（30 秒上下文 + 词级时间戳）
      → 按标点+时长切成字幕 cue（完整句子，不是碎片）
      → qwen-mt 整句批量翻译（带源语言提示）
      → 同名双语 .srt（中文一行、原文一行，交给播放器对齐）
      → 缓存 + 断点续跑（同一部片第二次秒出）

直播不适用（音频在播出前不存在），直播请继续用 start.bat 的回环路线。

用法
----
    python pretranslate.py 电影.mp4                    # 本地文件
    python pretranslate.py https://www.bilibili.com/video/BVxxx/
    python pretranslate.py a.mp4 b.mp4 --model large-v3 --to zh
    python pretranslate.py 电影.mp4 --force            # 忽略缓存重跑
"""
from __future__ import annotations
import model_home  # noqa: E402,F401  # 先把模型目录钉到 D 盘（务必在 HF/faster-whisper 之前）

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

PRE_DIR = os.path.join(HERE, "pre_cache")
URL_DIR = os.path.join(PRE_DIR, "url_cache")

# ── 切 cue 的参数（实测取舍）────────────────────────────────
# 上限 7 秒 / 56 字符：再长观众读不完；再短译文碎（与 live 路线
# 的教训一致：碎片翻译质量远低于整句）。
MAX_CUE_DUR = 5.5
MAX_CUE_CHARS = 30

# 断行/切条：优先标点、空格边界，避免把中文词从中间劈开
from subtitle_layout import split_cue as _split_cue_layout
# 句末标点（在这些符号后允许断句）
TERM_PUNCT = tuple("。！？…!?")
# 次级断点（句内太长时在它们后面断）
SOFT_PUNCT = tuple("，、；：,;:")

# ── 分块流水（对齐回环的"分体式"：先出一部分就让观众看上）────
# 回环是 partial 先显示、stable 到了再转正；点播是"先译完一块就落盘，
# 后面的块继续译" —— 目的相同：**不要让观众等全部完成**。
#   首块给短一点（45s）：第一屏字幕几秒内就该出现
#   之后每块 120s：切块开销（模型已经常驻，主要是重组句）摊薄
#   切点尽量落在静音处，避免把一句话从中间劈开
CHUNK_SEC = 120.0
FIRST_CHUNK_SEC = 30.0       # 首屏更快：第一块短，字幕更早落盘（原 45s）
CUT_SEARCH = 5.0            # 在候选切点前后几秒内找静音
CUT_RMS_MAX = 0.02          # 低于这个能量才算"静音"，否则不挪切点


# ══════════════════════════════════════════════════════════
# 来源解析：本地文件 / 网页 URL
# ══════════════════════════════════════════════════════════

def _md5_head_tail(path, n=1 << 20):
    """头尾各 1MB 的 md5：大文件秒算，碰撞概率可忽略。"""
    h = hashlib.md5()
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        h.update(f.read(n))
        if size > n:
            f.seek(-n, os.SEEK_END)
            h.update(f.read(n))
    return h.hexdigest()[:16]


def _ffmpeg_dir():
    """找 ffmpeg 所在目录：PATH 优先，其次 imageio-ffmpeg 自带的静态包。"""
    exe = shutil.which("ffmpeg")
    if exe:
        return os.path.dirname(exe)
    try:
        import imageio_ffmpeg
        return os.path.dirname(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:
        return None


def resolve_source(src):
    """返回 (音频路径, 展示名, 缓存键, srt输出路径, 时长提示秒)。

    本地文件：直接用（PyAV 能解 mp4/mkv/ts 等容器，不需要 ffmpeg）。
    URL：yt-dlp 只拉音频流到 pre_cache/url_cache/（已存在就复用）。
    """
    if os.path.isfile(src):
        ap = os.path.abspath(src)
        key = "file:%s:%d:%d:%s" % (
            ap, os.path.getsize(ap), int(os.path.getmtime(ap)),
            _md5_head_tail(ap))
        stem = os.path.splitext(ap)[0]
        return ap, os.path.basename(ap), key, None, 0.0

    if not re.match(r"^https?://", src or ""):
        raise SystemExit(f"不是文件也不是 URL：{src}")

    # B 站优先走自家公开 API：yt-dlp 的 B 站提取器经常被风控
    # （实测无 cookie 时连续返回 "No video formats found"），
    # 而 x/web-interface/view + x/player/playurl 带对 UA/Referer 就通。
    if "bilibili.com" in src or src.startswith("BV") or "b23.tv" in src:
        got = _fetch_bilibili(src)
        if got is not None:
            return got
        print("  B 站 API 失败，退回 yt-dlp 试试")

    os.makedirs(URL_DIR, exist_ok=True)
    import yt_dlp
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True}
    with yt_dlp.YoutubeDL(opts) as y:
        info = y.extract_info(src, download=False)
    title = info.get("title") or "video"
    vid = info.get("id") or hashlib.md5(src.encode()).hexdigest()[:12]
    dur = float(info.get("duration") or 0)
    if info.get("is_live"):
        # 直播是无限长的：整片预翻译会永远下不完，也不是这个模块该干的事。
        # 直接给出可执行的下一步，别让用户对着"预翻译失败"猜。
        print("  ！这是直播流：整片预翻译不适用。"
              "请切到「直播翻译」模式（实时抓系统播放的声音）。", flush=True)
        raise SystemExit(2)
    # 已下载过就直接用（按 id 找，不管扩展名）
    for ext in ("m4a", "webm", "opus", "mp3", "aac", "mp4"):
        p = os.path.join(URL_DIR, f"{vid}.{ext}")
        if os.path.isfile(p) and os.path.getsize(p) > 0:
            print(f"  音频已缓存：{os.path.basename(p)}")
            return (p, title, "url:" + src,
                    os.path.join(PRE_DIR, _safe_name(title)), dur)
    print(f"  下载音频：{title}（{dur/60:.0f} 分钟）")
    out_tmpl = os.path.join(URL_DIR, f"{vid}.%(ext)s")
    dl_opts = {"quiet": True, "no_warnings": True, "noplaylist": True,
               "format": "bestaudio/best", "outtmpl": out_tmpl,
               # HLS 优先用 yt-dlp 自带下载器：它跟随 Python/系统代理；
               # ffmpeg 是独立进程，不会自动走系统代理，直连 googlevideo
               # 在代理环境会报 "Error number -138"。ffmpeg_location 仍保留，
               # 供需要转封装/合并的格式使用。
               "hls_prefer_native": True}
    # YouTube 等站的 HLS（m3u8）下载需要 ffmpeg；PyAV 只负责本地解码。
    # imageio-ffmpeg 自带静态 ffmpeg，装了就自动用，不用用户配 PATH。
    ff_dir = _ffmpeg_dir()
    if ff_dir:
        dl_opts["ffmpeg_location"] = ff_dir
    with yt_dlp.YoutubeDL(dl_opts) as y:
        y.download([src])
    for ext in ("m4a", "webm", "opus", "mp3", "aac", "mp4"):
        p = os.path.join(URL_DIR, f"{vid}.{ext}")
        if os.path.isfile(p) and os.path.getsize(p) > 0:
            return (p, title, "url:" + src,
                    os.path.join(PRE_DIR, _safe_name(title)), dur)
    raise SystemExit("下载完成但找不到音频文件（formats 可能全需要 ffmpeg 转封装）")


def _fetch_bilibili(src):
    """B 站公开 API 拉音频。成功返回 resolve_source 的五元组，失败返回 None。

    为什么不用 yt-dlp：实测它的 B 站提取器被风控（无 cookie 时连续
    "No video formats found"），而公开 API 只要带对 UA + Referer。
    只需要音频，fnval=16 要 DASH 列表，挑码率最高的一条音轨即可。

    分 P（2026-10-18 实战修）：多 P 视频的 view API 顶层 cid/duration
    永远是 **P1** 的。用户看 P2（如 KPL 总决赛"第二局"）时，反查到
    的 URL 带不带 ?p=2 都好，旧代码下下来的都是 P1 的音频 —— 字幕
    文件名对、内容全错，观感就是"视频和字幕根本不在一个时间轴上"。
    现在解析 URL 的 p 参数，用 pages[p-1] 的 cid/时长/分P标题；
    音频缓存键也从 {bvid}.m4a 变为 {bvid}_p{N}.m4a（P1 保持旧名，
    不作废已有缓存）。
    """
    m = re.search(r"(BV[0-9A-Za-z]+)", src or "")
    if not m:
        return None
    bvid = m.group(1)
    pnum = 1
    mp = re.search(r"[?&]p=(\d+)", src or "")
    if mp:
        pnum = max(1, int(mp.group(1)))
    hdr = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                         "AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
           "Referer": "https://www.bilibili.com"}
    import urllib.request as _ur

    def _get(u):
        r = _ur.Request(u, headers=hdr)
        return json.loads(_ur.urlopen(r, timeout=15).read().decode())

    try:
        v = _get(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}")
        d = v.get("data") or {}
        cid, title = d.get("cid"), d.get("title") or bvid
        dur = float(d.get("duration") or 0)
        if not cid:
            return None
        pages = d.get("pages") or []
        if pnum > 1 or len(pages) > 1:
            pg = pages[pnum - 1] if 1 <= pnum <= len(pages) else None
            if pnum > 1:
                if pg is None:
                    print(f"  B 站：BV {bvid} 没有 P{pnum}"
                          f"（共 {len(pages)} P），按 P1 处理")
                    pg = pages[0] if pages else None
                    pnum = 1
            if pg:
                cid = pg.get("cid") or cid
                dur = float(pg.get("duration") or 0) or dur
                part = (pg.get("part") or "").strip()
                # P>1 用分P标题命名字幕（SMTC 播分P时上报的就是它）；
                # P1 用 BV 主标题（保持与历史缓存/匹配规则兼容）
                if pnum > 1:
                    title = part or f"{d.get('title') or bvid} P{pnum}"
        cache_key = bvid if pnum == 1 else f"{bvid}_p{pnum}"
        out = os.path.join(URL_DIR, f"{cache_key}.m4a")
        if not (os.path.isfile(out) and os.path.getsize(out) > 0):
            p = _get("https://api.bilibili.com/x/player/playurl"
                     f"?bvid={bvid}&cid={cid}&fnval=16&fnver=0&fourk=0")
            auds = ((p.get("data") or {}).get("dash") or {}).get("audio") or []
            if not auds:
                return None
            best = max(auds, key=lambda a: a.get("bandwidth") or 0)
            au = best.get("baseUrl") or best.get("base_url")
            if not au:
                return None
            os.makedirs(URL_DIR, exist_ok=True)
            print(f"  下载音频：{title}（{dur/60:.1f} 分钟，B 站 API"
                  f"{'，P' + str(pnum) if pnum > 1 else ''}）")
            r = _ur.Request(au, headers=hdr)
            with _ur.urlopen(r, timeout=30) as resp, open(out, "wb") as f:
                while True:
                    chunk = resp.read(1 << 18)
                    if not chunk:
                        break
                    f.write(chunk)
        else:
            print(f"  音频已缓存：{cache_key}.m4a")
        if not os.path.getsize(out):
            return None
        return (out, title, "url:" + src,
                os.path.join(PRE_DIR, _safe_name(title)), dur)
    except Exception as e:
        print(f"  B 站 API 异常: {type(e).__name__} {str(e)[:80]}")
        return None


def _safe_name(s, n=60):
    s = re.sub(r'[\\/:*?"<>|\r\n]+', " ", (s or "").strip())
    s = re.sub(r"\s+", " ", s).strip()
    return (s[:n].rstrip() or "video")


def _media_duration(path):
    try:
        import av
        with av.open(path) as c:
            if c.duration:
                return c.duration / 1e6
    except Exception:
        pass
    return 0.0


# ══════════════════════════════════════════════════════════
# 识别
# ══════════════════════════════════════════════════════════

def _decode_audio_16k(path):
    """用 PyAV 把任意容器解成 16k 单声道 float32。

    为什么不直接把文件路径交给 faster-whisper：它内部调
    av.open(..., metadata_errors="ignore")，而本机 PyAV 19 的 open()
    **不收这个参数**（实测 TypeError）。faster-whisper 与 PyAV 版本
    错位时这条路就断；自己解码喂 numpy 则完全不依赖那层封装。
    """
    import av
    import numpy as np
    from av.audio.resampler import AudioResampler
    resampler = AudioResampler(format="s16", layout="mono", rate=16000)
    chunks = []
    with av.open(path) as c:
        stream = c.streams.audio[0]
        for frame in c.decode(stream):
            for r in resampler.resample(frame):
                chunks.append(r.to_ndarray().reshape(-1))
    for r in resampler.resample(None):          # flush
        chunks.append(r.to_ndarray().reshape(-1))
    if not chunks:
        raise SystemExit("音频解码失败：没有解出任何样本")
    a = np.concatenate(chunks)
    return (a.astype(np.float32) / 32768.0)


def _hf_model_cached(name):
    """HF 缓存里有没有这个 faster-whisper 模型（有就跳过联网元数据请求）。"""
    if not name or os.path.isdir(str(name)):
        return False
    key = str(name).lower().replace("_", "-")
    hub = model_home.hf_hub_dir()
    try:
        for d in os.listdir(hub):
            dl = d.lower()
            if "faster-whisper" in dl and key in dl:
                return True
    except OSError:
        pass
    return False


def load_asr(model_name):
    """加载识别模型（一次，之后所有块共用）。

    为什么拆出来：分块流水要在一次运行里转写很多块，模型只该加载一次。

    qwen3-asr* / local:qwen3-asr* 走 qwen_asr_backend（faster-whisper
    兼容包装：VAD 切句 → 逐段转写 → 复读闸，POC 结论的上线形态）。
    """
    if str(model_name).lower().replace("_", "-").startswith(
            ("qwen3-asr", "local:qwen3-asr")):
        from qwen_asr_backend import load_qwen3_asr
        return load_qwen3_asr(model_name)
    from faster_whisper import WhisperModel
    device, ctype = "cpu", "int8"
    try:
        from gpu_setup import gpu_available, enable_cuda_dlls
        if gpu_available():
            enable_cuda_dlls()
            device, ctype = "cuda", "float16"
    except Exception:
        pass
    print(f"  识别模型 {model_name}（{device}/{ctype}）")
    t0 = time.time()
    # 缓存命中时 local_files_only=True：跳过 huggingface 元数据联网
    m = WhisperModel(model_name, device=device, compute_type=ctype,
                     local_files_only=_hf_model_cached(model_name))
    print(f"  模型加载 {time.time()-t0:.1f}s")
    return m


def transcribe_chunk(model, audio, lang=None, prompt=None, tag=""):
    """转写一块音频（numpy float32 / 16k）。返回 (segments, info)。

    与实时路线的差别全在这里：
      · vad_filter=True  长静音不喂模型（幻觉源头）
      · word_timestamps=True  切 cue 用，不靠猜
      · condition_on_previous_text=True  离线可以用前文，连贯性更好
        （实时路线不敢开：幻觉会自我强化；离线有 VAD+整段容错，划算）
      · prompt：上一块末尾的原文。分块会让跨块的上下文断掉，用它把
        语气和用词接上（faster-whisper 的 initial_prompt）。
    """
    t0 = time.time()
    segs, info = model.transcribe(
        audio, language=lang, beam_size=5,
        vad_filter=True, word_timestamps=True,
        # VAD 放松：threshold 0.5→0.35 + 句尾 200ms 垫片 —— 轻读/句尾
        # 低音量不再被当静音切掉（切丢的半句没有译文，内容就对不上）；
        # min_silence 500ms：别把正常换气当句间静音。
        vad_parameters=dict(threshold=0.35, min_silence_duration_ms=500,
                            speech_pad_ms=200),
        # 静音段幻觉抑制：BGM/纯静音里复读上一句是离线长音频的常见病，
        # 需要 word_timestamps（已开）才能生效。
        hallucination_silence_threshold=2.0,
        condition_on_previous_text=True,
        initial_prompt=(prompt or None))
    out = []
    dur = len(audio) / 16000.0
    last_pct = -1
    for s in segs:                       # 生成器：边识别边出
        out.append(s)
        if dur:
            pct = int(min(1.0, s.end / dur) * 100)
            if pct != last_pct:
                last_pct = pct
                el = time.time() - t0
                eta = el / max(1e-6, s.end) * max(0.0, dur - s.end)
                print(f"\r  识别{tag} {pct:3d}%　已用 {el:.0f}s　"
                      f"约剩 {eta:.0f}s", end="", flush=True)
    if dur:
        print()
    return out, info


# ── 分块：切点尽量落在静音上 ────────────────────────────────

def find_cut(audio, target, sr=16000, span=CUT_SEARCH, win=0.25):
    """在 target 秒附近找一个"最安静"的点当切点。

    为什么必须在静音处切：每一块的组句是独立的，切在词中间会把一句话
    劈成两半（后半块接不上前半块）。静音处切等于顺着自然的停顿切。
    找不到足够安静的位置（连续说话）就退回原切点，build_cues 还能兜住。
    """
    import numpy as np
    target = float(target)
    lo = max(0, int((target - span) * sr))
    hi = min(len(audio), int((target + span) * sr))
    w = int(win * sr)
    if hi - lo < 2 * w:
        return target
    seg = audio[lo:hi]
    n = (len(seg) - w) // w + 1
    if n < 2:
        return target
    rms = np.array([float(np.sqrt(np.mean(seg[i * w:(i + 1) * w] ** 2)))
                    for i in range(n)])
    k = int(np.argmin(rms))
    if rms[k] > CUT_RMS_MAX:
        return target            # 没有像样的静音，保持原切点
    return (lo + k * w + w / 2.0) / sr


def plan_cuts(audio, total, start_sec=0.0):
    """给出 [(a, b)] 块区间（秒）。首块短、其后按 CHUNK_SEC，切点贴静音。"""
    cuts = []
    a = float(start_sec)
    first = True
    while a < total - 0.8:
        step = FIRST_CHUNK_SEC if first else CHUNK_SEC
        b = min(float(total), a + step)
        if b < total - 0.8:
            b = max(a + 5.0, find_cut(audio, b))
        cuts.append((a, b))
        a = b
        first = False
    return cuts


# ══════════════════════════════════════════════════════════
# 切 cue：词级时间戳 → 完整句 → 合并/限长
# ══════════════════════════════════════════════════════════

class Cue:
    __slots__ = ("start", "end", "text")

    def __init__(self, start, end, text):
        self.start, self.end, self.text = float(start), float(end), text


def _is_term_word(word_text):
    """这个词是否句末。中文标点直接判；英文句点要求"字母+."，且
    前一字符是小写字母 —— "game." 判句末，"U.S."/"3.5" 不判。"""
    t = (word_text or "").rstrip()
    return t.endswith(TERM_PUNCT) or bool(
        t.endswith(".") and len(t) >= 2 and t[-2].islower())


def _seg_sentences(seg):
    """把一条 whisper 段按词级时间戳切成句子（标点断句）。"""
    words = [w for w in (getattr(seg, "words", None) or [])]
    if not words:
        t = (seg.text or "").strip()
        return [(seg.start, seg.end, t)] if t else []
    sents, buf = [], []
    for w in words:
        buf.append(w)
        if _is_term_word(w.word):
            sents.append(buf)
            buf = []
    if buf:
        sents.append(buf)
    out = []
    for ws in sents:
        # faster-whisper 的 word 自带前导空格（拉丁系），直接拼接再规整
        txt = re.sub(r"\s+", " ", "".join(w.word for w in ws)).strip()
        # 拉丁系原文在句末标点后补空格（"?Mày" → "? Mày"），观感一致；
        # CJK 源不动（中文句间不加空格是规范）
        if txt and not re.search(r"[぀-ヿ一-鿿]", txt):
            txt = re.sub(r"(?<=[。！？…!?])(?=\S)", " ", txt)
        if txt:
            out.append((ws[0].start, ws[-1].end, txt))
    return out


def _split_long(start, end, text, max_chars=None, max_dur=None):
    """单句过长：交给 subtitle_layout 按标点/空格/字符边界切，避免断字。

    max_chars/max_dur：显示上限。默认用 subtitle_layout 的紧上限（24
    字/5.5s，预切）；shape 层传放宽上限（42 字/8s），只拆真正超长的
    —— 语义合并（_try_merge）凑出的整句不回拆，交给 MT 整句翻，碎片
    译文质量远低于整句。None 表示该维度不限。
    """
    mc = max_chars if max_chars else 10 ** 9
    md = max_dur if max_dur else 10 ** 9
    parts = _split_cue_layout(start, end, "", text, max_chars=mc, max_dur=md)
    return [(a, b, src) for a, b, _zh, src in parts if src]


def _mk_pseudo_seg(words, mark=""):
    """把一组 word 包成 segment 形状（start/end/text/words），给 build_cues 用。

    mark：在最后一个 word 上补标点。批解码常丢标点，而 _seg_sentences
    是从 word 重建文本的，所以必须补在 word 上才能被后续断句看到。
    """
    from types import SimpleNamespace
    ws = [SimpleNamespace(word=getattr(w, "word", ""),
                          start=float(w.start), end=float(w.end))
          for w in words]
    if not ws:
        return None
    txt = re.sub(r"\s+", " ", "".join(w.word for w in ws)).strip()
    if not txt:
        return None
    if mark and txt and not txt.rstrip().endswith(tuple("。！？!?.;；,，")):
        ws[-1].word = ws[-1].word.rstrip() + mark
        txt = txt.rstrip() + mark
    return SimpleNamespace(start=ws[0].start, end=ws[-1].end,
                           text=txt, words=ws)


def split_batched_segments(segs, gap=0.45, max_seg=6.0, min_run=0.9):
    """批推理的粗 segment → 按词间静音 gap 重切（无标点也能切出句子感）。

    参数按中文解说语速（5-7 字/秒）实测：停顿 0.45s 就值得考虑切
    （0.75s 的旧值会十几秒不切、最后被 _split_long 硬劈）；句末标点
    （。）阈值 0.9s。切得碎没关系 —— _try_merge 会按语义把半句
    优先接回去，"细切 + 语义合并"比"粗切 + 硬拆"自然得多。
    """
    out = []
    for seg in segs:
        words = list(getattr(seg, "words", None) or [])
        if not words:
            out.append(seg)
            continue
        cur = []
        for w in words:
            if not cur:
                cur = [w]
                continue
            gap_t = float(w.start) - float(cur[-1].end)
            dur = float(cur[-1].end) - float(cur[0].start)
            if (gap_t >= gap and dur >= min_run) or dur >= max_seg:
                # 停顿 ≥0.9s 视为句末，否则按句中断开补逗号
                mark = "。" if gap_t >= 0.9 else "，"
                ps = _mk_pseudo_seg(cur, mark=mark)
                if ps is not None:
                    out.append(ps)
                cur = [w]
            else:
                cur.append(w)
        ps = _mk_pseudo_seg(cur)
        if ps is not None:
            out.append(ps)
    return out


def shape_batched_cues(cues):
    """把 build_cues 之后仍然过长的 cue 按放宽上限再切。

    关键：只拆真正超长（>42 字 / >8s）的，其余原样保留 —— build_cues
    里 _try_merge 已经按语义把半句接成整句，这里若再用 24/5.5 的紧
    上限回切，整句会被重新切碎，碎片译文质量远低于整句（live 路线的
    教训）。"细切 + 语义合并"才是对的顺序。
    """
    out = []
    for c in cues:
        dur = c.end - c.start
        n = len(re.sub(r"\s+", "", c.text or ""))
        if dur <= 8.0 and n <= 42:
            out.append(c)
            continue
        for st, en, txt in _split_long(c.start, c.end, c.text,
                                       max_chars=42, max_dur=8.0):
            out.append(Cue(st, en, txt))
    _min_duration_fixup(out)
    return out


def fit_cues_cps(cues, max_cps=20.0):
    """CPS 超标的 cue：用与下一条之间的静音间隙延长显示时间（不改文本）。

    注意：**切开字幕不会降低 CPS**（字符/总时长不变，只是摊到两条上）。
    真正能降 CPS 的只有 ① 延长显示（借用句间静音）② 压缩译文；
    这里做 ①，压缩留给人审/下一阶段。仍超标就保持原样，交给
    bench/eval_asr.py 的 CPS 指标暴露。
    """
    if max_cps is None or max_cps <= 0 or not cues:
        return cues
    for i, c in enumerate(cues):
        dur = max(0.01, c.end - c.start)
        n = len(re.sub(r"\s+", "", c.text or ""))
        if n <= 0 or n / dur <= max_cps:
            continue
        need = n / max_cps                  # 达标所需的显示时长
        limit = (cues[i + 1].start - 0.02) if i + 1 < len(cues) else c.end
        new_end = min(limit, c.start + need)
        if new_end > c.end:
            c.end = new_end
    return cues


def build_cues(segments):
    """句 → cue：合并到过读得完的上限，拆开过长的。"""
    sents = []
    for seg in segments:
        sents.extend(_seg_sentences(seg))
    cues = []
    cur = None
    for st, en, txt in sents:
        for piece in _split_long(st, en, txt):
            pst, pen, ptxt = piece
            merged = _try_merge(cur, pst, pen, ptxt)
            if merged is not None:
                cur = merged
            else:
                if cur is not None:
                    cues.append(cur)
                cur = Cue(pst, pen, ptxt)
    if cur is not None:
        cues.append(cur)
    _min_duration_fixup(cues)
    return cues


def _min_duration_fixup(cues):
    """显示时长兜底：太短的字幕一闪而过，至少留 1.2s（不压下一条）。

    幂等：只延长 end、且不超过下一条的 start，重复调用安全。
    流水线（iter_chunk_cues）边产边译，每次落盘前也对全局 cues
    过一遍 —— 中间写出的 srt 同样不会有一闪而过的短条。
    """
    for i, c in enumerate(cues):
        nxt = cues[i + 1].start if i + 1 < len(cues) else c.end + 2.0
        if c.end - c.start < 1.2:
            c.end = min(c.start + 1.2, nxt)


def _ends_term(text):
    return (text or "").rstrip().endswith(TERM_PUNCT)


def _try_merge(cur, pst, pen, ptxt):
    """build_cues / iter_chunk_cues 共用的"并入当前 cue"规则。

    语义感知（断句自然感的关键）：
    · 当前 cue 已是完整句（。！？…!? 结尾）→ **不再向后黏**。
      完整短句独立显示本来就是字幕规范（"好。""来了。"）。
    · 当前 cue 是悬空半句（逗号结尾 / 无标点截断）→ 放宽上限
      （dur×1.45 / chars×1.4）优先把语义半句接上 —— 否则观众会看到
      "我们看这波团战，" 这种挂着半条就换屏的破句。
    """
    if cur is None:
        return None
    term = _ends_term(cur.text)
    if term:
        return None
    max_dur = MAX_CUE_DUR * 1.6
    max_chars = MAX_CUE_CHARS * 1.4
    if pen - cur.start > max_dur or len(cur.text) + len(ptxt) > max_chars:
        return None
    cur.end = pen
    # 句间是否补空格：CJK 规范是不加（"。"后直接接下一句），
    # 拉丁系必须加（"?Mày" → "? Mày"），否则词黏在一起
    need_sp = bool(cur.text and ptxt) and not (
        re.search(r"[぀-ヿ一-鿿]$", cur.text)
        or re.match(r"[぀-ヿ一-鿿]", ptxt))
    cur.text = cur.text + (" " if need_sp else "") + ptxt
    return cur


def transcribe_batched(pipe, audio, lang=None, prompt=None, tag="",
                       batch_size=8):
    """整块批推理：返回 (segments, info)。比流式快 ~2.9×（120s 实测），
    代价是 segment 更粗（见 bench/_bench_batched_asr.py），所以只在
    --asr-batch > 0 且块够长时用；首块仍走流式保首屏。"""
    t0 = time.time()
    segs, info = pipe.transcribe(
        audio, batch_size=batch_size, beam_size=5,
        vad_filter=True, word_timestamps=True,
        condition_on_previous_text=True,
        initial_prompt=(prompt or None))
    out = list(segs)
    print(f"  识别{tag} 批推理 {len(out)} 段 / {len(audio)/16000:.0f}s，"
          f"用时 {time.time()-t0:.1f}s（batch={batch_size}）")
    return out, info


def iter_chunk_cues(model, audio, lang=None, prompt=None, tag="",
                    info_box=None):
    """**边识别边产出**已封尾的 cue（块内相对时间）—— 生成器。

    与 transcribe_chunk + build_cues 的差别：不再等整块识别完才组句。
    whisper 的 segments 本身就是生成器，每吐一段立刻组句；一句封尾
    （并不进 cur 时）立刻 yield。调用方凑够一批就翻译落盘 ——
    第一屏字幕从"整块识别完 + 整块翻译完"提前到"头几句译完"。

    切 cue 规则与 build_cues 完全一致（_seg_sentences → _split_long
    → _try_merge），只是产出时机不同；1.2s 显示时长兜底由调用方在
    落盘前统一做（_min_duration_fixup，幂等）。

    info_box：传入 dict 则在首次迭代时写入 language /
    language_probability（faster-whisper 在开始迭代前完成语言检测）。
    """
    t0 = time.time()
    segs, info = model.transcribe(
        audio, language=lang, beam_size=5,
        vad_filter=True, word_timestamps=True,
        vad_parameters=dict(threshold=0.35, min_silence_duration_ms=500,
                            speech_pad_ms=200),
        hallucination_silence_threshold=2.0,
        condition_on_previous_text=True,
        initial_prompt=(prompt or None))
    if info_box is not None:
        info_box["language"] = getattr(info, "language", None)
        info_box["language_probability"] = getattr(
            info, "language_probability", 0.0)
    dur = len(audio) / 16000.0
    last_pct = -1
    cur = None
    for seg in segs:
        if dur:
            pct = int(min(1.0, seg.end / dur) * 100)
            if pct != last_pct:
                last_pct = pct
                el = time.time() - t0
                eta = el / max(1e-6, seg.end) * max(0.0, dur - seg.end)
                print(f"\r  识别{tag} {pct:3d}%　已用 {el:.0f}s　"
                      f"约剩 {eta:.0f}s", end="", flush=True)
        for st, en, txt in _seg_sentences(seg):
            for pst, pen, ptxt in _split_long(st, en, txt):
                merged = _try_merge(cur, pst, pen, ptxt)
                if merged is not None:
                    cur = merged
                else:
                    if cur is not None:
                        yield cur
                    cur = Cue(pst, pen, ptxt)
    if cur is not None:
        yield cur
    if dur:
        print()


# ── 跨块断句缝合 ────────────────────────────────────────────
# 切点即使贴了静音，连续说话（CUT_SEARCH 内找不到安静点）时仍会退回
# 硬切 —— 一句话被劈在两块里，两半各自翻译，内容必然对不上。
# 后一块译前做一次"缝合"：上块末 cue 不是完整句（无终止标点）、与
# 本块首 cue 几乎贴在一起、合并不超长 → 合成一条**整句**重译。

def _should_stitch(prev, nxt):
    """prev（上块末 cue）与 nxt（本块首 cue）是否该缝合成一句。

    语义感知：prev 是悬空半句（无句末标点）→ 放宽间隙与上限优先缝
    （解说语流块间停 0.5~0.6s 很常见，0.4 的旧值会漏缝）；prev 已是
    完整句 → 不缝。与 _try_merge 的规则保持一致。
    """
    if prev is None or nxt is None:
        return False
    max_gap = 0.4 if _ends_term(prev.text) else 0.7
    if nxt.start - prev.end > max_gap:      # 间隙大 = 真两句
        return False
    if nxt.start < prev.start - 0.1:        # 时间倒挂，异常，不碰
        return False
    if _ends_term(prev.text):
        return False                        # 上块末已是完整句
    dur = nxt.end - prev.start
    chars = len(prev.text) + len(nxt.text)
    return dur <= MAX_CUE_DUR * 1.6 and chars <= MAX_CUE_CHARS * 1.4


def stitch_boundary(cues, part, done):
    """就地把 part[0] 与 cues[-1] 缝合（条件不满足则什么都不做）。

    done 的键是 cue 的全局下标：缝合后 cues[-1] 的位置将被合并 cue
    复用，旧译文（半句的译文）必须作废 —— 翻译循环会把它当未译重译。
    返回 True 表示发生了缝合。
    """
    if not cues or not part or not _should_stitch(cues[-1], part[0]):
        return False
    tail = cues.pop()
    done.pop(len(cues), None)               # len(cues) 即 tail 原全局下标
    head = part[0]
    need_sp = not (re.search(r"[぀-ヿ一-鿿]$", tail.text)
                   or re.match(r"[぀-ヿ一-鿿]", head.text))
    part[0] = Cue(tail.start, head.end,
                  tail.text + (" " if need_sp else "") + head.text)
    return True


# ══════════════════════════════════════════════════════════
# 翻译（复用 live 路线的 Translator / translate_batch）
# ══════════════════════════════════════════════════════════

def _mt_model_from_registry():
    """视频模式该用哪个本地 gguf：优先取注册表 cache.mt 的本地项。

    为什么需要：界面上选 `local:hy-mt2-1.8b` 后，`modes.cache.mt` 就是这个
    id；但如果不把它翻译成 gguf 路径，`local_mt.from_args()` 会按候选表
    取**第一个**存在的文件（hh-translate-base）—— 现象是"选了没生效"。
    """
    try:
        import model_registry as R
        mid = R.resolve("cache").get("mt") or ""
        if mid.startswith("local:") or mid.startswith("offline:"):
            from local_mt import resolve_model
            return resolve_model(mid)
    except Exception:
        pass
    return None


def make_translator(dst, src_hint=None, mt_local=False, terms=None,
                    mt_model=None):
    import live_subtitle_v2 as M
    if mt_local and not M.LOCAL_MT:
        from local_mt import from_args as _local_from_args
        _m = mt_model or _mt_model_from_registry()
        lm = _local_from_args(model=_m, terms=terms or [])
        if lm is not None:
            M.LOCAL_MT.append(lm)
    tr = M.Translator("auto", dst)
    # 术语表同样接给远端 Qwen-MT（P0-1）：本地 HY-MT 在 from_args 里已吃，
    # 这里补远端 LLM 的 translation_options.terms。
    if terms and getattr(tr, "llm", None) is not None:
        try:
            tr.llm.set_terms(terms)
        except Exception as e:
            print(f"  术语表挂到远端 MT 失败：{type(e).__name__} {e}")
    if src_hint:
        tr.src_hint = src_hint
    print(f"  翻译后端：{tr.mode}　源语言提示 {src_hint or '无'}")
    return tr


def translate_cues(tr, cues, dst, batch_size=6, index_base=0, done=None,
                   on_batch=None, tag="", head_cues=None):
    """整句批量翻译。done 是**全局索引 → 译文** 的字典（跨块复用）。

    index_base ：本批 cue 在全局里的起始下标。译文按全局索引存放，
                 所以重跑时已译过的部分自动跳过（断点续跑）。
    on_batch   ：每译完一批回调一次。分块流水用它**立刻落盘** ——
                 这正是"先翻译一部分就显示"的关键：不等整片译完。
    head_cues  ：本切片**之前**的全局 cue（上下文来源）。调用方传的
                 是切片（cues[base:]），没有它第一批永远拿不到跨块上下文。
    """
    from llm_translate import translate_batch
    done = done if done is not None else {}
    n = len(cues)
    t0 = time.time()
    for i in range(0, n, batch_size):
        grp = cues[i:i + batch_size]
        todo = [(j, c) for j, c in enumerate(grp, i)
                if not done.get(index_base + j)]
        if todo:
            # 上下文：本批之前最近的 2 条**已译定**的原文。要跨切片取——
            # head_cues（全局前段）+ 本切片已走部分，done 是全局索引。
            all_prev = (head_cues or []) + cues[:i]
            base0 = index_base - len(head_cues or [])   # all_prev[0] 的全局索引
            ctx = []
            for j in range(len(all_prev) - 1, -1, -1):
                if done.get(base0 + j):
                    ctx.append(all_prev[j].text)
                    if len(ctx) >= 2:
                        break
            ctx.reverse()
            zh_list = translate_batch(
                tr, [c.text for _j, c in todo],
                target="Simplified Chinese" if dst.startswith("zh") else dst,
                max_items=max(2, batch_size) + len(ctx), context=ctx)
            for (j, c), zh in zip(todo, zh_list):
                done[index_base + j] = (zh or "").strip()
            if on_batch:
                on_batch()
        got = min(i + batch_size, n)
        el = time.time() - t0
        eta = el / max(1, got) * max(0, n - got)
        pct = (got * 100 // n) if n else 100
        print(f"\r  翻译{tag} {got}/{n}（{pct}%）　"
              f"已用 {el:.0f}s　约剩 {eta:.0f}s", end="", flush=True)
    print()
    fails = sum(1 for j in range(n) if not done.get(index_base + j))
    if fails:
        print(f"  ！{fails} 条未译出（srt 里保留原文行，不缺字幕）")
    return done


# ══════════════════════════════════════════════════════════
# 输出
# ══════════════════════════════════════════════════════════

def _ts(t):
    t = max(0.0, t)
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = int(t % 60)
    ms = int(round((t - int(t)) * 1000))
    if ms >= 1000:
        s, ms = s + 1, 0
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(path, cues, done):
    """**原子**写双语 srt。

    为什么必须原子：分块流水会反复重写这个文件，而浮窗正在每 2 秒读它
    （热重载）。直接覆盖写会有一瞬间文件是半截的，读端可能解析到残片。
    先写 .tmp 再 os.replace —— 替换是原子的，读端要么看到旧版、要么看到
    新版，永远不会是半截。
    """
    tmp = path + ".tmp"
    # 写盘前也切短：外部播放器/浮窗读到的就是 2~5.5s、每行约 24 字的字幕，
    # 而不是 8~16s、50 字一条（播放器硬折行会把中文从词中间劈开）。
    rows = []
    for j, c in enumerate(cues):
        zh = (done.get(j) or "").strip()
        for st, en, z, src in _split_cue_layout(c.start, c.end, zh, c.text):
            if z or src:
                rows.append((st, en, z.strip(), src.strip()))
    with open(tmp, "w", encoding="utf-8-sig") as f:   # BOM：PotPlayer 友好
        for k, (st, en, z, src) in enumerate(rows):
            f.write(f"{k + 1}\n{_ts(st)} --> {_ts(en)}\n")
            if z:
                f.write(z + "\n")
            f.write(src + "\n\n")
    try:
        os.replace(tmp, path)
    except OSError:
        # 极少数情况（读端占着句柄）替换失败：退回直接写，别丢内容
        try:
            data = open(tmp, encoding="utf-8-sig").read()
            with open(path, "w", encoding="utf-8-sig") as f:
                f.write(data)
            os.remove(tmp)
        except OSError:
            pass


_TS_RE = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)")


def _ts_sec(m):
    return (int(m.group(1)) * 3600 + int(m.group(2)) * 60
            + int(m.group(3)) + int(m.group(4)) / 1000.0)


def read_dual_srt(path):
    """读回已经写出的双语 srt → (cues, done)。

    用途：① 断点续跑 —— 上次中断时 srt 里已有前几块，这次接着往后做，
    不重头再来；② 文件不存在/为空 → ([], {})，等价于从头开始。
    约定格式：每条 = 序号 / 时间行 / 译文行 / 原文行（无译文时只有原文行）。
    """
    try:
        txt = open(path, encoding="utf-8-sig", errors="replace").read()
    except Exception:
        return [], {}
    cues, done = [], {}
    for block in re.split(r"\r?\n\s*\r?\n", txt):
        lines = [ln.strip() for ln in block.strip().splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        tm = [_ts_sec(m) for m in _TS_RE.finditer(lines[1])]
        body = lines[2:]
        if len(tm) < 2:                       # 序号行可能被省掉
            tm = [_ts_sec(m) for m in _TS_RE.finditer(lines[0])]
            body = lines[1:]
        if len(tm) < 2 or not body:
            continue
        if len(body) == 1:
            zh, src = "", body[0]
        else:
            zh, src = body[0], body[1]
        i = len(cues)
        cues.append(Cue(tm[0], tm[1], src))
        if zh:
            done[i] = zh
    return cues, done


def _meta_path(srt_path):
    return srt_path + ".meta.json"


def _load_meta(srt_path):
    try:
        return json.load(open(_meta_path(srt_path), encoding="utf-8"))
    except Exception:
        return {}


def _save_meta(srt_path, meta):
    json.dump(meta, open(_meta_path(srt_path), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)


# ══════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════

def process(src, args):
    print("=" * 64)
    print(f"来源: {src}")
    audio_path, name, key, out_stem, dur_hint = resolve_source(src)
    if out_stem is None:
        out_stem = os.path.splitext(os.path.abspath(audio_path))[0] \
            if os.path.isfile(src) else os.path.join(PRE_DIR, _safe_name(name))
    os.makedirs(os.path.dirname(out_stem) or ".", exist_ok=True)
    srt_path = out_stem + f".{args.to}.srt"
    print(f"片名: {name}")
    print(f"输出: {srt_path}")

    # ── 缓存：键 + 模型 + 目标语言一致，**且上次是跑完整的** → 直接复用 ──
    # 加 complete 判断：分块流水会边跑边写 srt，中途退出也会留下一个
    # "看起来存在"的 srt。那个不能当缓存命中，否则永远只看到前半段。
    meta = _load_meta(srt_path)
    want = {"key": key, "model": args.model, "dst": args.to}
    if not args.force and os.path.isfile(srt_path) \
            and all(meta.get(k) == v for k, v in want.items()) \
            and meta.get("complete"):
        print("  缓存命中（同一来源 + 同一模型 + 同一目标语言），直接复用。")
        print(f"  想重跑加 --force")
        # 缓存命中也要补排版：老缓存里可能根本没有 .ass，
        # 或者之前用的是别的样式 —— 不补的话"改了样式没生效"。
        _style = getattr(args, "typeset", "ass") or "none"
        if _style != "none":
            try:
                import subtitle_typeset as _T
                if _T.available():
                    _o = _T.srt_to_ass(srt_path,
                                       os.path.splitext(srt_path)[0] + ".ass",
                                       style=_style)
                    if _o:
                        print(f"  排版：{os.path.basename(_o)}")
            except Exception as _e:
                print(f"  排版跳过（{type(_e).__name__}: {_e}）")
        return srt_path

    # ── 解码（一次；分块只是切这段数组，不重复解容器）──
    total = _media_duration(audio_path)
    if total:
        print(f"  音频时长 {total/60:.1f} 分钟")
    print("  解码音频（PyAV → 16k 单声道）…")
    audio = _decode_audio_16k(audio_path)
    sr = 16000
    if not total:
        total = len(audio) / sr

    # ── 断点：srt 里已有的（可能是上次中断留下的）接着用，不重头来 ──
    cues, done = [], {}
    if not args.force and os.path.isfile(srt_path):
        cues, done = read_dual_srt(srt_path)
        if cues:
            print(f"  断点：已有 {len(cues)} 条字幕（覆盖到 "
                  f"{cues[-1].end/60:.1f} 分钟），从那里继续")
    start_sec = cues[-1].end if cues else 0.0

    model = load_asr(args.model)
    _asr_pipe = None
    _asr_batch = int(getattr(args, "asr_batch", 0) or 0)
    if _asr_batch > 0 and not getattr(model, "is_qwen3_backend", False):
        # BatchedInferencePipeline 是 ctranslate2 专属 —— qwen 后端
        # 自带按 VAD 段逐段调用，批推理这条加速路对它没有意义。
        try:
            from faster_whisper import BatchedInferencePipeline
            _asr_pipe = BatchedInferencePipeline(model=model)
            print(f"  批推理开启（batch={_asr_batch}；首块仍走流式）")
        except Exception as e:
            print(f"  批推理不可用（{type(e).__name__}: {e}），退回顺序识别")
            _asr_pipe = None
    tr = None
    # 跨块上下文：用上一块末尾的原文当 initial_prompt，把语气接上
    prompt = cues[-1].text[-120:] if cues else None
    cuts = plan_cuts(audio, total, start_sec)
    print(f"  分块流水：{len(cuts)} 块（首块 {FIRST_CHUNK_SEC:.0f}s、"
          f"其后 {CHUNK_SEC:.0f}s）—— **块内边识别边翻译**，")
    print("            每凑够一批就立刻落盘，观看端可边看边等")
    t0 = time.time()

    _ts_stat = {"calls": 0, "ms": 0.0, "path": ""}

    def _typeset(_p):
        """第三方排版（pysubs2 → .ass）。纯 CPU，跑在翻译落盘之后。

        为什么放在 _flush 里：srt 是边译边长，ASS 也得跟着长，否则
        播放器看到的样式字幕永远只有开头一段。每次重排 <1ms/条。
        """
        style = getattr(args, "typeset", "ass") or "none"
        if style == "none":
            return
        try:
            import subtitle_typeset as _T
            if not _T.available():
                return
            t1 = time.time()
            out = _T.srt_to_ass(_p, os.path.splitext(_p)[0] + ".ass",
                                style=style)
            if out:
                _ts_stat["calls"] += 1
                _ts_stat["ms"] += (time.time() - t1) * 1000
                _ts_stat["path"] = out
        except Exception as _e:
            if _ts_stat["calls"] == 0:
                print(f"  排版跳过（{type(_e).__name__}: {_e}）")

    def _flush(_p=srt_path, _c=cues, _d=done):
        # 每译完一批就重写 srt —— 这就是"先翻译一部分就显示"
        _min_duration_fixup(_c)          # 幂等，中间态也没有一闪而过的短条
        # CPS 超标：用与下一条之间的静音间隙延长显示（幂等；不改文本/索引）
        try:
            fit_cues_cps(_c, float(getattr(args, "cps_max", 20.0) or 0))
        except Exception:
            pass
        write_srt(_p, _c, _d)
        _typeset(_p)

    try:
      for k, (a, b) in enumerate(cuts, 1):
        tag = f"({k}/{len(cuts)})"
        t_blk = time.time()
        t_tr_total = 0.0
        buf = []                                # 本块待译 cue 缓冲
        info_box = {}
        got_flushed = False                     # 本块是否已译过第一批

        def _flush_buf():
            """把 buf 里的 cue 并入全局、缝合边界、翻译并落盘。"""
            nonlocal t_tr_total
            if not buf:
                return
            if stitch_boundary(cues, buf, done):
                print("  缝合跨块断句：上块末尾的半句并入本块重译")
            base = len(cues)
            cues.extend(buf)
            buf.clear()
            if tr is None:
                return                          # 语言还没检出（理论不到）
            t_tr0 = time.time()
            translate_cues(tr, cues[base:], args.to,
                           batch_size=args.mt_batch,
                           index_base=base, done=done,
                           on_batch=_flush, tag=tag,
                           head_cues=cues[:base])
            t_tr_total += time.time() - t_tr0
            _flush()

        # 块内流水：识别一句产一句，凑够一批（mt_batch 条）就翻译落盘 ——
        # 首屏从"整块识别完+整块翻译完"提前到"头一批译完"。
        got_any = False
        _chunk_audio = audio[int(a * sr):int(b * sr)]
        _batched_ok = False
        if _asr_pipe is not None and (b - a) >= 60:
            try:
                _segs, _info = transcribe_batched(
                    _asr_pipe, _chunk_audio, lang=(args.lang or None),
                    prompt=prompt, tag=tag, batch_size=_asr_batch)
                info_box["language"] = getattr(_info, "language", None)
                info_box["language_probability"] = getattr(
                    _info, "language_probability", 0.0)
                _segs2 = split_batched_segments(_segs)
                _cue_iter = iter(shape_batched_cues(build_cues(_segs2)))
                _batched_ok = True
            except Exception as _e:
                print(f"  ！批推理失败（{type(_e).__name__}: {_e}），"
                      f"本块回退流式识别")
        if not _batched_ok:
            _cue_iter = iter_chunk_cues(
                model, _chunk_audio,
                lang=(args.lang or None), prompt=prompt,
                tag=tag, info_box=info_box)
        for c in _cue_iter:
            got_any = True
            c.start += a                       # 块内相对时间 → 全局时间
            c.end += a
            buf.append(c)
            if tr is None and info_box.get("language"):
                print(f"  语言 {info_box['language']}"
                      f"（置信 {info_box.get('language_probability', 0):.2f}）")
                tr = make_translator(args.to, src_hint=info_box["language"],
                                     mt_local=args.mt_local,
                                     terms=_load_terms(args),
                                     mt_model=getattr(args, "mt_local_model",
                                                      None))
            # 本块第一批 4 条就翻（首屏再压几秒），之后按 mt_batch 走
            batch_gate = 4 if not got_flushed else args.mt_batch
            if tr is not None and len(buf) >= batch_gate:
                _flush_buf()
                got_flushed = True
        t_rec = time.time() - t_blk - t_tr_total
        if tr is None and info_box.get("language"):
            # 整块静音也会导致语言检测完成 —— translator 后续块还要用
            print(f"  语言 {info_box['language']}"
                  f"（置信 {info_box.get('language_probability', 0):.2f}）")
            tr = make_translator(args.to, src_hint=info_box["language"],
                                 mt_local=args.mt_local,
                                 terms=_load_terms(args),
                                 mt_model=getattr(args, "mt_local_model",
                                                  None))
        if not got_any and not buf:
            print(f"  第 {k} 块没有识别到语音")
            continue
        _flush_buf()                            # 块尾不足一批的剩余
        if cues:
            prompt = cues[-1].text[-120:]
        # 逐块报耗时：以后"怎么突然变慢了"一看就知道卡在识别还是翻译
        print(f"  已出字幕到 {b/60:.1f} 分钟（{len(cues)} 条）"
              f"　本块 {time.time()-t_blk:.0f}s"
              f"（识别 {t_rec:.0f}s + 翻译 {t_tr_total:.0f}s）")
        # 长片跑到后半段显存碎片化会 OOM —— 每 8 块主动清一次缓存，
        # 有 torch 才做，没有（纯 CPU 环境）静默跳过
        if k % 8 == 0:
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass
    except (Exception, KeyboardInterrupt) as e:
        # 崩溃现场保护：进度其实都在 srt 里（每批落盘），这里补一条
        # progress 元信息，让下次启动一眼看到"上次跑到哪、为什么断"。
        _save_meta(srt_path, {**want,
                              "src_hint": (tr.src_hint if tr else None),
                              "cues": len(cues),
                              "translated": sum(1 for v in done.values() if v),
                              "complete": False,
                              "covered_sec": (cues[-1].end if cues else 0.0),
                              "crashed": type(e).__name__,
                              "time": time.strftime("%Y-%m-%d %H:%M:%S")})
        if isinstance(e, KeyboardInterrupt):
            print(f"\n  已中断：进度保留到 "
                  f"{(cues[-1].end/60 if cues else 0):.1f} 分钟，"
                  f"重跑同一命令自动续上")
            return srt_path if cues else None
        print(f"  ！第 {k} 块附近出错（{type(e).__name__}: {e}）——"
              f"进度已保留，重跑同一命令自动续上")
        raise

    if not cues:
        print("  ！没有识别出任何语音，srt 不生成")
        return None

    zh_n = sum(1 for v in done.values() if v)
    _save_meta(srt_path, {**want,
                          "src_hint": (tr.src_hint if tr else None),
                          "cues": len(cues),
                          "translated": zh_n,
                          "complete": True,
                          "time": time.strftime("%Y-%m-%d %H:%M:%S")})
    print(f"  完成：{srt_path}")
    print(f"  字幕 {len(cues)} 条，译文 {zh_n} 条，"
          f"总用时 {time.time()-t0:.0f}s")
    if _ts_stat["path"]:
        # 排版开销单独报：这是"加排版会不会拖慢翻译"的现场答案
        print(f"  排版：{os.path.basename(_ts_stat['path'])}"
              f"（重排 {_ts_stat['calls']} 次，累计 "
              f"{_ts_stat['ms']:.0f}ms，均摊 "
              f"{_ts_stat['ms']/max(1, _ts_stat['calls']):.1f}ms/次）")
    print(f"  播放器加载它即可（mpv/PotPlayer 对同名 srt 会自动加载）")
    return srt_path


def _load_terms(args):
    terms = []
    for it in (args.terms or []):
        if os.path.isfile(it):
            for ln in open(it, encoding="utf-8"):
                ln = ln.strip()
                if ln and not ln.startswith("#") and "=" in ln:
                    a, b = ln.split("=", 1)
                    terms.append((a.strip(), b.strip()))
        elif "=" in it:
            a, b = it.split("=", 1)
            terms.append((a.strip(), b.strip()))
    if terms:
        print(f"  术语表 {len(terms)} 条")
    return terms


def build_parser():
    ap = argparse.ArgumentParser(
        description="点播视频预翻译：全文识别 + 整句翻译 → 双语 srt")
    ap.add_argument("sources", nargs="+", help="视频文件路径或网页 URL")
    ap.add_argument("--to", dest="to", default="zh", help="目标语言（默认 zh）")
    ap.add_argument("--lang", default=None,
                    help="固定源语言码（默认自动检测，整部检测很准）")
    ap.add_argument("--model", default="large-v3",
                    help="识别模型（默认 large-v3：离线不要省这点时间）")
    ap.add_argument("--mt-batch", type=int, default=8,
                    help="一次调用合并翻译的条数（默认 8；失败自动退回逐条）")
    ap.add_argument("--mt-local", action="store_true",
                    help="用本地 HY-MT 翻译（完全离线）")
    ap.add_argument("--mt-local-model", default=None,
                    help="本地译器 gguf：路径，或注册表 id（local:hy-mt2-1.8b、"
                         "local:translategemma-4b…）。不给则取注册表 cache.mt")
    ap.add_argument("--cps-max", type=float, default=20.0,
                    help="字幕 CPS 上限（字符/秒；0=关）。超标的 cue 用相邻"
                         "静音间隙延长显示（切句不降 CPS，压缩译文留后续）")
    ap.add_argument("--asr-batch", type=int, default=8,
                    help="faster-whisper 批推理 batch（0=关；默认 8，"
                         "长块提速约 2.9×；短块/首块仍走流式；失败自动回退）")
    ap.add_argument("--terms", action="append", default=[],
                    help="术语：'源词=译法' 或术语表文件（仅 --mt-local 生效）")
    ap.add_argument("--force", action="store_true", help="忽略缓存重跑")
    ap.add_argument("--typeset", default="ass",
                    choices=["ass", "minimal", "reading", "none"],
                    help="第三方排版（pysubs2 → .ass）：ass=双语（默认）、"
                         "minimal=单语极简、reading=大字号底框、none=不排")
    ap.add_argument("--no-registry", action="store_true",
                    help="不用 model_registry.json 里的默认模型，"
                         "回到本文件里写死的默认值")
    ap.add_argument("--print-models", action="store_true",
                    help="打印注册表给本模式解析出的模型后退出")
    _apply_registry(ap, "cache")
    return ap


def _apply_registry(ap, mode):
    """把注册表里**缓存模式**的默认模型/参数灌进 argparse。

    与 live_subtitle_v2._apply_registry 同一套做法（两个模块必须一致，
    否则两边口径会再次漂移）。三层保护：
      ① import 失败 → 静默跳过，走原硬编码默认（默认值完全没变）
      ② `--no-registry` → 完全跳过
      ③ 只灌**该模式确实有**的参数名（model_registry.MODE_ARGS），
         多一个都不塞 —— 静默忽略是"设了没生效"的来源

    缓存模式与实时模式的关键差别在这一层就体现出来：
    实时要低延迟（--mt-batch 2、max_batch 2.0），
    缓存要高准确（--model large-v3、--mt-batch 8）。
    """
    if "--no-registry" in sys.argv[1:]:
        return
    try:
        import model_registry as R
        kw = R.cli_args(mode)
        known = {a.dest for a in ap._actions}
        kw = {k: v for k, v in kw.items() if k in known}
        if kw:
            ap.set_defaults(**kw)
    except Exception:
        pass


def main():
    args = build_parser().parse_args()
    if args.print_models:
        # 排障入口：证明注册表真的生效了
        import model_registry as R
        r = R.resolve("cache")
        print(f"模式 {r['label']}（来源 asr={r['asr_source']} mt={r['mt_source']}）")
        print(f"  识别 --model  = {args.model}")
        print(f"  合并 --mt-batch = {args.mt_batch}")
        for line in R.registry_pending_defaults():
            print("  · 仍是硬编码:", line)
        return 0
    os.makedirs(PRE_DIR, exist_ok=True)
    os.makedirs(URL_DIR, exist_ok=True)
    outs = []
    for src in args.sources:
        try:
            p = process(src, args)
            if p:
                outs.append(p)
        except SystemExit:
            raise
        except Exception as e:
            print(f"  ！{src} 处理失败: {type(e).__name__} {e}")
    print("=" * 64)
    print(f"全部完成：{len(outs)}/{len(args.sources)}")
    for p in outs:
        print(f"  {p}")


if __name__ == "__main__":
    main()
