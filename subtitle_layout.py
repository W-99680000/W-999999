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

try:                                   # 可选：中文词边界，未装也能跑
    import jieba as _jieba
except Exception:
    _jieba = None

CJK = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
SOFT = "，。！？、；：,.!?;:…—"
SPACE = " \t\u3000"

MAX_CHARS = 24          # 单条/单行的目标汉字数（拉丁系按 0.55 折算）
MAX_DUR = 5.5           # 单条目标时长（秒；Netflix 上限 7s，我们更紧）
MIN_DUR = 1.1

# ── Netflix Timed Text Style Guide 的断行原则（中文同样适用）─────────
# https://partnerhelp.netflixstudios.com/hc/en-us/articles/215758617
#   · 最多 2 行；优先在标点后断
#   · 优先在连词/介词前断
#   · 不要把名词和冠词/形容词拆开、不要拆姓名、不要拆动词和主语
# 中文没有词性标注时用“高频功能词”做近似：行尾/行首避开助词和虚词。
BREAK_AFTER = set("，。！？、；：,.!?;:…—")
NO_BREAK_BEFORE = set("，。！？、；：,.!?;:…—）】」』”’")
BAD_END = set("的了着过吗呢吧啊呀嘛哦嗯和与及或而但就都也还很把被给对在是地得所之其")
BAD_START = set("的了着过吗呢吧啊呀嘛哦嗯和与及或而但就都也还很把被给对在是地得所之其不没要会能可让使")
# 常见动词：右侧是“不X”时多为动补（撑不过/忍不住），不要断在中间
VERB_CHARS = set("撑受忍等看说做拿放走跑睡吃喝想记活过来去起下上出进回开关停住到成变长涨跌买卖用干弄搞整办让使给送带取留剩缺少多够")
# 出现在右侧时优先断（连词/介词/连接副词）
GOOD_START_WORDS = ("但是", "不过", "而且", "因为", "所以", "如果", "虽然",
                    "然后", "只是", "还是", "以及", "并且", "或者", "同时",
                    "因此", "其中", "对于", "关于", "为了", "除了", "随着",
                    "when ", "but ", "and ", "because ", "if ", "although ",
                    "while ", "so ", "or ")


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


_WORD_BOUNDS_CACHE: dict = {}


def _word_bounds(text: str) -> set:
    """jieba 词边界（没有 jieba 就返回空集合，退化到规则）。

    成熟中文断句/断行方案都先做分词，再在词边界切；否则会出现
    “撑 / 不过”这种把词劈开的情况。
    """
    if _jieba is None:
        return set()
    key = (len(text), text)
    got = _WORD_BOUNDS_CACHE.get(key)
    if got is not None:
        return got
    bounds, pos = set(), 0
    try:
        for w in _jieba.cut(text):
            pos += len(w)
            bounds.add(pos)
    except Exception:
        bounds = set()
    _WORD_BOUNDS_CACHE[key] = bounds
    return bounds


def _cut_score(text: str, i: int, limit: int,
               bounds: set | None = None) -> float:
    """给“在 i 处断开”打分；越高越好。"""
    if i <= 0 or i >= len(text):
        return -1e9
    left, right = text[i - 1], text[i]
    score = -abs(i - limit) * 0.35      # 越接近目标长度越好
    if bounds:
        # 有分词结果时，尽量只在词边界切；不在边界（且不是标点）重罚，
        # 避免“就撑 / 不过30年”这种把“撑不过”劈开的切法。
        if i in bounds:
            score += 10
        elif left not in BREAK_AFTER:
            score -= 10
    # 标点后断开（Netflix 首选）
    if left in BREAK_AFTER:
        score += 10
    # 右侧是连词/介词 → 断在它前面
    tail = text[i:i + 8].lower()
    for w in GOOD_START_WORDS:
        if tail.startswith(w):
            score += 6
            break
    # 右/左是空格 = 拉丁词边界
    if right in SPACE or left in SPACE:
        score += 8
    # 不能把标点甩到行首
    if right in NO_BREAK_BEFORE:
        score -= 12
    # 行尾避开助词/虚词（中文近似“不拆词”）
    if left in BAD_END:
        score -= 7
    # 行首避开助词/虚词
    if right in BAD_START:
        score -= 7
    # 动补“撑不过/忍不住”：不要切在动词和不之间
    if right == "不" and left in VERB_CHARS:
        score -= 9
    # 不拆 ASCII 单词/数字
    if left.isascii() and right.isascii() and left.isalnum() and right.isalnum():
        score -= 12
    return score


def _find_cut(text: str, limit: int) -> int:
    """在 text 前 limit 个字符附近找最佳切点，返回切点下标（>0）。

    参考 Netflix 断行规范：优先标点后 / 连词前；行尾避开助词；
    不拆英文单词与数字。比“按字数硬切”更接近人工字幕。
    """
    if len(text) <= limit:
        return len(text)
    lo = max(1, int(limit * 0.5))
    hi = min(limit, len(text) - 1)
    bounds = _word_bounds(text)
    best_i, best_s = limit, -1e9
    step = 1
    for i in range(lo, hi + 1, step):
        sc = _cut_score(text, i, limit, bounds)
        if sc > best_s:
            best_i, best_s = i, sc
    # 若最佳切点行首/行尾很糟（功能词），尝试向右/左让出 1~2 字
    for delta in (1, -1, 2, -2):
        i = best_i + delta
        if 1 <= i < len(text):
            sc = _cut_score(text, i, limit, bounds)
            if sc > best_s + 0.5:
                best_i, best_s = i, sc
    return max(1, best_i)


def split_text_n(text: str, n: int, hard_limit: int | None = None) -> list[str]:
    """把 text 切成 n 段（优先标点/空格/词边界），空段会合并。

    hard_limit：每段尽量不超过的字数。第一段优先用满（更接近人工字幕），
    剩下的再继续切；同时保证每段至少给后面留 4 个字，避免最后剩一个字。
    """
    text = (text or "").strip()
    if n <= 1 or not text:
        return [text] if text else [""]
    parts = []
    remain = text
    for k in range(n, 1, -1):
        if not remain:
            parts.append("")
            continue
        balanced = max(1, int(math.ceil(len(remain) / k)))
        if hard_limit and k > 1:
            room = max(1, len(remain) - (k - 1) * 4)
            limit = min(hard_limit, room)
            limit = max(balanced, limit)
        else:
            limit = balanced
        limit = max(1, min(limit, len(remain)))
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

    # 中文 24 个字；拉丁系按视觉宽折算成字符数（约 42~44）
    def _hard(t):
        cjk = sum(1 for ch in t if CJK.match(ch))
        ratio = cjk / max(1, len(t))
        return max(10, int(round(max_chars / 0.55))) if ratio < 0.3 \
            else int(round(max_chars))

    zh_parts = split_text_n(zh, n, _hard(zh)) if zh else [""] * n
    src_parts = split_text_n(src, n, _hard(src)) if src else [""] * n
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
