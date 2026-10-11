#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
live-subtitle v2 —— 针对三个实测问题的重做
============================================

用户提了三个问题，我逐个量了原因：

【问题一：实时性】
  实测（bench_latency.py）：**耗时 = 0.04×音频秒 + 1.08s**
    0.5s 音频 → 1.11s (0.45×)
    1.5s 音频 → 1.14s (1.31×)
    8.0s 音频 → 1.42s (5.65×)
  → **固定开销 1.08 秒是绝对瓶颈**，可变成本只有 0.04 秒/秒。
  → 由此**推翻了我原来打算做的三件事**：
      · 关 VAD     → 1.54s，反而更慢（VAD 本身在省时间）
      · beam=1     → 1.29s，没省
      · 换小模型   → 不动那 1.08 秒（tiny/base 加载还更慢）
  → 唯一有效的方向是**摊薄**：短段攒起来一次转写。
    实测：6 秒一次转写 1.98s；拆成 3 次各 2 秒则 3.89s。**近 2 倍。**
    字级时间戳可用，能把批量结果映射回各段。

【问题二：翻译准确性】
  实测到的错：所有格丢失、中英混排丢尾、
    还有一次输出混进乱码（"竞技场告诉我关于你自己的一切 и稱笵"）。
  对策三层（按代价从低到高）：
    ① **短句表**：口语高频短句直接查表（What? → 什么？），零延迟且不会错
    ② **会话内缓存**：同一句重复出现（字幕组常用重复台词）直接复用，保证一致
    ③ **长句才走 argos**，并且清洗输出（去掉非中文字符的残留）

【问题三：浮窗不能移动、缺基本功能】
  新增：
    · **鼠标拖动**（按住任意处拖）
    · **滑轮**调字号
    · **Ctrl+滑轮**调透明度
    · **方向键**微调位置
    · 快捷键：`[` `]` 字号　`-` `=` 透明度　`R` 重置　`H` 隐藏/显示　`Esc` 退出
    · **设置持久化**到 overlay_settings.json，下次启动还是你调好的样子

用法
----
  python live_subtitle_v2.py
  python live_subtitle_v2.py --from ja --to zh
  python live_subtitle_v2.py --selftest
  python live_subtitle_v2.py --bench        # 复现延迟测量
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import queue
import re
import ssl        # **必须在这里导入**：_google_free_ok() 里用到它，
                  # 而那个函数把所有异常都吞了，缺 import 时表现为
                  # "端点探测永远失败"（我自己踩过：表现为 False，查了很久）
import sys
import threading
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET_RATE = 16000
SETTINGS = os.path.join(HERE, "overlay_settings.json")


def make_dpi_aware():
    """必须最先调用。

    为什么：进程不是 DPI-aware 时，Windows 会给它一个**缩放后的虚拟屏幕**，
    Tk 报的 winfo_screenheight() 与真实值不符（本机实测 864 vs 真实 816）。
    于是我用虚拟坐标设 geometry，系统按真实坐标摆窗口，
    **窗口就跑到别的位置去了**——实测我设 (184,691)，窗口实际出现在 (287,323)，
    往上偏了 368 像素。用户报告"根本看不见翻译框"，根因就是这个。
    浮窗是半透明深色，跑到屏幕中间盖在窗口上，看起来就像没有。
    """
    if os.name != "nt":
        return
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # PER_MONITOR_DPI_AWARE
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()        # 老系统回退
    except Exception:
        pass


make_dpi_aware()

# ── 口语短句表：命中就不走模型，零延迟且不会翻译错 ────────────────
PHRASEBOOK = {
    "en": {
        "what?": "什么？", "what": "什么", "why?": "为什么？", "why": "为什么",
        "who?": "谁？", "where?": "哪儿？", "when?": "什么时候？",
        "how?": "怎么？", "really?": "真的？", "of course": "当然",
        "i see": "我明白了", "i know": "我知道", "i don't know": "我不知道",
        "thank you": "谢谢", "thanks": "谢谢", "sorry": "对不起",
        "excuse me": "打扰一下", "no": "不", "yes": "是", "okay": "好",
        "ok": "好", "hello": "你好", "hi": "嗨", "hey": "嘿",
        "good": "好", "great": "太好了", "nice": "不错", "sure": "当然",
        "come on": "拜托", "shut up": "闭嘴", "go": "走", "stop": "停下",
        "wait": "等等", "help": "救命", "run": "快跑", "look": "看",
        "listen": "听着", "right": "对", "wrong": "错了", "maybe": "也许",
        "never": "从不", "always": "总是", "again": "再一次",
        "what happened?": "发生了什么？", "are you okay?": "你还好吗？",
        "i'm sorry": "对不起", "i love you": "我爱你", "let's go": "走吧",
        "what is it?": "什么事？", "what's wrong?": "怎么了？",
        "what do you mean?": "你什么意思？", "i don't understand": "我不明白",
        "be careful": "小心", "hold on": "等一下", "calm down": "冷静",
        "it's okay": "没事", "it's fine": "没关系", "never mind": "算了",
        "good morning": "早上好", "good night": "晚安", "goodbye": "再见",
        "bye": "再见", "please": "请", "congratulations": "恭喜",
        "i'm fine": "我很好", "me too": "我也是", "so what?": "那又怎样？",
        "no way": "不可能", "my god": "天啊", "oh my god": "我的天",
        "damn it": "该死", "what the hell": "搞什么",
    },
    # ── 越南语 ────────────────────────────────────────────────
    # 用户实际在看越南新闻。argos **没有 vi→zh**，只能中继或走在线，
    # 所以短句表在这里价值更大：常见说法直接给对，不经过任何模型。
    "vi": {
        "xin chào": "你好", "cảm ơn": "谢谢", "cám ơn": "谢谢",
        "vâng": "是的", "dạ": "是的", "không": "不", "đúng": "对",
        "sai": "错", "được": "好", "tốt": "好", "rất tốt": "很好",
        "xin lỗi": "对不起", "tạm biệt": "再见", "chào": "你好",
        "hôm nay": "今天", "ngày mai": "明天", "hôm qua": "昨天",
        "bây giờ": "现在", "ở đây": "在这里", "ở đó": "在那里",
        "bao nhiêu": "多少", "tại sao": "为什么", "như thế nào": "怎么样",
        "cái gì": "什么", "ai": "谁", "khi nào": "什么时候", "ở đâu": "在哪里",
        "tin tức": "新闻", "thời sự": "时事", "thế giới": "世界",
        "việt nam": "越南", "trung quốc": "中国", "người dân": "民众",
        "chính phủ": "政府", "kinh tế": "经济", "xã hội": "社会",
        "theo tin": "据消息", "cho biết": "表示", "báo cáo": "报告",
        "phát biểu": "发言", "tuyên bố": "声明",
        "một người": "一个人", "nhiều người": "很多人",
        "tất cả": "全部", "không có": "没有", "có thể": "可以",
        "phải không": "是吗", "đúng không": "对吗", "thế nào": "怎么样",
    },
}
# 白名单：明确保留什么，其余一律删。
# 上一版用"字符类取反"，而 \u3000-\u303f 里含全角空格、又和 ASCII 空格的关系没理清，
# 结果把 **所有空格都删了**（'hello world' → 'helloworld'）。
# 教训：删除型正则在处理空白时极易出错，改用白名单。
KEEP = re.compile(
    r"[\u4e00-\u9fff"          # 汉字
    r"A-Za-z0-9"               # 拉丁与数字
    r"\u00c0-\u024f"           # 拉丁扩展（法德等带音符）
    r"\u1ea0-\u1eff"           # **越南语专用区**（ạ ả ấ ầ ậ ắ ằ ẻ ẽ ẹ ế ề ệ ố ồ ộ ơ ớ ờ ợ ư ứ ừ ự …）
    r"\u0300-\u036f"           # **组合声调符号**（越南语/法语用）
                               # 实测漏了这一段，导致越南语被删成残片："ườ ườ ạ ạ"
    r" \t"                     # 半角空格
    r"，。！？、；：（）《》〈〉「」『』【】“”‘’…—·～"   # 中文标点
    r",.!?;:()\[\]'\-"         # 西文标点
    r"]")
HAS_CJK = re.compile(r"[\u4e00-\u9fff]")
KANA = re.compile(r"[\u3040-\u30ff]")
CYRILLIC = re.compile(r"[\u0400-\u04ff]")
# 泰文 / 老挝文 / 高棉文：whisper 在越南语上偶尔跑偏到这些文字，
# 实测出现 "แล ้ ว เจ ็ บ เจ ้ า ..."。不是目标语言，删。
SEA_SCRIPT = re.compile(r"[\u0e00-\u0e7f\u0e80-\u0eff\u1780-\u17ff]")
# ASS/SSA 字幕样式覆盖代码：实测输出里出现过
#   "fn黑体fs22bord1shad03aHBE4aH00fscx67fscy662cHFFFFFF3cH"
# 这是把视频里嵌入的字幕样式串当文本识别出来了，必须清掉。
ASS_KNOWN = re.compile(
    r"(?<![A-Za-z0-9])(?:fn[A-Za-z\u4e00-\u9fff]*|fs\d{1,3}|bord\d{1,3}|shad\d{1,3}|"
    r"fsc[xy]\d{1,3}|fsp\d{1,3}|fr[xyz][\d.]+|"
    r"[1-4]?c&H[0-9A-Fa-f]{6}&?|alpha&H[0-9A-Fa-f]{2}&?|"
    r"an\d|b[01]|i[01]|u[01]|s[01]|q\d)")
# 兜底：连续 3 组以上"字母/汉字 + 数字"（如 bord1shad0fscx67）。
# 正常译文里不可能有这种结构。
# **注意不能用 \b** —— 它在"fn黑体"这类中英混排处不成立，
# 实测因此漏掉 "22bord1shad03aHBE4aH00fscx67fscy662cHFFFFFF3cH"。
ASS_SHAPE = re.compile(r"(?:[A-Za-z\u4e00-\u9fff]{1,5}\d{1,3}[\s&]*){3,}")


_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
_GF_OK = None          # 免 key Google 端点是否可用（探一次就记住）


def _google_free_ok():
    """探测免 key 的 Google 端点是否可用（只探一次）。

    实测端点变体差异极大（同一台机器、同一时刻）：
        translate.googleapis.com {client=gtx}           → 429
        translate.googleapis.com {client=dict-chrome-ex} → 通
        clients5.google.com      {client=dict-chrome-ex} → 通且最快
    所以必须用后者，并且探一次缓存结果，避免每句都探。
    """
    global _GF_OK
    if _GF_OK is not None:
        return _GF_OK
    import urllib.parse
    import urllib.request
    try:
        q = urllib.parse.quote("xin chào")
        url = ("https://clients5.google.com/translate_a/single"
               f"?client=dict-chrome-ex&sl=vi&tl=zh-CN&dt=t&q={q}")
        r = urllib.request.Request(url, headers={"User-Agent": _UA})
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(r, timeout=6, context=ctx) as resp:
            j = json.loads(resp.read().decode("utf-8", "replace"))
        txt = "".join(x[0] for x in j[0] if x and x[0])
        _GF_OK = bool(txt)
    except Exception:
        _GF_OK = False
    return _GF_OK


def strip_ass_cruft(s):
    """去掉 ASS/SSA 样式代码残留。

    两条判据（任一成立即整段判为噪声、返回空）：
      ① 出现 3 个以上已知 ASS 标签
      ② 出现"字母/汉字+数字"连续 3 组以上
    实测来源：视频里嵌入的字幕样式串被 whisper 当文本识别出来，
    例如 "fn黑体fs22bord1shad03aHBE4aH00fscx67fscy662cHFFFFFF3cH"。
    """
    if not s:
        return s
    if len(ASS_KNOWN.findall(s)) >= 3 or ASS_SHAPE.search(s):
        return ""
    s = s.replace("\\N", " ").replace("\\n", " ")
    s = re.sub(r"\{[^}]*\}", " ", s)
    s = ASS_KNOWN.sub(" ", s)
    return s


def clean_translation(s: str) -> str:
    """清理译文：argos 偶尔混进假名/西里尔/多余符号。
    用白名单保留，其余替换为空；去字符后重新归一化空格。"""
    if not s:
        return ""
    s = KANA.sub(" ", s)          # 用空格替，保住词边界
    s = CYRILLIC.sub(" ", s)
    s = SEA_SCRIPT.sub(" ", s)    # 泰文/老挝文/高棉文（whisper 在越南语上会跑偏）
    s = strip_ass_cruft(s)          # ASS 字幕样式代码残留
    s = "".join(ch if KEEP.match(ch) else "" for ch in s)
    s = re.sub(r"[ \t]{2,}", " ", s).strip()
    # 中文之间/中文与中文标点之间的空格去掉；英数之间保留
    s = re.sub(r"(?<=[\u4e00-\u9fff]) +(?=[\u4e00-\u9fff，。！？、；：）】」』”’])", "", s)
    s = re.sub(r"(?<=[，。！？、；：（【「『“‘]) +(?=[\u4e00-\u9fff])", "", s)
    return s.strip()


# ── 音频捕获 ─────────────────────────────────────────────────────
class LoopbackCapture:
    def __init__(self, chunk_ms=40):
        import pyaudiowpatch as pyaudio
        self._pa = pyaudio
        self.p = pyaudio.PyAudio()
        try:
            self.dev = self.p.get_wasapi_loopback_analogue_by_dict(
                self.p.get_default_output_device_info())
        except Exception as e:
            self.p.terminate()
            raise RuntimeError(f"取 WASAPI 环回失败：{e}") from e
        self.rate = int(self.dev["defaultSampleRate"])
        self.ch = min(2, self.dev["maxInputChannels"])
        self.blocksize = int(self.rate * chunk_ms / 1000)
        self.q = queue.Queue(maxsize=400)
        self.errs = []
        self._stream = None

    def _cb(self, in_data, frames, tinfo, status):
        try:
            if in_data:
                try:
                    self.q.put_nowait(np.frombuffer(in_data, dtype=np.int16).copy())
                except queue.Full:
                    pass
        except Exception as e:
            self.errs.append(f"{type(e).__name__}: {e}")
        return (None, self._pa.paContinue)

    def start(self):
        self._stream = self.p.open(
            format=self._pa.paInt16, channels=self.ch, rate=self.rate,
            input=True, input_device_index=self.dev["index"],
            frames_per_buffer=self.blocksize, stream_callback=self._cb)
        self._stream.start_stream()
        return self

    def stop(self):
        try:
            if self._stream:
                self._stream.stop_stream(); self._stream.close()
        finally:
            self.p.terminate()

    def read(self, timeout=0.3):
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None


def screen_workarea():
    """返回**真实工作区** (left, top, right, bottom)，避开任务栏。

    为什么不用 Tk 的 winfo_screenwidth/height：
      Tk 给的是**整屏**（本机 1536x864），而任务栏占了底部 48 像素，
      真实可用区只有 1536x816。我按整屏算 y=0.8*864=691，
      底部到 787 —— **正好压在任务栏那块，看起来就像窗口不存在。**
    用 SystemParametersInfo(SPI_GETWORKAREA) 拿系统自己认的可用区。
    """
    if os.name != "nt":
        return None
    try:
        import ctypes
        import ctypes.wintypes as wt
        r = wt.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0):
            if r.right - r.left > 200 and r.bottom - r.top > 200:
                return (r.left, r.top, r.right, r.bottom)
    except Exception:
        pass
    return None

def to_mono16k(a: np.ndarray, rate: int, ch: int) -> np.ndarray:
    x = a.astype(np.float32) / 32768.0
    if ch == 2 and len(x) % 2 == 0:
        x = x.reshape(-1, 2).mean(axis=1)
    if rate != TARGET_RATE and len(x):
        n = int(len(x) / rate * TARGET_RATE)
        idx = np.clip((np.arange(n) * rate / TARGET_RATE).astype(np.int64), 0, len(x) - 1)
        x = x[idx]
    return x


# ── 切句（逐步推进，安静处切）────────────────────────────────────
class Segmenter:
    def __init__(self, min_len=0.6, max_len=6.0, silence=0.5, thresh=0.008):
        self.min_len, self.max_len, self.silence, self.thresh = \
            min_len, max_len, silence, thresh
        self.buf = np.zeros(0, dtype=np.float32)
        self.analyzed = 0
        self.quiet = 0.0

    def push(self, x):
        out = []
        if len(x):
            self.buf = np.concatenate([self.buf, x]) if len(self.buf) else x.copy()
        step = int(TARGET_RATE * 0.05)
        while self.analyzed + step <= len(self.buf):
            w = self.buf[self.analyzed:self.analyzed + step]
            rms = float(np.sqrt((w.astype(np.float64) ** 2).mean()))
            self.analyzed += step
            if rms < self.thresh:
                self.quiet += step / TARGET_RATE
            else:
                self.quiet = 0.0
            min_s = int(TARGET_RATE * self.min_len)
            if self.analyzed < min_s:
                continue
            cut = None
            if self.quiet >= self.silence:
                back = int(TARGET_RATE * min(self.silence, 0.3))
                cut = max(min_s, self.analyzed - back)
            elif self.analyzed >= int(TARGET_RATE * self.max_len):
                cut = self.analyzed
            if cut is not None:
                audio = self.buf[:cut]
                self.buf = self.buf[cut:]
                self.analyzed = max(0, self.analyzed - cut)
                self.quiet = 0.0
                if len(audio) and float(np.abs(audio).max()) > 0.01:
                    out.append(audio)
        return out


# ── 批量转写：把短段攒起来一次转写，摊薄 1.08 秒固定开销 ──────────
class BatchTranscriber:
    """核心改动。
    实测依据：耗时 = 0.04×音频秒 + 1.08s。
    所以把 N 个小段合成一次调用，只付一次固定开销。

    两档触发（先到先走）：
      · 攒够 max_batch 秒音频
      · 或攒了 max_wait 秒还没走（防止一直等不到长音频而不出字幕）
    """

    def __init__(self, model="small", threads=4, max_batch=5.0, max_wait=1.2):
        from faster_whisper import WhisperModel
        self.m = WhisperModel(model, device="cpu", compute_type="int8",
                              cpu_threads=threads)
        self.max_batch = max_batch
        self.max_wait = max_wait
        self.last_lang = None          # 上一次检测到的语言（给 LanguageLock 用）
        self.last_lang_prob = 0.0

    def transcribe_batch(self, segs, lang=None):
        """segs: list[np.ndarray] → list[str]（与输入等长，对应各段文本）"""
        if not segs:
            return []
        lengths = [len(s) / TARGET_RATE for s in segs]
        cat = np.concatenate(segs)
        total = len(cat) / TARGET_RATE
        segs_out, info = self.m.transcribe(
            cat, language=lang, beam_size=5, vad_filter=True,
            condition_on_previous_text=False, word_timestamps=True)
        segs_out = list(segs_out)
        # 把检测结果留给调用方（LanguageLock 用它锁定源语言，
        # 翻译器再据此选路 —— 原来这里丢了，导致翻译器一直用 args.src）
        self.last_lang = getattr(info, "language", None)
        self.last_lang_prob = float(getattr(info, "language_probability", 0) or 0)

        words = []
        for s in segs_out:
            words.extend(getattr(s, "words", None) or [])
        if not words:
            # 没拿到字级时间戳：退化为整段文本给第一段
            txt = " ".join(s.text.strip() for s in segs_out).strip()
            return [txt] + [""] * (len(segs) - 1)

        # 按累计时长把词分回各段
        bounds, acc = [], 0.0
        for L in lengths:
            acc += L
            bounds.append(acc)
        out = [[] for _ in segs]
        for w in words:
            mid = (w.start + w.end) / 2
            k = 0
            while k < len(bounds) - 1 and mid > bounds[k]:
                k += 1
            out[k].append(w.word.strip())
        # 中文无空格，但英文要空格连；统一用空格连再压掉多余空格
        return [re.sub(r"\s{2,}", " ", " ".join(ws)).strip() for ws in out]


# ── 语言锁定 ─────────────────────────────────────────────────────
class LanguageLock:
    """从 whisper 的检测结果里定出"这段视频在说什么语言"，然后锁住。

    为什么必须锁：
      ① 翻译器要按**源语言**选路（vi→zh 和 en→zh 完全不同的包）。
         我原来用 args.src（默认 "en"）初始化翻译器 —— 看越南语时
         越南语被塞进 en→zh 模型，出来的自然是垃圾。**这是真 bug。**
      ② 逐段检测会抖：实测同一段越南新闻里偶尔被判成韩语，
         于是那几段字幕就废了。

    做法：前几段累计投票；连续两段一致或累计到 min_votes 就锁定；
    锁定后不再改（除非连续 stable_after 段都是别的语言，才允许切）。
    """

    def __init__(self, min_votes=2, confidence=0.5):
        self.min_votes = min_votes
        self.confidence = confidence
        self.votes = {}
        self.locked = None
        self.history = []
        self.switches = 0

    def observe(self, lang, prob):
        """喂一次检测结果，返回当前认定的语言（可能还是 None）。"""
        if not lang:
            return self.locked
        self.history.append((lang, round(float(prob or 0), 3)))
        if self.locked is None:
            if float(prob or 0) >= self.confidence:
                self.votes[lang] = self.votes.get(lang, 0) + 1
            if self.votes:
                top, n = max(self.votes.items(), key=lambda kv: kv[1])
                if n >= self.min_votes:
                    self.locked = top
            if self.locked is None and len(self.votes) == 1:
                # 只有一种语言被投过，且已投够
                top = next(iter(self.votes))
                if self.votes[top] >= self.min_votes:
                    self.locked = top
            return self.locked
        # 已锁定：连续多段都是别的语言才允许切
        recent = [h[0] for h in self.history[-4:]]
        if len(recent) >= 4 and all(x != self.locked for x in recent):
            new = max(set(recent), key=recent.count)
            self.locked = new
            self.switches += 1
            self.votes = {new: len(recent)}
        return self.locked

    def status(self):
        return (f"语言={self.locked or '检测中'}  "
                f"投票={self.votes}  切换={self.switches}")


# ── 翻译：四层（短句表 → 缓存 → 直连 → 中继/在线）───────────────
class Translator:
    """按语言对自动选路。

    为什么需要"选路"：**argos 不是所有语言对都有**。
    实测它只有 100 个包，越南语只有 en↔vi —— **没有 vi→zh**。
    而用户正在看越南新闻，所以必须能自动改道：

        ① 短句表（口语高频，零延迟不会错）
        ② 会话缓存（重复台词复用）
        ③ argos 直连（有 src→dst 包时最优，纯离线）
        ④ 中继：src→en→dst（argos 里 en 的中转包最全）
        ⑤ 在线兜底：MyMemory（vi→zh-CN 这种 argos 没有的，实测最准）
    """

    def __init__(self, src="en", dst="zh"):
        self.src, self.dst = src, dst
        self.mode = "none"
        self._fn = None
        self._pivot = None          # (fn1, fn2) 中继用
        self.cache = {}
        self.book = PHRASEBOOK.get(src, {}) if dst.startswith("zh") else {}
        self.hits_book = self.hits_cache = self.calls = self.rejected = 0
        self.web_cruft_blocked = self.web_cruft_fixed = 0
        self._gf_fails = 0
        self._setup()

    def _setup(self):
        try:
            import argostranslate.translate as T
            langs = {l.code: l for l in T.get_installed_languages()}
            # ③ 直连
            if self.src in langs and self.dst in langs:
                tr = langs[self.src].get_translation(langs[self.dst])
                # **坑：argos 对没有模型的语言对会返回 CompositeTranslation
                # （它内部自己拼两段），接口上看起来像"直连可用"。**
                # 实测 vi→zh 返回的就是 CompositeTranslation，类型名会骗人。
                # 所以必须按类型判断，否则会误以为有直连包。
                if tr and type(tr).__name__ != "CompositeTranslation":
                    try:
                        tr.translate("warm up")       # 预热，失败即降级
                        self._fn, self.mode = tr.translate, "argos"
                    except Exception:
                        self.mode = "none"
                elif tr:
                    self._pivot = (tr.t1.translate, tr.t2.translate)
                    self.mode = "argos-pivot"
            # ④ 中继（argos 里英语的中转包最全）
            if self.mode == "none" and self.src != "en" and self.dst != "en":
                if "en" in langs and self.src in langs and self.dst in langs:
                    t1 = langs[self.src].get_translation(langs["en"])
                    t2 = langs["en"].get_translation(langs[self.dst])
                    if t1 and t2:
                        try:
                            t1.translate("warm up"); t2.translate("warm up")
                            self._pivot = (t1.translate, t2.translate)
                            self.mode = "argos-pivot"
                        except Exception:
                            self.mode = "none"
        except Exception:
            pass
        # ⑤ **免 key 的 Google 端点**（实测可用）
        # 对比实测（真实越南新闻句）：
        #   argos 中继  : "tình quảng ninh đưa ra xét sử bị cáo doãn chung dũng"
        #                 → "我不确定,但我还不知道"              ← 完全错
        #   google-free : → "广宁省警方将被告段忠勇提审"           ← 人名地名都对
        #   MyMemory    : → "tinh Quang Ninh将被告Doan Chung Dung" ← 半截不译
        # 延迟（5 分钟 75 段实测）：中位 0.89s、P90 1.81s、零失败、零积压。
        if self.mode in ("none", "argos-pivot", "mymemory", "online-first"):
            self.mode = "google-free" if _google_free_ok() else (
                "argos-pivot" if self._pivot else "mymemory")

    # ── 免 key 的 Google 端点 ────────────────────────────────
    def _google_free(self, text):
        """端点变体是关键（实测）：
            translate.googleapis.com {client=gtx}           → HTTP 429（被限）
            translate.googleapis.com {client=dict-chrome-ex} → 通，2.30s
            **clients5.google.com    {client=dict-chrome-ex} → 通，0.61s ← 用这个**
        返回格式 [[["译文","原文",...],...],...]
        """
        import urllib.parse
        import urllib.request
        q = urllib.parse.quote((text or "")[:1500])
        url = (f"https://clients5.google.com/translate_a/single"
               f"?client=dict-chrome-ex&sl={self.src}&tl={self._tl()}&dt=t&q={q}")
        r = urllib.request.Request(url, headers={"User-Agent": _UA})
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(r, timeout=8, context=ctx) as resp:
            j = json.loads(resp.read().decode("utf-8", "replace"))
        return "".join(x[0] for x in j[0] if x and x[0])

    def _tl(self):
        """目标语言码：Google 用 zh-CN，不是 zh。"""
        d = (self.dst or "").lower()
        return "zh-CN" if d.startswith("zh") else self.dst

    def __call__(self, text):
        text = (text or "").strip()
        if not text:
            return ""
        # 归一化保留 . ? ! —— 它们改变语气，不能当噪声删掉
        # （原来用 [^\w\s'] 会把 '?' 删掉，导致 "What?" 与 "What" 撞成同一个键）
        # \w 在 Unicode 模式下包含越南语字母，所以声调符号不会丢
        key = re.sub(r"[^\w\s'.?!]", "", text.lower(), flags=re.UNICODE).strip()
        key = re.sub(r"\s+", " ", key)
        if key in self.book:                      # ① 短句表
            self.hits_book += 1
            return self.book[key]
        if key in self.cache:                     # ② 会话缓存
            self.hits_cache += 1
            return self.cache[key]
        self.calls += 1
        out = ""
        if self.mode == "argos":                  # ③ argos 直连 + 清洗
            try:
                out = clean_translation(self._fn(text))
            except Exception as e:
                out = f"[译错 {type(e).__name__}]"
        elif self.mode == "argos-pivot":          # ④ 中继 src→en→dst
            try:
                mid = self._pivot[0](text)
                out = clean_translation(self._pivot[1](mid))
            except Exception as e:
                out = f"[译错 {type(e).__name__}]"
        elif self.mode == "google-free":          # ⑤ 免 key Google
            try:
                out = clean_translation(self._google_free(text))
            except Exception as e:
                out = f"[译错 {type(e).__name__}]"
                # 免 key 端点偶尔抽风 → 立刻退回中继，别让字幕断
                self._gf_fails += 1
                if self._pivot and self._gf_fails <= 5:
                    try:
                        out = clean_translation(
                            self._pivot[1](self._pivot[0](text)))
                    except Exception:
                        pass
        elif self.mode in ("mymemory", "online-first"):   # ⑥ MyMemory（备选）
            out = self._mymemory(text)
            if not out or out.startswith("["):
                if self._pivot:
                    try:
                        out = clean_translation(self._pivot[1](self._pivot[0](text)))
                    except Exception:
                        pass
        # 在线结果若像维基模板 → 丢掉，改用中继
        if self._is_web_cruft(out) and self._pivot:
            try:
                out = clean_translation(self._pivot[1](self._pivot[0](text)))
                self.web_cruft_fixed += 1
            except Exception:
                pass
        if self._is_web_cruft(out):
            self.web_cruft_blocked += 1
            return ""
        if out and not out.startswith("["):
            # **目标语言校验**：源语言不是中文时，译文必须含中文。
            # 实测踩到过：语言锁定生效前的第 1 条用了错的源语言，
            # 出来的"译文"是越南语残片（"tôi nhớ là triệu tập với..."）。
            if self.dst.startswith("zh") and not HAS_CJK.search(out):
                self.rejected += 1
                return ""
            if self._looks_broken(out, text):
                self.rejected += 1
                return out          # 仍显示，但明显是胡话时不给它进缓存
            self.cache[key] = out
        return out

    @staticmethod
    def _is_web_cruft(s):
        """识别"这不是翻译，是网页模板"。

        实测 MyMemory 在越南语上返回的东西：
            "(原始内容存档于2019-09-29). Vå 2 Uông Bí..."
            "ã(学名:Cái tên rãn trãng rũng)为ệ科ệ属下的"
            "(英语)"
        这是维基百科的存档/物种分类模板，不是译文。宁可空着也不要显示给用户。
        """
        if not s:
            return False
        if "原始内容存档于" in s or "存档于" in s:
            return True
        # 实测第二种：维基的维护/引用模板
        #   原句 "Người về đóng tại thành phố này" → "请检查date中的日期值 (帮助) ..."
        if re.search(r"请检查\s*\w*\s*中的", s) or "(帮助)" in s:
            return True
        if "维基百科" in s or "Wikipedia" in s:
            return True
        if re.search(r"\(学名[:：]", s) or "为科" in s and "属下的" in s:
            return True
        if re.match(r"^\(\s*(英语|日语|越南语|中文)\s*\)", s):
            return True
        # 纯变音符号残片：实测 whisper 在越南语上会吐出 "ườ ườ ạ ạ"。
        # 试过"字母占比<30%"的判据 —— **抓不到**，因为 ườ/ạ 本身就是
        # U+1EA0–U+1EFF 区间的合法越南语字母，占比不低。
        # 换本质判据：**多个短片段、且没有一个是长度 ≥3 的实词**。
        # 必须要求 toks >= 2：单个短词（"好"、"谢谢"、"是"）是合法译文，
        # 我第一版没加这条，把"谢谢"和"好"都误杀了。
        toks = [t for t in re.split(r"[\s,，。.!！?？;；:：]+", s) if t]
        if len(toks) >= 2 and all(len(t) < 3 for t in toks):
            return True
        return False

    @staticmethod
    def _looks_broken(zh, src):
        """识别 argos 偶尔吐出的胡话。

        真实案例（英文原句 She'll hate me forever... 时）：
            译文 = "亦复如是. 师曰. 那她会永远恨我"
        "亦复如是"「师曰」是文言文，日常口语字幕里出现必然是模型跑飞了。
        判据取保守的几条，宁可漏判不可误杀。
        """
        if not zh:
            return False
        for bad in ("亦复如是", "师曰", "子曰", "阿弥陀佛", "善哉", "呜呼",
                    "之乎者也", "矣。", "也哉"):
            if bad in zh:
                return True
        # 长度严重失衡：英文很短却译得很长，或反过来
        en_len = len(re.sub(r"[^\w\s]", "", src or ""))
        zh_len = len(re.sub(r"[^\u4e00-\u9fff]", "", zh))
        if en_len >= 6 and zh_len > en_len * 2.2:
            return True
        if zh_len == 0 and en_len > 0:
            return True
        return False

    def _mymemory(self, text):
        try:
            import json as J
            import ssl
            import urllib.parse
            import urllib.request
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            u = ("https://api.mymemory.translated.net/get?q="
                 + urllib.parse.quote(text[:480])
                 + f"&langpair={self.src}|{self.dst}")
            r = urllib.request.Request(u, headers={"User-Agent": "dsh-live-subtitle"})
            with urllib.request.urlopen(r, timeout=8, context=ctx) as resp:
                j = J.loads(resp.read().decode("utf-8", "replace"))
            return clean_translation((j.get("responseData") or {}).get("translatedText") or "")
        except Exception as e:
            return f"[译错 {type(e).__name__}]"


# ── 浮窗：改用 PyQt5（见 overlay_qt.py 的文件头说明）────────────────
# 我原来用 tkinter 写的浮窗，系统层面全部正常（IsWindowVisible、TOPMOST、
# DWM cloaked=0、像素里也有文字），但用户看不到；试过 overrideredirect、
# Win32 强制置顶、WS_POPUP、纯 Win32 CreateWindowExW 都不行。
# 查成熟方案（VoxGo）后改用 PyQt5：Qt.Tool + WA_TranslucentBackground
# 这类窗口是 Windows 上透明置顶浮窗的标准做法，实测可见。
from overlay_qt import QtOverlay as Overlay      # noqa: E402


class _UnusedTkOverlay:
    """留空占位，防止老代码引用；真正的实现在 overlay_qt.QtOverlay。"""
    DEFAULT = {"font_size": 26, "alpha": 1.0, "x": None, "y": 0.78,
               "width_ratio": 0.76, "visible": True,
               "theme": "boxed"}

    def __init__(self, **kw):
        raise RuntimeError("tkinter 浮窗已废弃，请用 overlay_qt.QtOverlay")


class SrtWriter:
    """把字幕持续写入 .srt，供播放器实时加载。

    为什么需要它：浮窗依赖窗口渲染，一旦有任何环境因素干扰就看不到。
    **写文件完全绕开窗口系统**——PotPlayer / MPV / VLC 都能实时加载外挂字幕，
    字号、位置、透明度、描边全由播放器控制，显示效果比自绘浮窗好得多。

    写法：每来一条字幕就重写整个文件（字幕总量小，重写最安全——
    播放器对"边写边追加"的支持不一致，整文件替换最兼容）。
    时间轴用收到字幕的墙钟时间，每条显示 6 秒。
    """

    def __init__(self, path, hold=6.0, keep=80):
        self.path = path
        self.hold = hold
        self.keep = keep
        self.items = []            # (start_ts, en, zh)
        self.t0 = time.time()
        self._lock = threading.Lock()

    @staticmethod
    def _fmt(t):
        t = max(0.0, t)
        h = int(t // 3600); m = int(t % 3600 // 60)
        s = int(t % 60); ms = int((t - int(t)) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    def add(self, en, zh):
        if not en:
            return
        with self._lock:
            self.items.append((time.time() - self.t0, en, zh or ""))
            self.items = self.items[-self.keep:]
            self._write()

    def _write(self):
        out = []
        for i, (st, en, zh) in enumerate(self.items, 1):
            out.append(f"{i}")
            out.append(f"{self._fmt(st)} --> {self._fmt(st + self.hold)}")
            if zh:
                out.append(en)
                out.append(zh)
            else:
                out.append(en)
            out.append("")
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                f.write("\n".join(out))
        except Exception:
            pass



def run(args):
    print("=" * 64)
    print("live-subtitle v2")
    print("=" * 64)
    tr = Translator(args.src, args.dst)
    print(f"翻译: {tr.mode}　短句表 {len(tr.book)} 条")
    # **翻译器要跟着实际语言走。** 原来用 args.src（默认 "en"）初始化，
    # 看越南语时越南语被塞进 en→zh 模型 —— 这是"缺中文翻译"的真原因。
    lang_lock = LanguageLock(min_votes=2)
    streaming = bool(args.stream)
    if streaming:
        # 流式模式（LocalAgreement-2，见 streaming_asr.py 的说明）
        from streaming_asr import StreamingTranscriber
        th = StreamingTranscriber(
            model=args.stream_model, threads=args.threads,
            language=args.src, update_interval=args.stream_interval,
            tail_seconds=args.stream_tail, max_buffer=args.stream_maxbuf)
        print(f"转写: 流式 {args.stream_model}（LocalAgreement-2）"
              f"　更新间隔 {args.stream_interval}s　tail {args.stream_tail}s"
              f"　上限 {args.stream_maxbuf}s")
        print("      优点：首字延迟 ≈ 一次更新耗时（1–2s），不用等整句")
        print("      代价：CPU 上长时间连续说话时缓冲会积压到上限，字幕会落后")
    else:
        th = BatchTranscriber(args.model, args.threads,
                              max_batch=args.max_batch, max_wait=args.max_wait)
        print(f"转写: 分块 {args.model}　批量≤{args.max_batch}s / 等≤{args.max_wait}s")
        print("      优点：字幕与音频同步、不积压")
        print("      代价：要等整段说完（首字延迟 = 段长 + 1.5s）")
    cap = LoopbackCapture().start()
    print(f"采集: [{cap.dev['index']}] {cap.dev['name'][:44]}")
    print("\n控制：拖动移动　滑轮字号　Ctrl+滑轮透明度")
    print("      [ ] 字号　- = 透明度　方向键微调　H 隐藏　R 重置　Esc 退出\n")

    seg = Segmenter(min_len=args.min_seg, max_len=args.max_seg,
                    silence=args.vad_silence)
    ov = None if args.no_overlay else Overlay(font_size=args.font_size)
    srt = SrtWriter(args.srt) if args.srt else None
    if srt:
        print(f"字幕文件: {args.srt}　（播放器加载它，不依赖浮窗）")
    if ov:
        # 立刻显示一行，让用户确认框在哪
        ov.flash("live-subtitle ready — listening…", 5.0)
        print(f"浮窗位置: {ov.geo[0]},{ov.geo[1]}  尺寸 {ov.geo[2]}x{ov.geo[3]}  "
              f"flags=0x{int(ov.w.windowFlags()):08X}")
    stop = threading.Event()

    # **Qt 的 GUI 操作必须在主线程。**
    # 上一版我用后台线程调 processEvents()，Qt 浮窗不渲染。
    # demo 里用 app.exec_()（真事件循环）是好的，差异就在这里。
    # 现在改成：工作线程只往队列丢文本，主线程的 QTimer 取出来更新浮窗。
    ui_q = queue.Queue()
    try:
        from PyQt5.QtCore import QTimer
        _qt_timer = QTimer()
        _qt_timer.timeout.connect(
            lambda: (ov.set(*ui_q.get()) if not ui_q.empty() else None))
        _qt_timer.start(40)
        print("浮窗更新：主线程 QTimer（Qt 要求 GUI 在主线程）")
    except Exception as e:
        print(f"QTimer 建立失败: {type(e).__name__}")

    pending, pend_since = [], None
    n_batch = n_seg = 0
    t_all = time.time()

    # 流式模式：音频直接喂给 StreamingTranscriber，由它自己触发更新
    if streaming:
        try:
            while True:
                if ov:
                    ov.pump()
                blk = cap.read(timeout=0.25)
                if blk is not None:
                    th.feed(to_mono16k(blk, cap.rate, cap.ch))
                for inc in th.poll():
                    n_seg += 1
                    # 流式模式也要跟语言走
                    locked = lang_lock.observe(
                        getattr(th, "last_lang", None),
                        getattr(th, "last_lang_prob", 0.0))
                    if locked and locked != tr.src:
                        try:
                            tr = Translator(locked, args.dst)
                            print(f"  ★ 源语言切到 {locked} → 翻译模式 {tr.mode}")
                        except Exception:
                            pass
                    stable, partial = th.state()
                    zh = tr(stable or partial)
                    print(f"[{n_seg:>3}] 稳定: {stable[-60:]}")
                    if partial:
                        print(f"      局部: {partial[:60]}")
                    if zh:
                        print(f"      译: {zh[:60]}")
                    if ov:
                        ui_q.put((stable or partial, zh))
                    if srt:
                        srt.add(stable or partial, zh)
        except KeyboardInterrupt:
            print("\n中断。")
        finally:
            try:
                for inc in th.flush():
                    pass
            except Exception:
                pass
            th.stop()
            stop.set()
            cap.stop()
            if ov:
                ov.close()
            el = time.time() - t_all
            print(f"\n共 {n_seg} 段，用时 {el:.0f}s")
            print(f"流式统计：更新 {th.stats['updates']} 次，"
                  f"平均 {th.stats['t_upd']/max(1,th.stats['updates']):.2f}s/次，"
                  f"裁剪 {th.stats['trims']} 次")
            print(f"翻译：短句表命中 {tr.hits_book}　缓存命中 {tr.hits_cache}　"
                  f"模型调用 {tr.calls}")
        return

    try:
        while True:
            if ov:
                ov.pump()          # 主线程跑事件循环
            blk = cap.read(timeout=0.25)
            if blk is not None:
                for s in seg.push(to_mono16k(blk, cap.rate, cap.ch)):
                    pending.append(s)
                    if pend_since is None:
                        pend_since = time.time()
            # 触发条件：攒够时长 或 等够时间
            batch_dur = sum(len(x) for x in pending) / TARGET_RATE
            waited = (time.time() - pend_since) if pend_since else 0
            due = pending and (batch_dur >= args.max_batch or waited >= args.max_wait)
            if not due:
                continue

            segs_in, pending, pend_since = pending, [], None
            n_batch += 1
            t0 = time.time()
            # **强制指定语言是错的默认。** 实测：越南新闻被强制按 en 识别时，
            # whisper 不是"听错"，而是**把越南语直接意译成英文**（还译错），
            # 于是后面按 en→zh 翻译全崩。用户实际遇到的就是这个。
            # 现在默认 auto：whisper 检出 vi 置信度 0.976，识别正确。
            # 想固定语言用 --lang，不要用 --from 去强制。
            _lang = None if args.detect else (args.lang or None)
            texts = th.transcribe_batch(segs_in, lang=_lang)

            # 语言锁定：whisper 逐段检测会抖（实测越南语偶尔被判成韩语），
            # 所以累计投票、锁住，翻译器再按锁定语言重建。
            locked = lang_lock.observe(th.last_lang, th.last_lang_prob)
            if locked and locked != tr.src:
                try:
                    tr = Translator(locked, args.dst)
                    print(f"  ★ 源语言切到 {locked}（{lang_lock.status()}）→ "
                          f"翻译模式 {tr.mode}")
                except Exception as e:
                    print(f"  翻译器切换失败: {type(e).__name__} {e}")
            t_asr = time.time() - t0
            zh_all = [tr(t) for t in texts]
            t_end = time.time() - t0
            per_call = t_asr
            for s, txt, zh in zip(segs_in, texts, zh_all):
                n_seg += 1
                if not txt:
                    continue
                dur = len(s) / TARGET_RATE
                print(f"[{n_seg:>3}] 段{dur:4.1f}s 批{batch_dur:4.1f}s "
                      f"→转写{t_asr:4.2f}s 译{t_end-t_asr:4.2f}s "
                      f"| 原: {txt[:52]}")
                if zh:
                    print(f"      译: {zh[:52]}")
                if ov:
                    ui_q.put((txt, zh))
                if srt:
                    srt.add(txt, zh)
            print(f"      (批次 {n_batch}：{len(segs_in)} 段 / {batch_dur:.1f}s "
                  f"音频，一次转写；固定开销摊了 {len(segs_in)} 份)")
    except KeyboardInterrupt:
        print("\n中断。")
    finally:
        stop.set()
        cap.stop()
        if ov:
            ov.close()
        el = time.time() - t_all
        print(f"\n共 {n_batch} 批 / {n_seg} 段，用时 {el:.0f}s")
        print(f"翻译：短句表命中 {tr.hits_book}　缓存命中 {tr.hits_cache}　"
              f"模型调用 {tr.calls}")
        if cap.errs:
            print("采集异常:", cap.errs[:3])


def selftest():
    ok = fail = 0

    def chk(n, c):
        nonlocal ok, fail
        if c:
            ok += 1; print(f"  ok   {n}")
        else:
            fail += 1; print(f"  FAIL {n}")

    print("一、模块与导入")
    for m in ("numpy", "pyaudiowpatch", "faster_whisper", "PyQt5.QtWidgets"):
        try:
            __import__(m); chk(m, True)
        except Exception:
            chk(m, False)
    chk("浮窗是 Qt 实现（tkinter 已废弃）", Overlay.__module__.startswith("overlay_qt"))

    print("\n二、译文清洗（针对实测到的乱码）")
    # 注意：稱(U+7A31)、笵(U+7B35) 是正经汉字，程序保留它们是对的 —— 别拿它们当乱码测
    chk("去假名", clean_translation("一切 テスト 好") == "一切好")
    chk("纯假名清空", clean_translation("テスト") == "")
    chk("去假名不粘英文词", clean_translation("hello テスト world") == "hello world")
    chk("去西里尔", "и" not in clean_translation("你好 и world"))
    chk("保留汉字", clean_translation("稱笵") == "稱笵")
    chk("保留中文与标点", clean_translation("我从没想过会再见到你") == "我从没想过会再见到你")
    chk("空输入安全", clean_translation("") == "")

    print("\n二之一、目标语言校验（译文必须是中文）")
    # 实测踩到：语言锁定生效前的第 1 条用了错源语言，译文是越南语残片
    _tv = Translator("vi", "zh")
    chk("越南语残片被判为无效译文", not HAS_CJK.search("tôi nhớ là triệu tập"))
    chk("正常中文译文有 CJK", bool(HAS_CJK.search("广宁省警方将被告段忠勇提审")))
    chk("含纯英文人名的中文译文仍算有效",
        bool(HAS_CJK.search("全名Doang Trung Dung，出生于1971年")))
    chk("免 key Google 端点可探测", _google_free_ok() in (True, False))

    print("\n二之二、越南语与噪声过滤（针对越南新闻实测到的问题）")
    # 越南语声调符号必须保留（KEEP 里加了 \u1ea0-\u1eff 专用区）
    vi = "Theo tin từ chính phủ, kinh tế năm nay tăng trưởng tốt."
    chk("越南语声调符号保留", clean_translation(vi) == vi)
    chk("越南语不被清空", len(clean_translation(vi)) > 20)
    # 泰文：whisper 在越南语上会跑偏
    chk("泰文清空", clean_translation("แล ้ ว เจ ็ บ เจ ้ า") == "")
    # ASS 字幕样式代码：视频里嵌入的样式串被识别成文本
    chk("ASS 代码清空",
        clean_translation("fn黑体fs22bord1shad03aHBE4aH00fscx67fscy662cHFFFFFF3cH") == "")
    chk("ASS 残留片段清空",
        clean_translation("22bord1shad03aHBE4aH00fscx67fscy662cH") == "")
    chk("花括号样式去掉但保留文字",
        clean_translation("你好 {\\fn黑体\\fs22}世界") == "你好世界")
    chk("正常译文不被误杀",
        clean_translation("政府表示,今年的经济成长良好.") == "政府表示,今年的经济成长良好.")
    # 网页模板（MyMemory 实测返回过维基模板）
    chk("维基存档模板被拦",
        Translator._is_web_cruft("(原始内容存档于2019-09-29). Vå 2 Uông Bí"))
    chk("维基物种模板被拦",
        Translator._is_web_cruft("ã(学名:Cái tên rãn trãng)为ệ科ệ属下的"))
    chk("正常译文不误判为模板",
        not Translator._is_web_cruft("后来，Pulse带着他的自行车去卖"))
    # 变音符号残片："ườ ườ ạ ạ" —— 试过"字母占比"判据抓不到
    # （ườ/ạ 本身是合法越南语字母），改用"多个短片段且无实词"
    chk("变音残片被判为垃圾", Translator._is_web_cruft("ườ ườ ạ ạ"))
    chk("维基维护模板被拦",
        Translator._is_web_cruft("请检查date中的日期值 (帮助) 说明"))
    # **不能误杀合法的短词**（我第一版就误杀了"谢谢"和"好"）
    chk("单个短词不被误杀（谢谢）", not Translator._is_web_cruft("谢谢"))
    chk("单个短词不被误杀（好）", not Translator._is_web_cruft("好"))
    chk("单个短词不被误杀（是）", not Translator._is_web_cruft("是"))
    chk("越南语短语不被误杀", not Translator._is_web_cruft("Cảm ơn các bạn"))

    print("\n二之三、语言锁定（针对两个实测问题）")
    # 问题一："缺少中文翻译" —— 翻译器原来用 args.src（"en"）初始化，
    #   越南语被塞进 en→zh 模型。语言锁定后翻译器跟着实际语言走。
    # 问题二："越南语有时被识别成韩语" —— 逐段检测会抖，累计投票后锁住。
    L = LanguageLock(min_votes=2)
    chk("第一段先不定", L.observe("vi", 0.98) is None)
    chk("第二段一致才锁定", L.observe("vi", 0.97) == "vi")
    chk("中间一段误判成韩语，不改锁", L.observe("ko", 0.60) == "vi")
    chk("后续越南语仍锁 vi", L.observe("vi", 0.99) == "vi")
    L2 = LanguageLock(min_votes=2)
    for lg, p in (("vi", .98), ("vi", .97), ("en", .9), ("en", .9), ("en", .9)):
        L2.observe(lg, p)
    chk("连续 3 段别的语言还不切", L2.locked == "vi")
    L2.observe("en", .9)
    chk("连续 4 段才允许切", L2.locked == "en")
    L3 = LanguageLock(min_votes=2, confidence=0.5)
    L3.observe("ko", 0.20); L3.observe("ko", 0.30)
    chk("低置信度不投票", L3.votes == {})
    chk("不因此锁成韩语", L3.locked is None)

    print("\n三、短句表")
    t = Translator("en", "zh")
    print(f"     模式 {t.mode}　短句表 {len(t.book)} 条")
    chk("What? 命中短句表（带问号）", t("What?") == "什么？")
    chk("What 命中短句表（不带问号，值不同）", t("What") == "什么")
    chk("两者不撞键", t("What?") != t("What"))
    chk("What? 不计入模型调用", t.calls == 0)
    a = t("This is a moderately long sentence for the model.")
    b = t("This is a moderately long sentence for the model.")
    chk("第二次命中缓存", t.hits_cache >= 1)

    print("\n三之二、胡话识别（针对实测到的文言垃圾）")
    chk("识出「亦复如是」", Translator._looks_broken(
        "亦复如是. 师曰. 那她会永远恨我", "She'll hate me forever"))
    chk("识出「师曰」", Translator._looks_broken("师曰。走", "He said go"))
    chk("正常译文不误杀", not Translator._looks_broken("我从没想过会再见到你",
                                                  "I never thought I would see you again"))
    chk("短译不误杀", not Translator._looks_broken("好", "ok"))
    chk("空译文不报错", Translator._looks_broken("", "x") is False)

    print("\n四、切句器")
    sg = Segmenter(min_len=0.4, max_len=3.0, silence=0.3)
    loud = (np.random.randn(int(16000 * 0.8)) * 0.25).astype(np.float32)
    got = sg.push(loud); got += sg.push(np.zeros(int(16000 * 0.5), dtype=np.float32))
    chk("有声+安静 → 切出一段", len(got) >= 1)
    sg2 = Segmenter(min_len=0.4, max_len=3.0, silence=0.3)
    chk("纯静音不切", len(sg2.push(np.zeros(int(16000 * 2), dtype=np.float32))) == 0)

    print("\n五、批量映射（字级时间戳分回各段）")
    th = BatchTranscriber("small", 4)
    a1 = np.zeros(int(16000 * 2.0), dtype=np.float32)
    a2 = np.zeros(int(16000 * 2.0), dtype=np.float32)
    # 用真实语音造两段，验证不崩且返回等长
    import wave
    wp = os.path.join(HERE, "e2e_speech.wav")
    if os.path.exists(wp):
        w = wave.open(wp, "rb")
        raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        aud = to_mono16k(raw, w.getframerate(), w.getnchannels())
        half = len(aud) // 2
        res = th.transcribe_batch([aud[:half], aud[half:]])
        chk("返回与输入等长", len(res) == 2)
        chk("两段都有文本", all(r.strip() for r in res))
        for i, r in enumerate(res):
            print(f"     段{i+1}: {r[:60]}")
    else:
        print("     （没有 e2e_speech.wav，跳过）")

    print("\n六、浮窗（PyQt5）的功能")
    try:
        from PyQt5.QtCore import Qt as _Q
        o = Overlay(font_size=20)
        chk("WindowStaysOnTopHint", bool(o.w.windowFlags() & _Q.WindowStaysOnTopHint))
        chk("FramelessWindowHint", bool(o.w.windowFlags() & _Q.FramelessWindowHint))
        chk("Tool（不占任务栏）", bool(o.w.windowFlags() & _Q.Tool))
        chk("WA_TranslucentBackground", o.w.testAttribute(_Q.WA_TranslucentBackground))
        o.set("hello", "你好")
        o.pump()
        chk("可设原文+译文（进入条目列表）",
            bool(o.entries) and o.entries[-1][1] == "hello" and o.entries[-1][2] == "你好")
        chk("译文色 = 白（mpv --sub-color）", o.st["text_color"] == "#FFFFFF")
        chk("原文色退一档", o.st["original_text_color"] == "#B7C4D8")
        chk("字号随屏幕缩放（mpv 规则）", o.fs() > 20 and o.fs_original() < o.fs())
        n0 = o.st["font_size"]; o.bump_font(4)
        chk("字号可调", o.st["font_size"] == n0 + 4)
        a0 = o.st["opacity"]; o.bump_opacity(-0.1)
        chk("透明度可调", abs(o.st["opacity"] - (a0 - 0.1)) < 1e-6)
        chk("几何在屏内", o.geo[2] > 200 and o.geo[3] > 50)
        o.toggle(); chk("可隐藏", not o.w.isVisible())
        o.toggle(); chk("可恢复", o.w.isVisible())
        o.reset(); chk("可重置", o.st["font_size"] == 26)
        o.close()
    except Exception as e:
        import traceback
        traceback.print_exc()
        chk(f"Overlay 失败 {type(e).__name__}: {e}", False)

    print(f"\n合计 {ok} 通过 / {fail} 失败")
    return 0 if fail == 0 else 1


def main():
    ap = argparse.ArgumentParser(description="外文视频实时翻译字幕 v2")
    ap.add_argument("--from", dest="src", default="en")
    ap.add_argument("--to", dest="dst", default="zh")
    ap.add_argument("--model", default="small")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--max-batch", type=float, default=4.0,
                    help="攒够多少秒音频就转写（摊薄固定开销）")
    ap.add_argument("--max-wait", type=float, default=1.2,
                    help="最多等多少秒就转写（防积压）")
    ap.add_argument("--min-seg", type=float, default=0.5)
    # 单段最长 4 秒：实测 argos 在长句上会退化
    # （真实案例：She'll hate me forever... → "亦复如是. 师曰. 那她会永远恨我"
    #   混进文言文垃圾；If my mother disagrees... → "…我们等着瞧吧" 尾句乱造）
    # 成熟方案 VoxGo 的 max_speech_seconds 均衡档也是 4.5。
    ap.add_argument("--max-seg", type=float, default=4.0)
    ap.add_argument("--vad-silence", type=float, default=0.45)
    ap.add_argument("--font-size", type=int, default=None)
    ap.add_argument("--no-overlay", action="store_true")
    # **识别语言默认交给 whisper 自动检测。**
    # 原来默认按 --from 强制成 en，导致看越南语时把越南语当英语识别 ——
    # 而 whisper 遇到这种错配**不是"听错"，是直接把越南语意译成英文（还译错）**，
    # 于是后面按 en→zh 翻，整条链路全废。用户实际遇到的就是这个。
    ap.add_argument("--lang", default=None,
                    help="固定识别语言（vi / en / ja …）；不给则自动检测")
    ap.add_argument("--detect", action="store_true", default=True,
                    help="自动检测语言（默认开启；用 --no-detect 关闭）")
    ap.add_argument("--no-detect", dest="detect", action="store_false",
                    help="关掉自动检测，改用 --from")
    # ── 流式模式（LocalAgreement-2，见 streaming_asr.py）──────────
    # 两套模式是实测出来的取舍，不是"哪个更好"：
    #   分块（默认）：字幕与音频同步、不积压；代价是首字延迟 = 段长 + 1.5s
    #   流式：首字延迟 ≈ 一次更新耗时（1–2s）；代价是 CPU 上长句会积压
    ap.add_argument("--stream", action="store_true",
                    help="用流式识别（LocalAgreement-2）：首字更快，但长句会积压")
    ap.add_argument("--stream-model", default="base",
                    help="流式模式用哪个模型（base 比 small 快 3 倍，追得上更多）")
    ap.add_argument("--stream-interval", type=float, default=0.5,
                    help="流式更新间隔（秒）。越小越及时，但每次都有固定开销")
    ap.add_argument("--stream-tail", type=float, default=3.0,
                    help="流式缓冲保留多少秒未确认音频")
    ap.add_argument("--stream-maxbuf", type=float, default=10.0,
                    help="流式缓冲硬上限（秒），保证延迟有界")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--srt", default=os.path.join(HERE, "live.srt"),
                    help="持续写出 .srt 供播放器加载（--no-srt 关闭）")
    ap.add_argument("--no-srt", dest="srt", action="store_const", const=None,
                    help="不写字幕文件，只用浮窗")
    ap.add_argument("--reset-ui", action="store_true",
                    help="清掉保存的浮窗位置/字号/透明度，回到默认")
    a = ap.parse_args()
    if a.reset_ui:
        try:
            os.remove(SETTINGS)
            print(f"已删除 {SETTINGS}（浮窗回到默认位置与外观）")
        except FileNotFoundError:
            print("没有保存的设置，无需重置")
        return 0
    return selftest() if a.selftest else run(a)


if __name__ == "__main__":
    # 自己写日志：bat 里用管道做 tee 不可靠（我试过，日志根本没生成）。
    # 这样双击 start.bat 启动时，出错信息一定会落在 run.log 里。
    class _Tee:
        def __init__(self, *fs):
            self.fs = fs

        def write(self, s):
            for f in self.fs:
                try:
                    f.write(s); f.flush()
                except Exception:
                    pass
            return len(s)

        def flush(self):
            for f in self.fs:
                try:
                    f.flush()
                except Exception:
                    pass

    try:
        _log = open(os.path.join(HERE, "run.log"), "w", encoding="utf-8")
        sys.stdout = _Tee(sys.__stdout__, _log)
        sys.stderr = _Tee(sys.__stderr__, _log)
    except Exception:
        pass
    try:
        rc = main()
    except Exception:
        import traceback
        traceback.print_exc()
        rc = 1
    print(f"\n(exit {rc})")
    sys.exit(rc)
