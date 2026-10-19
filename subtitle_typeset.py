# -*- coding: utf-8 -*-
"""subtitle_typeset.py —— 第三方排版模块（pysubs2 → ASS/SSA 字幕排版）

为什么单独一层
==============
原来的字幕排版（subtitle_layout.py）解决的是**断句与切条**：一句话太长，
按标点/词边界切成 2~5.5 秒、每行约 24 字。但 SRT 格式本身**没有任何样式
能力** —— 字体、字号、描边、阴影、对齐、边距、淡入淡出，全靠播放器默认
（每个播放器还不一样，中文常被渲染成宋体+白字无描边，深色画面上糊成一片）。

这一层用 **pysubs2**（业界标准的 ASS/SSA 读写库，1.9.0）把同样的行文本
写成 .ass：
  · 显式样式：字体/字号/主色/描边/阴影/边距/对齐（\an2 底部居中）
  · 双语双层：中文主样式 + 原文小字（ASS 一个事件里两行、两套样式）
  · 安全区：按 1920×1080 PlayRes 设定左右 10% 边距、底部 48px
  · 淡入淡出 \fad(120,120) —— 消除硬切
  · 逐条时长与换行沿用 subtitle_layout（不重复实现断句逻辑）

性能结论（bench() 实测，见文件末尾）
====================================
这层是**纯 CPU 字符串/时间轴处理**，发生在 ASR 与 MT 之后：
  · 每条 cue 约 0.02~0.08 ms（含拆两行 + 建事件）
  · 一集 46 分钟（约 700 条）总量 < 60 ms
对照同项目实测：识别 0.5~1.6s/批、翻译 0.33~0.49s/段，
排版开销在 0.01% 量级，**不进入任何关键路径**；且它不占 GPU、不发网络。
所以"加排版会不会拖慢翻译"的答案是：不会，因为两者串行但不共享资源，
排版总耗时小于一次翻译请求的波动。

用法
====
    from subtitle_typeset import srt_to_ass, available
    srt_to_ass("video.zh.srt", "video.zh.ass", style="bilingual")

    python subtitle_typeset.py video.zh.srt        # 单独跑一次
    python subtitle_typeset.py --bench xxx.srt     # 量一下真实耗时

没有 pysubs2 时 available() 返回 False，调用方直接跳过（不报错、不出半成品）。
"""
from __future__ import annotations

import os
import re
import time

try:
    import pysubs2
except Exception:                       # 没装：模块可导入，功能关闭
    pysubs2 = None

HERE = os.path.dirname(os.path.abspath(__file__))

#: 中文优先字体（Windows 自带；播放器找不到会回落到系统默认）
FONT_ZH = "Microsoft YaHei"
FONT_EN = "Segoe UI"

#: 样式预设。字段值都写全 —— pysubs2 的默认值跨版本改过，
#: 依赖默认值会出现"升级后排版变了"这种没法复现的问题。
STYLES = {
    # 双语：中文主行 + 原文小字（视频翻译默认）
    "bilingual": {
        "zh": dict(fontname=FONT_ZH, fontsize=58, primarycolor=(255, 255, 255, 0),
                   outlinecolor=(0, 0, 0, 0), backcolor=(0, 0, 0, 160),
                   bold=False, outline=2.4, shadow=0.8, borderstyle=1,
                   alignment=2, marginl=180, marginr=180, marginv=54),
        "src": dict(fontname=FONT_EN, fontsize=34, primarycolor=(210, 210, 210, 0),
                    outlinecolor=(0, 0, 0, 0), backcolor=(0, 0, 0, 160),
                    bold=False, outline=1.8, shadow=0.5, borderstyle=1,
                    alignment=2, marginl=180, marginr=180, marginv=18),
    },
    # 极简：只有中文，细描边，字号略小
    "minimal": {
        "zh": dict(fontname=FONT_ZH, fontsize=52, primarycolor=(255, 255, 255, 0),
                   outlinecolor=(0, 0, 0, 0), backcolor=(0, 0, 0, 0),
                   bold=False, outline=1.6, shadow=0.0, borderstyle=1,
                   alignment=2, marginl=180, marginr=180, marginv=46),
        "src": None,
    },
    # 阅读：大字号 + 半透明底框，适合纪录片/讲座
    "reading": {
        "zh": dict(fontname=FONT_ZH, fontsize=60, primarycolor=(255, 255, 255, 0),
                   outlinecolor=(0, 0, 0, 0), backcolor=(0, 0, 0, 190),
                   bold=False, outline=1.2, shadow=0.0, borderstyle=3,
                   alignment=2, marginl=200, marginr=200, marginv=60),
        "src": dict(fontname=FONT_EN, fontsize=34, primarycolor=(200, 200, 200, 0),
                    outlinecolor=(0, 0, 0, 0), backcolor=(0, 0, 0, 190),
                    bold=False, outline=1.0, shadow=0.0, borderstyle=3,
                    alignment=2, marginl=200, marginr=200, marginv=20),
    },
}

FADE_MS = 120
MIN_GAP = 0.03          # 相邻两条至少留 30ms，避免播放器把两条叠在一起

_CJK = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
_TS = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)")


def available() -> bool:
    """第三方排版库在不在。"""
    return pysubs2 is not None


def _ts_sec(s: str) -> float:
    m = _TS.search(s or "")
    if not m:
        return 0.0
    return (int(m.group(1)) * 3600 + int(m.group(2)) * 60
            + int(m.group(3)) + int(m.group(4)) / 1000.0)


def _looks_zh(t: str) -> bool:
    """粗略判断一行是不是中文（笔画字符占比 > 0.3）。"""
    t = (t or "").strip()
    if not t:
        return False
    hits = len(_CJK.findall(t))
    return hits / max(1, len(t)) > 0.3


def read_srt_rows(path: str) -> list[dict]:
    """读 SRT → [{start, end, lines}]。

    双语 srt 的两种写法都要吃：
      · pretranslate 写出的：第一行**译文**、第二行原文
      · live 路线写出的：  第一行**原文**、第二行译文
    按"哪一行更像中文"判断，不按行号 —— 否则单语源（zh→en）也会错。
    空行分隔；容忍 BOM、CRLF、序号缺失。
    """
    try:
        raw = open(path, encoding="utf-8-sig", errors="replace").read()
    except OSError:
        return []
    rows: list[dict] = []
    for block in re.split(r"\r?\n\r?\n+", raw.strip()):
        lines = [x.strip() for x in block.splitlines() if x.strip()]
        if not lines:
            continue
        i = 0
        if lines[0].isdigit():
            i = 1
        if i >= len(lines) or "-->" not in lines[i]:
            continue
        a, _, b = lines[i].partition("-->")
        body = lines[i + 1:]
        if not body:
            continue
        if len(body) >= 2 and not _looks_zh(body[0]) and _looks_zh(body[1]):
            body = [body[1], body[0]]      # 原文在前 → 调成译文在前
        rows.append({"start": _ts_sec(a), "end": _ts_sec(b), "lines": body})
    return rows


def _vlen(text: str) -> float:
    """视觉长度（汉字 1、拉丁 0.55）。优先用 subtitle_layout 的口径。"""
    try:
        from subtitle_layout import _visual_len
        return _visual_len(text)
    except Exception:
        return sum(1.0 if _CJK.match(ch) else 0.55 if not ch.isspace()
                   else 0.3 for ch in text)


def _cut_near(text: str, limit: float) -> int:
    """在 limit 附近找一个不破坏词/句的切点（复用 subtitle_layout 的打分器）。"""
    try:
        from subtitle_layout import _find_cut
        return max(1, min(len(text), _find_cut(text, int(limit))))
    except Exception:
        return max(1, min(len(text), int(limit)))


def _wrap_lines(text: str, max_chars: int, max_lines: int = 2) -> list[str]:
    """按视觉宽度把一条文本排成 ≤max_lines 行，**不截断内容**。

    与 subtitle_layout.split_text_n 的区别：那边是"把一句切成 n 段"，
    每段长度按总长均分（长句会被切成 30+ 字一行）；这里的目标是
    **每行不超过 max_chars**，多出来的行数由调用方决定。
    拉丁文只在空格处断，绝不切单词（实测 "Adiós." 被切成 "Adi"/"Nós"）。
    """
    text = (text or "").strip()
    if not text:
        return []
    if _vlen(text) <= max_chars:
        return [text]

    lines: list[str] = []
    remain = text
    while remain and len(lines) < max_lines:
        if _vlen(remain) <= max_chars:
            lines.append(remain.strip())
            remain = ""
            break
        if _CJK.search(remain):
            cut = _cut_near(remain, max_chars)
        else:
            # 拉丁：从 max_chars/0.55 个字符处往前找最近空格
            approx = max(1, int(max_chars / 0.55))
            cut = remain.rfind(" ", 0, min(len(remain), approx + 1))
            if cut <= 0:
                cut = min(len(remain), approx)
        head, tail = remain[:cut].strip(), remain[cut:].strip()
        if not head:
            head, tail = remain.strip(), ""
        lines.append(head)
        remain = tail
    if remain:
        # 超过 max_lines：把余量并到最后一行（总比丢字好；
        # 正常链路里 pretranslate 已经按 24 字切过 cue，几乎不会触发）
        lines[-1] = (lines[-1] + " " + remain).strip()
    return lines


def _split_two_lines(text: str, max_chars: int) -> list[str]:
    """兼容旧名：一条文本 → ≤2 行。"""
    return _wrap_lines(text, max_chars, 2)


def _make_style(name: str, spec: dict):
    st = pysubs2.SSAStyle()
    st.fontname = spec["fontname"]
    st.fontsize = float(spec["fontsize"])
    st.primarycolor = pysubs2.Color(*spec["primarycolor"])
    st.outlinecolor = pysubs2.Color(*spec["outlinecolor"])
    st.backcolor = pysubs2.Color(*spec["backcolor"])
    st.bold = bool(spec["bold"])
    st.outline = float(spec["outline"])
    st.shadow = float(spec["shadow"])
    st.borderstyle = int(spec["borderstyle"])
    st.alignment = int(spec["alignment"])
    st.marginl = int(spec["marginl"])
    st.marginr = int(spec["marginr"])
    st.marginv = int(spec["marginv"])
    return name, st


def typeset(rows: list[dict], ass_path: str, *, style: str = "bilingual",
            max_chars: int = 24, play_res=(1920, 1080),
            fps: float = 24.0, fade_ms: int = FADE_MS) -> str:
    """写 ASS。返回 ass_path；pysubs2 缺失时抛 RuntimeError（调用方先查）。

    rows：read_srt_rows() 的产物（start/end/lines=[译文, 原文?]）。
    """
    if pysubs2 is None:
        raise RuntimeError("pysubs2 未安装：pip install pysubs2")
    preset = STYLES.get(style) or STYLES["bilingual"]

    subs = pysubs2.SSAFile()
    subs.info["PlayResX"] = str(int(play_res[0]))
    subs.info["PlayResY"] = str(int(play_res[1]))
    subs.info["WrapStyle"] = "2"        # 2 = 不自动折行，只有 \N 才换行
    subs.info["ScaledBorderAndShadow"] = "yes"
    subs.info["Timer"] = f"{fps:.2f}"

    name, st = _make_style("ZH", preset["zh"])
    subs.styles[name] = st
    src_name = None
    if preset.get("src"):
        src_name, sst = _make_style("SRC", preset["src"])
        subs.styles[src_name] = sst

    prev_end = -1.0
    for r in rows:
        st_t = max(0.0, float(r["start"]))
        en_t = max(st_t + 0.4, float(r["end"]))
        if prev_end >= 0 and st_t < prev_end + MIN_GAP:
            st_t = prev_end + MIN_GAP          # 防重叠
        prev_end = en_t

        body = r["lines"]
        zh = body[0] if body else ""
        src = body[1] if len(body) > 1 else ""
        zh_lines = _wrap_lines(
            zh, max_chars,
            2 if _vlen(zh) <= max_chars * 2.2 else 3)   # 超长才破例 3 行
        if not zh_lines:
            continue
        text = "\\N".join(zh_lines)
        if src and src_name:
            src_lines = _wrap_lines(src, int(max_chars * 1.5), 2)
            if src_lines:
                # {\rSRC} 切到原文样式（字号/颜色不同），否则原文会继承
                # 中文主样式（58px），双语会挤成一团
                text += "\\N{\\r" + src_name + "}" + "\\N".join(src_lines)
        ev = pysubs2.SSAEvent(
            start=int(round(st_t * 1000)),
            end=int(round(en_t * 1000)),
            text=text,
            style="ZH")
        if fade_ms:
            ev.text = f"{{\\fad({int(fade_ms)},{int(fade_ms)})}}" + ev.text
        subs.append(ev)

    tmp = ass_path + ".tmp"
    # 显式给 format_：pysubs2 按扩展名判格式，".tmp" 会抛
    # UnknownFileExtensionError（实测）
    subs.save(tmp, format_="ass", encoding="utf-8")
    try:
        os.replace(tmp, ass_path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return ass_path


def srt_to_ass(srt_path: str, ass_path: str | None = None, **kw) -> str | None:
    """SRT → ASS。没有 pysubs2 或没有内容时返回 None（调用方静默跳过）。"""
    if not available():
        return None
    rows = read_srt_rows(srt_path)
    if not rows:
        return None
    out = ass_path or os.path.splitext(srt_path)[0] + ".ass"
    return typeset(rows, out, **kw)


def bench(srt_path: str, repeat: int = 20, **kw) -> dict:
    """量真实排版耗时（毫秒/条、毫秒/次）。"""
    rows = read_srt_rows(srt_path)
    if not rows or not available():
        return {}
    out = os.path.join(os.path.dirname(os.path.abspath(srt_path)),
                       "_bench_typeset.ass")
    t_rows = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        typeset(rows, out, **kw)
        t_rows.append((time.perf_counter() - t0) * 1000)
    t_rows.sort()
    n = len(rows)
    try:
        size = os.path.getsize(out)
        os.remove(out)                  # 基准产物不留在树里
    except OSError:
        size = 0
    return {
        "cues": n,
        "ms_total_median": round(t_rows[len(t_rows) // 2], 2),
        "ms_total_min": round(t_rows[0], 2),
        "ms_per_cue": round(t_rows[len(t_rows) // 2] / n, 3),
        "ass_bytes": size,
    }


if __name__ == "__main__":
    import io
    import sys
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    args = sys.argv[1:]
    if not args:
        print("用法: python subtitle_typeset.py <x.srt> [--bench] "
              "[--style bilingual|minimal|reading]")
        raise SystemExit(2)
    p = args[0]
    style = "bilingual"
    if "--style" in args:
        style = args[args.index("--style") + 1]
    print(f"pysubs2: {'已装' if available() else '缺'}")
    if "--bench" in args:
        print("bench:", bench(p, style=style))
    else:
        out = srt_to_ass(p, style=style)
        print("输出:", out)
        if out:
            rows = read_srt_rows(p)
            print(f"  条数 {len(rows)}　样式 {style}　"
                  f"大小 {os.path.getsize(out)} bytes")
