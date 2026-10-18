# -*- coding: utf-8 -*-
"""subtitle_layout.py —— 字幕断行/切条（视频翻译显示用）。

为什么需要：
  预翻译/批识别的 cue 经常是 8~16 秒、40~55 个字一条；SRT 播放器或浮窗
  只能按像素硬折行，中文会被从词中间劈开（“断字”），整体也很难看。

这里做两件事：
  1. 按软标点 / 空格 / 字符边界，把过长的文本切成最多约 24 个汉字/行；
  2. 长 cue 按比例切成多条短 cue，每条 2~5.5 秒，浮窗按播放位置逐条显示。

只用于显示端（watch_player.parse_srt）与输出端（pretranslate 可选），
不改变已有 SRT 文件。
"""
from __future__ import annotations

import math
import re

CJK = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
SOFT = "，。！？、；：,.!?;:…—"
SPACE = " \t\u3000"

MAX_CHARS = 24          # 单条/单行的目标汉字数（拉丁系按 0.55 折算）
MAX_DUR = 5.5           # 单条目标时长（秒）
MIN_DUR = 1.1


def _visual_len(t: str) -> int:
    """汉字算 1，拉丁/数字算 0.55（粗估）。"""
    n = 0.0
    for ch in t:
        if CJK.match(ch) or ch in SOFT:
            n += 1.0
        elif ch.isspace():
            n += 0.3
        else:
            n += 0.55
    return n


def _find_cut(text: str, limit: int) -> int:
    """在 text 前 limit 个字符附近找最佳切点，返回切点下标（>0）。"""
    if len(text) <= limit:
        return len(text)
    lo = max(1, int(limit * 0.55))
    best = 0
    # ① 标点后
    for i in range(min(limit, len(text) - 1), lo - 1, -1):
        if text[i] in SOFT:
            best = i + 1
            break
    if best:
        return best
    # ② 空格处（拉丁词边界）
    for i in range(min(limit, len(text) - 1), lo - 1, -1):
        if text[i] in SPACE:
            best = i
            break
    if best:
        return best
    # ③ 硬切；如果切在 ASCII 单词/数字中间，往左挪到词边界
    best = limit
    # 只保护 ASCII 单词/数字；中文汉字 .isalnum() 也是 True，别误伤
    while 0 < best < len(text) and \
            text[best - 1].isascii() and text[best].isascii() and \
            text[best - 1].isalnum() and text[best].isalnum():
        best -= 1
    return max(1, best)


def split_text_n(text: str, n: int) -> list[str]:
    """把 text 均分成 n 段（优先标点/空格边界），空段会合并。"""
    text = (text or "").strip()
    if n <= 1 or not text:
        return [text] if text else [""]
    parts = []
    remain = text
    for k in range(n, 1, -1):
        if not remain:
            parts.append("")
            continue
        limit = max(1, int(math.ceil(len(remain) / k)))
        cut = _find_cut(remain, limit)
        parts.append(remain[:cut].strip())
        remain = remain[cut:].lstrip()
    parts.append(remain.strip())
    # 合并过短碎片（1 个字符）到相邻段，避免“的”“了”单独一闪
    out = []
    for p in parts:
        if p and out and len(p) <= 1:
            out[-1] += p
        else:
            out.append(p)
    return [p for p in out if p] or [text]


def split_cue(start: float, end: float, zh: str, src: str,
              max_chars: float = MAX_CHARS,
              max_dur: float = MAX_DUR) -> list[tuple]:
    """把一条 (start,end,zh,src) 切成若干条短 cue。

    zh 与 src 分别按比例切；时间按 src 的字数比例摊（两边语言长度不同，
    但同一句的语义位置大致同比例）。
    """
    zh = (zh or "").strip()
    src = (src or "").strip()
    dur = max(0.0, float(end) - float(start))
    if not zh and not src:
        return [(start, end, zh, src)]
    # 需要切几段：文本长度 / 时长
    n_len = max(
        int(math.ceil(_visual_len(zh) / max_chars)) if zh else 1,
        int(math.ceil(_visual_len(src) / max_chars)) if src else 1,
    )
    n_dur = int(math.ceil(dur / max_dur)) if max_dur > 0 else 1
    n = max(1, n_len, n_dur)
    if n <= 1:
        return [(start, end, zh, src)]

    zh_parts = split_text_n(zh, n) if zh else [""] * n
    src_parts = split_text_n(src, n) if src else [""] * n
    # 对齐段数（取较多者，短的一侧末尾补空）
    m = max(len(zh_parts), len(src_parts))
    zh_parts += [""] * (m - len(zh_parts))
    src_parts += [""] * (m - len(src_parts))

    # 时间按 src（没有 src 用 zh）的字符数比例摊
    weights = [max(1, len(x)) for x in (src_parts if any(src_parts)
                                        else zh_parts)]
    total_w = sum(weights) or m
    out = []
    t = float(start)
    acc = 0
    for i, (z, s) in enumerate(zip(zh_parts, src_parts)):
        acc += weights[i]
        tend = start + dur * acc / total_w
        if i == m - 1:
            tend = float(end)
        if tend - t < MIN_DUR and i < m - 1:
            tend = t + MIN_DUR
        out.append((t, min(tend, float(end)), z.strip(), s.strip()))
        t = tend
    return [(a, b, z, s) for a, b, z, s in out if (z or s)]
