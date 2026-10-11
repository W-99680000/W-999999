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
import concurrent.futures as _concurrent
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


_LLM_SINGLETON = []


def _get_llm():
    """拿一个可用的 LLM 翻译后端（进程内单例），没有就返回 None。

    用单例是因为探测本机端点（Ollama / DeepSeek）每次都有网络开销，
    而 Translator 会被反复构造（路由切换时）。
    """
    if _LLM_SINGLETON:
        return _LLM_SINGLETON[0]
    try:
        from llm_translate import from_env_or_args
        got = from_env_or_args()
    except Exception:
        got = None
    _LLM_SINGLETON.append(got)
    return got


def _pick_offline_source(lang, prob, min_conf=0.85):
    """本批检测结果是否**足够可信**，可以走离线 argos（en→zh）。

    去掉语言锁定后的路由判据。门槛比"能锁定"高得多，因为代价不对称：
      · 走 Google（sl=auto）：判错也无所谓，Google 自己再判一次，且更快
      · 走 argos（en→zh）：**源语言必须真的是英语**，否则俄语/越南语被
        塞进 en→zh 模型，输出是垃圾（实测过 argos 中继产出
        "我不确定,但我还不知道" 这种完全错的译文）
    所以只在高置信英语时才用 argos，其余一律 Google+auto。
    返回 True 表示"可以用 en 作为源语言"，False 表示"交给 Google 判"。
    """
    if not lang:
        return False
    if not str(lang).lower().startswith("en"):
        return False
    return float(prob or 0) >= min_conf


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


def is_hallucination(s: str) -> bool:
    """判断这句是不是 whisper 的**复读幻觉**。

    实测踩到的原始输出（45 秒真实越南新闻里）：
        「不能理解,怎麼理解,怎麼理解,怎麼理解,怎麼理解,怎麼理解。」
        「왕코, 왕코, 왕코, 왕코, 왕코, 왕코.」
    这两种都是 whisper 在**静音或低能量音频**上退化出的模式：
    把同一个词/字无限重复。

    这不是翻译问题 —— 是识别层出的垃圾，必须在进字幕之前拦掉。

    判据（都要求"重复"达到相当密度，避免误杀正常口头禅）：
      · 去掉标点空白后，**最长的单字符重复串** ≥ 6
      · 或 以逗号/句号切出的片段里，**重复片段占 ≥ 5 个且占比 ≥ 60%**
    """
    if not s:
        return False
    core = re.sub(r"[\s，。！？、；：,.!?;:…—\-]+", "", s)
    if len(core) < 6:
        return False
    # ① 单字符长重复：「AAAAAA」
    run = 1
    best = 1
    for i in range(1, len(core)):
        run = run + 1 if core[i] == core[i - 1] else 1
        best = max(best, run)
    if best >= 6:
        return True
    # ② 片段复读：「X, X, X, X, X」
    parts = [p.strip() for p in re.split(r"[，。！？、；：,.!?;:…\s]+", s) if p.strip()]
    if len(parts) >= 5:
        from collections import Counter
        c = Counter(parts)
        top_n = c.most_common(1)[0][1]
        if top_n >= 5 and top_n / len(parts) >= 0.6:
            return True
    # ③ 双字/三字循环：「怎麼理解怎麼理解怎麼理解」
    for w in (2, 3, 4):
        if len(core) >= w * 5:
            chunk = core[:w]
            if core == chunk * (len(core) // w) and len(core) // w >= 5:
                return True
    return False


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
class NoiseGate:
    """自适应噪声门：从音频本身**开机校准**，而不是写死阈值。

    借鉴 VoxGo（zxbb1190/VoxGo_game_voice_trans），但参数按我们自己的
    实测数据改了 —— 直接用它的默认值会失效。

    实测依据（30 秒真实环回，越南新闻，按 1 秒窗）：
        整段 RMS = -21.1 dBFS　峰值 -2.6 dBFS
        P5  = -40.9 dBFS   ← 真正的静音/底噪
        P20 = -28.6 dBFS   ← **已被轻声语音污染，不能当噪声底**
        P50 = -23.3 dBFS
        P80 = -18.9 dBFS
        中位语音 -18.9 dBFS

    由此得出两条与 VoxGo 不同的结论：

    ① **不做归一化**。VoxGo 用 target=-20 dBFS / +12dB 上限做增益。
       我们的语音中位已经是 -18.9 dBFS，测得"需要 -1.1 dB"，
       实际施加 0.0 dB —— 等于白做一层还可能引入削波。
       要看音频电平用 --show-level。

    ② 噪声底取 **P5 而不是 P20**。VoxGo 用 P20+7dB 并夹在
       [-45,-30]；我们的 P20 已经是 -28.6（被语音污染），
       夹取后门限直接顶到 -30，会切掉轻声。
       用 P5 = -40.9 做底，+8dB → -32.9 dBFS，落在安全区间。
       上界仍守 -30 dBFS（再高就会切语音）。
    """

    def __init__(self, margin_db=8.0, floor_pct=5.0,
                 lo_dbfs=-62.0, hi_dbfs=-48.0, warmup_sec=6.0):
        """噪声门：只挡**真静音**，不做语音检测。

        **hi_dbfs 从 -30 放宽到 -48 —— 这是"识别不到声音"的修复。**
        故障现象：连续 85 批、每批都有音频，但一条字幕都没有，
        日志全是"转写 0.00s、已显示 0 段"；而远端其实是好的
        （诊断显示"远端 47字 失0"）—— 是**门把段全挡了**。

        根因：**门限是相对校准的，而系统音量是绝对的。**
            th = (开机 6 秒电平的 5 百分位) + 8dB，再夹到 [lo, hi]
        开机时音量正常 → floor 高 → th 顶到上限 -30 dBFS。
        之后把音量调小（或那段内容本身轻），段电平落在 -32~-44，
        **全部低于 -30，全被拒**，字幕整场消失。

        -30 这个上限太激进了。这个门的职责只是"别把纯静音送去识别"
        （静音上识别会编内容），语音检测由远端 VAD 与 whisper 的
        vad_filter 负责。取 -48：真静音（<-55）照样挡，
        音量很小的正常语音（-45 上下）能过。
        """
        self.margin_db = margin_db
        self.floor_pct = floor_pct
        self.lo_dbfs = lo_dbfs
        self.hi_dbfs = hi_dbfs
        self.warmup_sec = warmup_sec
        self.samples = []            # 开机阶段收集的 dBFS
        self.threshold_dbfs = None
        self.floor_dbfs = None
        self.rejected = 0
        self.checked = 0
        self._seconds = 0.0

    @staticmethod
    def dbfs(x):
        if x is None or len(x) == 0:
            return -120.0
        r = float(np.sqrt(np.mean(np.asarray(x, dtype=np.float64) ** 2)))
        return 20.0 * np.log10(max(r, 1e-10))

    def observe(self, x):
        """喂一段音频；开机阶段用它校准。返回 None。"""
        if x is None or len(x) == 0:
            return
        self._seconds += len(x) / TARGET_RATE
        self.samples.append(self.dbfs(x))
        # 只留开机窗口的样本，避免长跑时永远在校准
        if self._seconds >= self.warmup_sec and self.threshold_dbfs is None:
            self._calibrate()

    def _calibrate(self):
        if not self.samples:
            return
        self.floor_dbfs = float(np.percentile(self.samples, self.floor_pct))
        th = self.floor_dbfs + self.margin_db
        th = max(self.lo_dbfs, min(self.hi_dbfs, th))
        self.threshold_dbfs = th

    def is_speech(self, x):
        """这段音频是否够响。未校准前一律放过（宁多勿漏）。

        注意：必须在这里也把样本喂进 observe()，否则永远校准不了 ——
        原来只有外部调 observe() 才收集样本，而实际调用只走 is_speech，
        结果门限一直是"校准中"，等于没门。
        """
        self.checked += 1
        self.observe(x)                 # 开机阶段持续收集，够时长就校准
        if self.threshold_dbfs is None:
            return True                 # 还没校准 → 放过
        if self.dbfs(x) < self.threshold_dbfs:
            self.rejected += 1
            return False
        return True

    def status(self):
        if self.threshold_dbfs is None:
            return "噪声门=校准中"
        return (f"噪声门={self.threshold_dbfs:.1f} dBFS"
                f"（底 {self.floor_dbfs:.1f} + {self.margin_db:.0f}）"
                f"　拦 {self.rejected}/{self.checked}")


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
        # 远端识别器。非 None 时 transcribe_batch 会改走它
        # （main 里按 --remote-asr 赋值，见那里的注释）
        self.remote = None
        self.last_text = ""
        # 远端连续无结果的次数 + 最近一次统计。原来远端失败是**静默**的：
        # 日志只刷"转写 0.00s、已显示 0 段"，没有错误也没有原因，
        # 操作者只能看到"没在听了"。现在这些用来周期性打出诊断。
        self.rejected_remote = 0
        self.last_remote_stats = {}
        self.last_lang = None          # 上一次检测到的语言（给 LanguageLock 用）
        self.last_lang_prob = 0.0
        self.rejected_hallu = 0        # 复读幻觉拦掉的段数
        self.rejected_lowq = 0         # 段级质量闸（nsp/logp/cr）拦掉的段数
        self.rejected_gate = 0         # 噪声门拦掉的段数
        self.last_quality = []         # 最近一批的质量信号，供诊断/自检
        self.gate = NoiseGate()        # 开机能自校准的噪声门

    def transcribe_batch(self, segs, lang=None):
        """segs: list[np.ndarray] → list[str]，**与输入严格等长**（对应各段文本）。

        噪声门、质量闸、幻觉过滤都可能丢掉某段，但返回值必须仍然与输入
        一一对应 —— 调用方是按 zip(segs_in, texts) 取用的。
        所以这里用 keep_ix 记下保留下来的下标，最后把结果**散回原位**，
        丢掉的段填空串。早先版本直接 return 过滤后的列表会把对应关系错位。
        """
        if not segs:
            return []
        n_in = len(segs)
        # 噪声门：低于自适应门限的段不送识别（静音上 whisper 会编内容）
        keep_ix = [i for i, s in enumerate(segs) if self.gate.is_speech(s)]
        self.rejected_gate = self.gate.rejected
        if not keep_ix:
            return [""] * n_in
        segs = [segs[i] for i in keep_ix]
        lengths = [len(s) / TARGET_RATE for s in segs]
        # ── 远端识别优先（存在就用它）──────────────────────────
        # 远端一次调用就能转写整段（实测 12 秒 → 0.76s），所以不需要
        # 本地那套"拼接后按字级时间戳切回各段"的映射，直接一次出结果。
        # 语种也用远端自己判的（annotations[0].language），写回 last_lang
        # 让主程序的语言路由继续工作。
        _rem = getattr(self, "remote", None)
        if _rem is not None:
            cat = np.concatenate(segs)
            txt, rlang = _rem(cat)
            # **远端失败必须留下痕迹。** 我第一版这里静默返回空串：
            # 远端一挂，日志只有"转写 0.00s、已显示 0 段"刷屏几百行，
            # 既没错误也没原因 —— 操作者只能看到"没在听了"。
            # 现在记下电平/返回长度/错误/失败计数，并周期性打出来。
            self.last_remote_stats = {
                "secs": len(cat) / TARGET_RATE,
                "rms": float(np.sqrt(np.mean(cat.astype(np.float64) ** 2)))
                if len(cat) else 0.0,
                "txt_len": len(txt or ""),
                "lang": rlang,
                "err": getattr(_rem, "last_err", ""),
                "calls": getattr(_rem, "calls", -1),
                "fails": getattr(_rem, "fails", -1),
            }
            if rlang:
                self.last_lang = rlang
                self.last_lang_prob = 0.99      # 远端自己判的，视为高置信
            if not txt:
                self.rejected_remote += 1
                if self.rejected_remote in (1, 5, 20) or \
                        self.rejected_remote % 50 == 0:
                    _s = self.last_remote_stats
                    print(f"  ！远端识别无结果（连续第 {self.rejected_remote} 次）"
                          f"　音频 {_s['secs']:.1f}s RMS {_s['rms']:.4f}"
                          f"　调用 {_s['calls']} 失败 {_s['fails']}"
                          f"　错误: {_s['err'] or '无（返回空串）'}", flush=True)
                return [""] * n_in
            self.rejected_remote = 0
            # 远端不做字级时间戳，无法按段切分 → 整段结果给最长的那一段，
            # 其余填空（调用方 zip 取用时只有一条会显示，符合预期：
            # 批次被放大后本来就是"一次出整段字幕"）
            out = [""] * n_in
            out[keep_ix[int(np.argmax(lengths))]] = txt
            self.last_text = txt
            return out
        cat = np.concatenate(segs)
        total = len(cat) / TARGET_RATE
        segs_out, info = self.m.transcribe(
            cat, language=lang, beam_size=5, vad_filter=True,
            condition_on_previous_text=False, temperature=0.0,
            word_timestamps=True)
        segs_out = list(segs_out)
        # ── 段级质量闸 ──────────────────────────────────────────
        # 用 whisper **自己算出的**三个信号判废，而不是我手搓的文本启发式。
        # 实测依据（4 秒合成信号，small 模型）：
        #     噪声 RMS=0.05 / 正弦扫频，vad_filter=False
        #       → 输出 "You"，no_speech_prob=0.771，avg_logprob=-0.774  ← 纯幻觉
        #     同样输入 vad_filter=True → 无段（正确滤掉）
        # 阈值借鉴 VoxGo（zxbb1190/VoxGo_game_voice_trans）：它用
        #   no_speech_prob>=0.55 / avg_logprob<=-0.75 / compression_ratio>=2.4
        # 三者含义不同，要一起看：
        #   no_speech_prob 高    → 这段其实是静音/噪声
        #   avg_logprob 很低     → 解码不可信
        #   compression_ratio 高 → 复读（同一串反复出现）
        self.last_quality = []
        keep = []
        for s in segs_out:
            nsp = float(getattr(s, "no_speech_prob", 0) or 0)
            lgp = float(getattr(s, "avg_logprob", 0) or 0)
            cr = float(getattr(s, "compression_ratio", 0) or 0)
            bad = (nsp >= 0.55) or (lgp <= -0.75) or (cr >= 2.4)
            self.last_quality.append((nsp, lgp, cr, bad, s.text.strip()[:40]))
            if not bad:
                keep.append(s)
            else:
                self.rejected_lowq += 1
        segs_out = keep
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
            if is_hallucination(txt):
                self.rejected_hallu += 1
                res = [""] * len(segs)
            else:
                res = [txt] + [""] * (len(segs) - 1)
            return self._scatter(res, keep_ix, n_in)

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
        res = [re.sub(r"\s{2,}", " ", " ".join(ws)).strip() for ws in out]
        # 幻觉过滤：whisper 在静音/低能量上会复读，那种文本不能进字幕
        for i, s in enumerate(res):
            if is_hallucination(s):
                res[i] = ""
                self.rejected_hallu += 1
        return self._scatter(res, keep_ix, n_in)

    @staticmethod
    def _scatter(res, keep_ix, n_in):
        """把"只保留下标 keep_ix 的结果"散回长度 n_in 的列表，空隙填 ""。"""
        if len(keep_ix) == n_in:
            return res
        full = [""] * n_in
        for pos, i in enumerate(keep_ix):
            if pos < len(res):
                full[i] = res[pos]
        return full


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

    def __init__(self, min_votes=3, confidence=0.7):
        self.min_votes = min_votes
        self.confidence = confidence
        self.votes = {}
        self.locked = None
        self.history = []
        self.switches = 0
        self.rejected_lowconf = 0

    def peek(self):
        """只看当前认定的语言，**不喂新样本、不改状态**。

        为什么需要它：批处理必须在**转写之前**就把译器定好
        （不然本批中途语言判定一变，剩下的段就会用新译器译旧语言）。
        所以先 peek() 拿判定，转写完再 observe() 更新。
        """
        return self.locked

    def observe(self, lang, prob):
        """喂一次检测结果，返回当前认定的语言（可能还是 None）。

        **低置信的判定一律不投票、也不进历史。**
        实测依据（45 秒真实越南新闻环回，按 4 秒切 11 块）：
            块  语言  置信   能量RMS   文本
             1   en  0.227  0.0000   （静音，空）
             2   en  0.340  0.0109   （近静音）
             3   zh  0.659  0.0434   應該是連排節應該聽了吧      ← 幻觉
             4   vi  0.804  0.0394   Còn giá quay hông…（真语音）
             7   zh  0.552  0.0391   不能理解,怎麼理解,怎麼理解…  ← 复读幻觉
            11   ko  0.661  0.0072   왕코, 왕코, 왕코…            ← 能量到噪声底
        全部**误判的置信都 < 0.7**，全部真实越南语语音都 ≥ 0.688。
        所以把门槛从 0.5 提到 0.7：
          · 挡掉静音/噪声上的幻觉判定（0.227 / 0.340 / 0.552 / 0.659）
          · 真语音照样进票
        再把 min_votes 从 2 提到 3：单个高置信误判不再能一票定音。
        """
        p = float(prob or 0)
        if not lang:
            return self.locked
        if p < self.confidence:
            # 低置信：不投票、不进历史（否则它会污染 recent 判定）
            self.rejected_lowconf += 1
            return self.locked
        self.history.append((lang, round(p, 3)))
        if self.locked is None:
            self.votes[lang] = self.votes.get(lang, 0) + 1
            top, n = max(self.votes.items(), key=lambda kv: kv[1])
            if n >= self.min_votes:
                self.locked = top
            return self.locked
        # 已锁定：连续多段都是别的语言才允许切
        recent = [h[0] for h in self.history[-5:]]
        if len(recent) >= 5 and all(x != self.locked for x in recent):
            new = max(set(recent), key=recent.count)
            self.locked = new
            self.switches += 1
            self.votes = {new: len(recent)}
        return self.locked

    def status(self):
        return (f"语言={self.locked or '检测中'}  "
                f"投票={self.votes}  切换={self.switches}")


# ── 翻译：四层（短句表 → 缓存 → 直连 → 中继/在线）───────────────
class LruTtlCache:
    """带容量上限与过期时间的翻译缓存。

    照搬成熟方案（youtube-live-translate 的 TranslationCache）的两个点：
      · maxSize = 500，满了淘汰**最旧**的一条（LRU）
      · ttl = 24 小时
    为什么要有：新闻/连续剧里重复句非常多（开场白、垫词、口头禅），
    命中就直接返回，既省一次请求（免 key 端点有限流风险），也瞬间出字。
    """

    def __init__(self, max_size=500, ttl=24 * 3600):
        from collections import OrderedDict
        self.d = OrderedDict()
        self.max_size = max_size
        self.ttl = ttl
        self.hits = self.misses = self.evicted = self.expired = 0

    def get(self, key):
        item = self.d.get(key)
        if item is None:
            self.misses += 1
            return None
        val, ts = item
        if time.time() - ts > self.ttl:
            self.d.pop(key, None)
            self.expired += 1
            self.misses += 1
            return None
        self.d.move_to_end(key)          # 命中就挪到最新
        self.hits += 1
        return val

    def set(self, key, val):
        self.d[key] = (val, time.time())
        self.d.move_to_end(key)
        while len(self.d) > self.max_size:
            self.d.popitem(last=False)   # 淘汰最旧
            self.evicted += 1

    def __len__(self):
        return len(self.d)


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
        # LRU + TTL 缓存（原来只是个普通 dict，无上限、不过期）
        self.cache = LruTtlCache(max_size=500, ttl=24 * 3600)
        self.book = PHRASEBOOK.get(src, {}) if dst.startswith("zh") else {}
        self.hits_book = self.hits_cache = self.calls = self.rejected = 0
        self.web_cruft_blocked = self.web_cruft_fixed = 0
        self.stale_dropped = self.kept_last = 0
        self.llm = None            # LLM 后端（_setup 里按可用性赋值）
        self.llm_fails = 0
        self._gf_fails = 0
        self.last_ok = ""           # 上一句成功译文（失败时保持不刷屏）
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
        #
        # ⑥ **LLM 后端**（llm_translate.py）—— 如果有可用端点就优先用它。
        # 实测对比（同一批真实字幕句，DeepSeek deepseek-chat）：
        #     波兰语  G(3.64s) 那是十万个月，是美国百万富翁的妻子。
        #             L(0.61s) 这是十万个月，也是美国百万富翁的妻子。
        #     俄语    G(1.82s) 我真的只是指望着，这里有一个女人。
        #             L(0.76s) 实际上，我指望着的就是这个，这里有个女人。
        #     俄语    G(1.23s) 那么，什么，告诉我，那里有什么，这有什么故事？
        #             L(0.55s) 那么，说说看，那件事到底是怎么回事？
        #     英语长句 G(1.50s) 最大的优点是，一旦操作员正确锁定目标，制导系统就不会受到干扰。
        #             L(0.72s) 它最大的优势在于，一旦操作员正确锁定目标，制导系统就能免受干扰。
        #   → 平均 0.63s vs 2.00s（快 3 倍），且译文明显更像字幕。
        # 但只在文本够长时才用（碎片会让小模型更啰嗦，见 llm_translate 的说明）。
        self.llm = _get_llm()          # 可能为 None（那就退回 Google）
        if self.mode in ("none", "argos-pivot", "mymemory", "online-first"):
            if self.llm is not None:
                self.mode = "llm"
            else:
                self.mode = "google-free" if _google_free_ok() else (
                    "argos-pivot" if self._pivot else "mymemory")

    # ── 免 key 的 Google 端点 ────────────────────────────────
    def _google_free(self, text):
        """端点变体是关键（实测）：
            translate.googleapis.com {client=gtx}           → HTTP 429（被限）
            translate.googleapis.com {client=dict-chrome-ex} → 通，2.30s
            **clients5.google.com    {client=dict-chrome-ex} → 通，0.61s ← 用这个**
        返回格式 [[["译文","原文",...],...],...]

        **sl=auto：源语言交给 Google 判，不用我们传。**
        借鉴 youtube-live-translate（它一直就是 sl=auto）。
        为什么必须这样：我们上游是 whisper 的语言检测，而它**会判错**
        （实测越南语偶尔被判成韩语）。一旦判错，我们原来会把 sl=ko
        发出去，可文本是越南语 —— 整句译文直接废掉。

        实测对照（同一批越南新闻句，真实请求）：
            sl=ko   2.81s / 4.47s / 3.16s → **越南语原样返回，没翻译**
            sl=vi   2.51s / 1.67s / 2.41s → 广宁省警方刚刚以谋杀罪起诉被告段忠勇。
            sl=auto 0.58s / 1.09s / 0.90s → 同上，完全正确
        **不但修掉判错，还快 2.8 倍**（0.86s vs 2.36s 平均）。
        所以 Google 这条路上让它自己判，我们只负责选路
        （en→argos 离线走专用模型，其他→Google + auto）。
        """
        import urllib.parse
        import urllib.request
        q = urllib.parse.quote((text or "")[:1500])
        url = (f"https://clients5.google.com/translate_a/single"
               f"?client=dict-chrome-ex&sl=auto&tl={self._tl()}&dt=t&q={q}")
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

    def _target_name(self):
        """给 LLM 用的目标语言全名（提示词里比语言码更好懂）。"""
        d = (self.dst or "").lower()
        if d.startswith("zh"):
            return "Simplified Chinese"
        return {"en": "English", "ja": "Japanese", "ko": "Korean",
                "ru": "Russian", "vi": "Vietnamese",
                "fr": "French", "de": "German", "es": "Spanish"}.get(d, d)

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
        hit = self.cache.get(key)                 # ② 会话缓存（LRU+TTL）
        if hit is not None:
            self.hits_cache += 1
            return hit
        self.calls += 1
        # **失败一律返回空串，不往字幕里塞 [译错 ...]**。
        # 原来失败时会 output "[译错 URLError]" 这种占位文本，操作者
        # 会在画面上真看到它。成熟方案（youtube-live-translate）的做法是
        # 请求失败直接 throw，字幕保持上一句不动 —— 我们也这样：
        # 返回空串，上层看到空串就不更新浮窗（见 run() 里的 if zh）。
        out = ""
        if self.mode == "argos":                  # ③ argos 直连 + 清洗
            try:
                out = clean_translation(self._fn(text))
            except Exception:
                out = ""
        elif self.mode == "argos-pivot":          # ④ 中继 src→en→dst
            try:
                mid = self._pivot[0](text)
                out = clean_translation(self._pivot[1](mid))
            except Exception:
                out = ""
        elif self.mode == "llm":                   # ⑥ LLM（质量最好）
            # **有 LLM 就一律用它，不做任何长度分流。**
            #
            # 这段我改过两次，把教训记下来：
            #   第一版：长句走 LLM、短句退回 Google。
            #     实测两处都错：短文本走 LLM 只需 ~0.5s（往返是提示词
            #     常数开销，与长度无关）；而短文本退回 Google **反而慢**
            #     （7.19s / 8.73s），更糟的是它在超时/限流时**返回空串**，
            #     于是整条字幕消失 —— 操作者报的"字幕不可见"就是这个：
            #     短句（如 "Ок"）走了 Google 那条路，失败后什么都没有，
            #     浮窗一直收不到内容。
            #   第二版：只把"没配 LLM"时才算短句分流。仍会走到 Google。
            #   现在：**LLM 可用 → 全部走 LLM**；只有 LLM 缺失或返回空
            #     才用免 key 端点兜底。少一条分支就少一类 bug。
            if self.llm is not None:
                out = clean_translation(
                    self.llm(text, target=self._target_name()))
                if not out:
                    self.llm_fails += 1
            if not out:
                try:
                    out = clean_translation(self._google_free(text))
                except Exception:
                    out = out or ""
        elif self.mode == "google-free":          # ⑤ 免 key Google
            try:
                out = clean_translation(self._google_free(text))
            except Exception:
                out = ""
                # 免 key 端点偶尔抽风 → 立刻退回中继，别让字幕断
                self._gf_fails += 1
                if self._pivot and self._gf_fails <= 5:
                    try:
                        out = clean_translation(
                            self._pivot[1](self._pivot[0](text)))
                    except Exception:
                        out = ""
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
            self.cache.set(key, out)
            self.last_ok = out
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
    # 译器实例缓存 + 当前键（None = 让 Google 自己判源语言）。
    # **必须在初始化 tr 之前定义**（我第一版放在它后面，启动即
    # UnboundLocalError: _tr_key）。
    _tr_cache = {}
    _tr_key = [None]
    # **初始译器用 auto，不要用 args.src。**
    # args.src 默认 "en"，会立刻选中 argos（en→zh）。但第一批的语言检测
    # 结果还没出来，万一不是英语就把非英语喂给了 en→zh 模型 → 垃圾。
    # 用 auto 起步（Google + sl=auto，判错也无所谓），等检测结果出来
    # 再按 _pick_offline_source 决定是否切到 argos。
    tr = Translator("auto", args.dst)
    _tr_key[0] = None if not args.lang else args.lang
    print(f"翻译: {tr.mode}　短句表 {len(tr.book)} 条")
    # **翻译器要跟着实际语言走。** 原来用 args.src（默认 "en"）初始化，
    # 看越南语时越南语被塞进 en→zh 模型 —— 这是"缺中文翻译"的真原因。
    lang_lock = LanguageLock(min_votes=3)
    # （_tr_cache / _tr_key 已在上面、初始化 tr 之前定义）
    # 计数器必须在两个分支**之前**初始化：流式分支里也要用 n_hallu，
    # 原来它是在流式分支之后才定义的（会把流式模式直接 NameError 崩掉）。
    n_batch = n_seg = 0
    n_hallu = 0
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
        # ── 远端识别（阿里云百炼 qwen3-asr-flash）────────────────
        # 本地 CPU 的延迟已到天花板（_bench_bottleneck.py 实测）：
        #   一次 transcribe 固定成本 ≈ 2.0 秒，**与音频长短无关**
        #   （0.2 秒音频 2.078s、8 秒音频 2.288s）
        #   参数/线程/compute_type 全动不了它；降模型掉准确度
        #   （base 快 3 倍但越南语一致率只 0.53）
        # 远端实测：12 秒音频 0.76s、16 秒音频 0.88s —— 快一倍，
        # **而且一次吃得下长音频**，所以批次可以放大、少等句子。
        _rasr = None
        if getattr(args, "remote_asr", False):
            try:
                from remote_asr import from_env_or_key
                _rasr = from_env_or_key()
            except Exception as e:
                print(f"  远端识别不可用: {type(e).__name__} {e}")
            if _rasr is None:
                print("  远端识别：没找到 key（~/.dsh/.dashscope），用本地")
            else:
                th.remote = _rasr
                print(f"  远端识别已启用：{_rasr.status()}")
                print(f"      批量上限放宽到 {args.max_batch}s"
                      f"（远端一次吃得下长音频）")
                # **批次要缩小，不是放大。** 我第一版想反了：以为远端能
                # 吃长音频就该放大批次来摊薄调用开销。那是**本地**的逻辑
                # （本地固定开销 2.0s，只有放大批次才划算）。
                # 远端固定开销只有 0.427s、斜率 0.029s/音频秒，所以
                # **等批次攒齐的时间反而成了主要成本**。
                # 实测（_bench_chunk_size.py，45 秒真实音频、实时喂入）：
                #     max_batch  中位延迟  P90    调用数
                #       12.0s     0.77s   0.83s     3
                #        8.0s     0.69s   0.71s     4
                #        2.0s     0.49s   0.55s    15
                #        1.0s     0.37s   0.45s    39
                # 限流 RPM=100，1 秒一批 = 60 RPM，在限内。
                # 取 1.5s：延迟接近最优，调用数只有一半，留足余量。
                if args.max_batch > 1.5 and not args.no_widen:
                    old = args.max_batch
                    args.max_batch = 1.5
                    args.max_wait = 0.6
                    th.max_batch, th.max_wait = args.max_batch, args.max_wait
                    print(f"      批次 {old}s → {args.max_batch}s"
                          f"（远端固定开销仅 0.43s，小批次延迟更低）")
        print(f"转写: 分块 {args.model}　批量≤{args.max_batch}s / 等≤{args.max_wait}s")
        print("      优点：字幕与音频同步、不积压")
        print("      代价：要等整段说完（首字延迟 = 段长 + 1.5s）")
    cap = LoopbackCapture().start()
    print(f"采集: [{cap.dev['index']}] {cap.dev['name'][:44]}")
    print("控制：**拖动字幕本身**移动（整块都能拖，点一下不会动）")
    print("      滑轮字号　Shift+滑轮条数　Alt+滑轮宽度　Ctrl+滑轮透明度")
    print("      方向键微调　L 排布　J 对齐　M 调整模式　T 深浅底")
    print("      H 隐藏　R 重置　C 回默认位置　Esc 退出\n")

    seg = Segmenter(min_len=args.min_seg, max_len=args.max_seg,
                    silence=args.vad_silence)
    ov = None if args.no_overlay else Overlay(font_size=args.font_size)
    srt = SrtWriter(args.srt) if args.srt else None
    if srt:
        print(f"字幕文件: {args.srt}　（播放器加载它，不依赖浮窗）")
    if ov:
        # 先闪一行让用户确认框在哪；随后由 idle_hint() 接替成**常驻**的
        # "正在听"提示。为什么需要常驻：没有音频时浮窗是全透明的
        # （bg_alpha=0），于是"程序在跑但没声音"和"程序没启动"看起来
        # 完全一样 —— 操作者几次报"字幕消失"都是这个。
        ov.flash("live-subtitle ready — listening…", 3.0)
        print(f"浮窗位置: {ov.geo[0]},{ov.geo[1]}  尺寸 {ov.geo[2]}x{ov.geo[3]}  "
              f"flags=0x{int(ov.w.windowFlags()):08X}")
        try:
            from PyQt5.QtCore import QTimer
            QTimer.singleShot(3200, ov.idle_hint)
        except Exception:
            ov.idle_hint()
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
    t_all = time.time()

    # ── 翻译并行化所需的状态（分块路线用） ──────────────────────
    # 为什么要并行：实测（_measure_e2e_lag.py，70 秒真实音频）字幕平均落后
    # 4.8 秒，其中翻译占 1~2.3 秒，而原来是串行 —— 翻译那段时间机器空着、
    # 下一批音频已经在积压（后半 5.35s 比前半 4.07s 更落后）。
    #
    # 设计：
    #   _seq_in   批号，提交时自增
    #   _txtq     批号 → (原文段, 该批的 [段, 文本] 列表)
    #   done_q    后台译好的 (批号, [译文...])
    #   _order    {批号: (段列表, 译文列表)}，用来**按批序**显示
    #   之所以要按序：长句后译完、短句先译完时，字幕会乱序。
    import queue as _queue_mod
    _th_pool = _concurrent.ThreadPoolExecutor(max_workers=2)
    done_q = _queue_mod.Queue()
    _order = {}
    _next_show = [0]              # 下一个该显示的批号
    _seq_in = [0]                 # 下一个要提交的批号

    def _drain_one(timeout=None):
        """取一批译文进来（耐心可选）。"""
        try:
            if timeout is None:
                batch = done_q.get_nowait()
            else:
                batch = done_q.get(timeout=timeout)
        except Exception:
            return False
        _order[batch[0]] = (batch[1], batch[2])
        return True

    def _show_ready():
        """按批序把已译好的批次显示出来。返回显示了几段。"""
        shown = 0
        while _next_show[0] in _order:
            segs_k, zh_k = _order.pop(_next_show[0])
            _next_show[0] += 1
            # segs_k 的元素是 (段音频, 原文)；zh_k 是等长译文列表
            for (seg, txt), zh in zip(segs_k, zh_k):
                if not txt:
                    continue
                shown += 1
                dur = len(seg) / TARGET_RATE if seg is not None else 0.0
                print(f"[{shown:>3}] 段{dur:4.1f}s | 原: {txt[:52]}")
                if zh:
                    print(f"      译: {zh[:52]}")
                if ov:
                    if zh:
                        ui_q.put((txt, zh))
                    else:
                        # 译文为空（可能是被质量闸挡了、或还没回来）：
                        # 传哨兵，让浮窗**保留上一帧译文**而不是闪成空白
                        # （借鉴 Whisper-Ollama-Translator 的 PENDING_KEEP_OLD）
                        _SENT = getattr(ov, "PENDING_KEEP_OLD",
                                        "\x00PENDING_KEEP_OLD\x00")
                        ui_q.put((txt, _SENT))
                if srt:
                    srt.add(txt, zh)
        return shown

    def _drain_translations(block=False, timeout=0.2):
        if block:
            _drain_one(timeout=timeout)
        else:
            while _drain_one():
                pass
        return _show_ready()

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
                    src_txt = stable or partial
                    # 复读幻觉过滤：whisper 在静音/低能量上会复读同一串
                    # （实测「怎麼理解,怎麼理解…」「왕코, 왕코…」）。
                    # 那种文本不能进字幕，也不能进翻译。
                    if is_hallucination(src_txt):
                        n_hallu += 1
                        print(f"[{n_seg:>3}] 丢弃复读幻觉: {src_txt[:44]}")
                        continue
                    zh = tr(src_txt)
                    print(f"[{n_seg:>3}] 稳定: {stable[-60:]}")
                    if partial:
                        print(f"      局部: {partial[:60]}")
                    if zh:
                        print(f"      译: {zh[:60]}")
                    if ov:
                        ui_q.put((src_txt, zh))
                    if srt:
                        srt.add(src_txt, zh)
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
            print(f"翻译：短句表命中 {tr.hits_book}　缓存命中 {tr.hits_cache}"
                  f"（{tr.cache.hits} 次 LRU 命中 / 淘汰 {tr.cache.evicted}）　"
                  f"模型调用 {tr.calls}　丢弃 {tr.rejected}")
            print(f"识别：复读幻觉拦掉 {n_hallu} 段　"
                  f"低置信判定拒票 {lang_lock.rejected_lowconf} 次")
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
            # ── 源语言：完全不做"锁定"，每批独立决策 ──────────────
            # 操作者要求去掉语言锁定。为什么可以去掉：
            #   ① Google 那条路本来就是 sl=auto（它自己判源语言，实测比
            #      whisper 准，而且**快 2.8 倍**：0.86s vs 2.36s）
            #   ② 锁定唯一的用途是**选译器**（en→argos 离线，其余→Google）
            # 所以"去掉锁定"= 把这一个决策从"累计投票的结果"改成
            # "本批自己的检测结果"。
            #
            # **但要防一个坑**：Translator("en") 会走 argos。如果 whisper
            # 把俄语误判成 en，就会把俄语喂给 en→zh 模型 → 输出垃圾。
            # 所以路由到 argos 的门槛比"能锁定"更高：要 conf >= 0.85。
            # 达不到就退到 Google + sl=auto（那条路判错也无所谓）。
            #
            # 译器实例缓存：argos 加载模型有开销，不能每批重建。
            want = _pick_offline_source(th.last_lang, th.last_lang_prob)
            cur_src = "en" if want else None
            if cur_src != _tr_key[0]:
                try:
                    tr = Translator(cur_src or "auto", args.dst)
                    _tr_key[0] = cur_src
                    _tr_cache[cur_src] = tr
                    print(f"  → 译器切到 {cur_src or 'auto'}"
                          f"（{tr.mode}；本批检测 "
                          f"{th.last_lang}/{th.last_lang_prob:.2f}）")
                except Exception as e:
                    print(f"  翻译器切换失败: {type(e).__name__} {e}")
            else:
                tr = _tr_cache.get(_tr_key[0], tr)
            # **强制指定语言是错的默认。** 实测：越南新闻被强制按 en 识别时，
            # whisper 不是"听错"，而是**把越南语直接意译成英文**（还译错），
            # 于是后面按 en→zh 翻译全崩。用户实际遇到的就是这个。
            # 现在默认 auto：whisper 检出 vi 置信度 0.976，识别正确。
            # 想固定语言用 --lang，不要用 --from 去强制。
            _lang = None if args.detect else (args.lang or None)
            texts = th.transcribe_batch(segs_in, lang=_lang)
            # 仍然喂投票，但只用于**诊断显示**，不再决定路由
            lang_lock.observe(th.last_lang, th.last_lang_prob)
            t_asr = time.time() - t0

            # ── 翻译并行化 ──────────────────────────────────────
            # 实测（_measure_e2e_lag.py，70 秒真实音频）：
            #     产出时刻  音频位置  落后   转写   翻译
            #       9.8      6.0   +3.83  2.58  1.26
            #      16.3     12.0   +4.31  2.78  1.54
            #      47.5     42.0   +5.51  3.77  1.75
            #      66.2     60.0   +6.21  3.89  2.34
            #   → 平均落后 4.8s，其中翻译占 1~2.3s。
            #   原来是串行：转写完 → 才翻译 → 才显示。翻译那段时间机器
            #   完全空着，而下一批音频已经在积压（后半比前半更落后）。
            #   现在把翻译丢给后台队列，主循环立刻回去收下一批音频。
            #   代价：多个译器实例会被同时使用（argos 是纯函数式、Google
            #   是 HTTP，两者都无共享可变状态）；tr 这个变量在批边界被
            #   换掉，线程里用**当前**引用即可。
            _keep = [(segs_in[i], texts[i]) for i in range(len(texts))
                     if texts[i]]
            if _keep:
                _tr_now = tr          # 快照：批边界可能换译器
                _bno = _seq_in[0]
                _seq_in[0] += 1

                def _do_translate(_bno, _keep, _tr):
                    try:
                        _zh = [_tr(t) for (_s, t) in _keep]
                    except Exception as _e:
                        print(f"  翻译线程异常: {type(_e).__name__} {_e}")
                        _zh = ["" for _ in _keep]
                    done_q.put((_bno, _keep, _zh))

                _th_pool.submit(_do_translate, _bno, _keep, _tr_now)
            t_end = time.time() - t0
            per_call = t_asr
            # 顺手把已经译好的批显示掉（不阻塞）
            _n_shown = _drain_translations(block=False)
            # **每批一行诊断。** 原来只打"转写 0.00s"，那个数字同时对应
            # 三种完全不同的故障：噪声门挡了、远端没返回、音频是静音。
            # 把门的门限/拒数、远端返回长度都带上，一眼能分清。
            _g = getattr(th, "gate", None)
            _gt = (getattr(_g, "threshold_dbfs", None) if _g else None)
            _rs = getattr(th, "last_remote_stats", {}) or {}
            print(f"      (批次 {n_batch}：{len(segs_in)} 段 / {batch_dur:.1f}s "
                  f"音频，转写 {t_asr:.2f}s；翻译并行，已显示 {_n_shown} 段)"
                  f"　[门 {('%.0f' % _gt) if _gt is not None else '未校准'}"
                  f" 拒{getattr(_g, 'rejected', 0) if _g else 0}"
                  f" 远端 {_rs.get('txt_len', '-')}字"
                  f" 失{_rs.get('fails', '-')}]")
    except KeyboardInterrupt:
        print("\n中断。")
    finally:
        # 收尾：把后台还没显示的译文全部取出显示（否则最后几批会丢）
        try:
            _deadline = time.time() + 20.0
            while _next_show[0] < _seq_in[0] and time.time() < _deadline:
                if not _drain_one(timeout=0.5):
                    continue
                _show_ready()
        except Exception:
            pass
        _th_pool.shutdown(wait=False)
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

    print("\n  借鉴项：peek / LRU 缓存 / 失败不污染")
    _lk = LanguageLock()
    chk("peek 初始为 None", _lk.peek() is None)
    _lk.observe("vi", 0.97)
    chk("一票还没锁定（min_votes=2）", _lk.peek() is None)
    _snap0 = (_lk.locked, dict(_lk.votes), len(_lk.history))
    _lk.peek()
    chk("peek 不改状态",
        (_lk.locked, dict(_lk.votes), len(_lk.history)) == _snap0)
    _lk.observe("vi", 0.96)
    chk("还要一票（min_votes 已提到 3）", _lk.peek() is None)
    _lk.observe("vi", 0.95)
    chk("三票锁定 vi", _lk.peek() == "vi")
    _snap = (_lk.locked, dict(_lk.votes), len(_lk.history))
    _lk.peek()
    chk("peek 后状态完全没变", (_lk.locked, dict(_lk.votes), len(_lk.history)) == _snap)
    _c = LruTtlCache(max_size=3, ttl=0.4)
    _c.set("a", "甲"); _c.set("b", "乙"); _c.set("c", "丙")
    chk("LRU 命中会挪到最新", _c.get("a") == "甲")
    _c.set("d", "丁")
    chk("超容量淘汰最旧的", _c.get("b") is None and len(_c) == 3)
    _c2 = LruTtlCache(max_size=9, ttl=0.3)
    _c2.set("k", "v")
    chk("TTL 内命中", _c2.get("k") == "v")
    time.sleep(0.35)
    chk("TTL 过期后失效", _c2.get("k") is None)
    chk("Translator 用 LruTtlCache",
        type(Translator("en", "zh").cache).__name__ == "LruTtlCache")
    _tf = Translator("en", "zh")
    _tf.mode = "google-free"; _tf._pivot = None
    _tf._google_free = lambda s: (_ for _ in ()).throw(OSError("x"))
    _o = _tf("anything that fails")
    chk("翻译失败返回空串", _o == "")
    chk("失败不返回 [译错 ...] 占位", "[译错" not in _o and not _o.startswith("["))
    # 真验 sl=auto：拦下发出的请求，看 URL 里到底是什么
    _tf2 = Translator("vi", "zh")
    _seen = {}

    def _cap(url, *a, **k):
        # urlopen 收到的是 Request 对象（不是字符串），要取 full_url
        _seen["url"] = getattr(url, "full_url", None) or str(url)
        raise OSError("captured")          # 截住，别真发出去

    import urllib.request as _ur
    _orig = _ur.urlopen
    _ur.urlopen = _cap
    try:
        _tf2._google_free("xin chào")
    except Exception:
        pass
    finally:
        _ur.urlopen = _orig
    _u = _seen.get("url", "")
    chk("google-free 请求里带 sl=auto", "sl=auto" in _u)
    chk("不再出现 sl=vi（源语言由 Google 判）", "sl=vi" not in _u)
    chk("目标语言仍是 zh-CN", "tl=zh-CN" in _u)
    print("\n二之四、噪声门不能吃掉正常语音（回归「识别不到声音」）")
    # 故障复现：开机时音量正常 → floor 高 → 门限顶到上限，
    # 之后音量一小就全被拒，字幕整场消失。
    import numpy as _np
    def _gate_after_warmup(hi):
        _g = NoiseGate(hi_dbfs=hi)
        _loud = (_np.random.randn(int(0.1 * TARGET_RATE)) * 0.016
                 ).astype(_np.float32)
        for _i in range(70):
            _g.is_speech(_loud)
        return _g
    _g30 = _gate_after_warmup(-30.0)
    _g48 = NoiseGate()                      # 当前默认
    chk("默认上限已放宽到 -48（不再是 -30）", _g48.hi_dbfs <= -48.0)
    chk("开机音量偏大时门限不再顶到 -30",
        _gate_after_warmup(-48.0).threshold_dbfs <= -47.0)
    _speech = (_np.random.randn(64000) * 0.025).astype(_np.float32)
    chk("正常语音（约 -32 dBFS）放行", _g48.is_speech(_speech))
    _sil = (_np.random.randn(64000) * 0.0003).astype(_np.float32)
    chk("真静音（约 -70 dBFS）仍被挡住", not _g48.is_speech(_sil))

    print("\n二之三、源语言路由（去掉锁定后的判据）")
    # 判据：只有高置信英语才走离线 argos；其余交给 Google sl=auto
    for lg, pb, want in (("en", .97, True), ("en", .86, True),
                         ("en", .60, False), ("en", .20, False),
                         ("ru", .99, False), ("vi", .98, False),
                         ("ko", .80, False), (None, .99, False),
                         ("", .99, False), ("EN", .90, True)):
        chk(f"路由 {lg!r}/{pb} -> {want}",
            _pick_offline_source(lg, pb) is want)

    print("\n二之三、语言锁定（针对两个实测问题）")
    # 问题一："缺少中文翻译" —— 翻译器原来用 args.src（"en"）初始化，
    #   越南语被塞进 en→zh 模型。语言锁定后翻译器跟着实际语言走。
    # 问题二："越南语有时被识别成韩语" —— 逐段检测会抖，累计投票后锁住。
    L = LanguageLock(min_votes=2)
    chk("第一段先不定", L.observe("vi", 0.98) is None)
    chk("第二段一致才锁定", L.observe("vi", 0.97) == "vi")
    chk("中间一段误判成韩语，不改锁", L.observe("ko", 0.60) == "vi")
    chk("后续越南语仍锁 vi", L.observe("vi", 0.99) == "vi")
    L2 = LanguageLock()
    for lg, p in (("vi", .98), ("vi", .97), ("vi", .96),
                  ("en", .9), ("en", .9), ("en", .9), ("en", .9)):
        L2.observe(lg, p)
    chk("锁定 vi 后，连续 4 段别的语言还不切", L2.locked == "vi")
    L2.observe("en", .9)
    chk("连续 5 段才允许切", L2.locked == "en")

    print("\n二之四、低置信判定不投票（实测数据驱动）")
    # 依据：45 秒真实越南新闻环回，11 块里所有误判的置信都 <0.7，
    # 所有真实越南语语音都 >=0.688。门槛 0.5→0.7、票数 2→3。
    L4 = LanguageLock()
    # 这些就是实测到的垃圾判定（静音/近静音/复读幻觉）
    for lg, p in (("en", 0.227), ("en", 0.340), ("zh", 0.659),
                  ("zh", 0.552), ("ko", 0.661)):
        L4.observe(lg, p)
    chk("低置信全被拒（一票都没投）", L4.votes == {})
    chk("因此没锁成任何语言", L4.locked is None)
    chk("拒票计数 = 5", L4.rejected_lowconf == 5)
    L4.observe("vi", 0.804); L4.observe("vi", 0.875); L4.observe("vi", 0.965)
    chk("真实越南语三票锁定", L4.locked == "vi")
    chk("低置信不进历史（recent 不被污染）",
        all(x[0] == "vi" for x in L4.history))

    print("\n二之五、复读幻觉拦截")
    for s, want in (("不能理解,怎麼理解,怎麼理解,怎麼理解,怎麼理解,怎麼理解。", True),
                    ("왕코, 왕코, 왕코, 왕코, 왕코, 왕코.", True),
                    ("啊啊啊啊啊啊啊啊", True),
                    ("Còn giá quay hông, thì chỉ bán nhớ nằm b", False),
                    ("广宁省警方刚刚以谋杀罪起诉被告段忠勇。", False),
                    ("谢谢", False),
                    ("对对对", False),
                    ("嗯嗯", False),
                    ("哈哈哈哈", False)):
        chk(f"is_hallucination={want}: {s[:26]}", is_hallucination(s) is want)

    L3 = LanguageLock(min_votes=2, confidence=0.5)
    L3.observe("ko", 0.20); L3.observe("ko", 0.30)
    chk("手动放宽到 0.5 时才投票（说明门槛真的生效）",
        L3.votes == {} and L3.locked is None)

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
    # 开发目录里样本在根下；发布仓库里在 samples/ 下。两处都找。
    wp = next((p for p in (os.path.join(HERE, "e2e_speech.wav"),
                           os.path.join(HERE, "samples", "e2e_speech.wav"))
               if os.path.exists(p)), None)
    if wp:
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
        o.reset(); chk("可重置", o.st["font_size"] == 24)
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
    # ── 远端识别（解决本地 CPU 的 2 秒固定成本）─────────────────
    # 本地实测（_bench_bottleneck.py）：一次 transcribe 固定 ~2.0 秒，
    # 与音频长短无关，参数/线程/compute_type 都动不了它。
    # 远端 qwen3-asr-flash：12 秒音频 0.76s、16 秒 0.88s，且吃得下长音频。
    # **默认开启**：有 key 就用（实测延迟 4.9s → 1.0s），没 key 自动退回
    # 本地 whisper，不影响任何现有行为。
    ap.add_argument("--no-remote-asr", dest="remote_asr",
                    action="store_false",
                    help="禁用远端识别，强制用本地 whisper（默认优先远端）")
    ap.add_argument("--remote-asr", dest="remote_asr", action="store_true",
                    help="用阿里云百炼 qwen3-asr-flash 做识别（默认已开启）")
    ap.set_defaults(remote_asr=True)
    ap.add_argument("--no-widen", action="store_true",
                    help="远端识别时不自动放宽批量上限（默认会放宽到 8s）")
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
