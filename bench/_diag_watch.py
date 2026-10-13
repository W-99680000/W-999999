# -*- coding: utf-8 -*-
"""诊断：点播字幕链路到底断在哪一环。

逐层验证：srt 解析 → 标题匹配 → 浮窗渲染 → SMTC 位置。
不依赖真实播放，能跑的层先跑，跑不了的层打印清晰提示。
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(HERE)

from watch_player import parse_srt, _find_cached_srt, _norm_title

print("=" * 60)
print("① srt 解析")
SRT = "pre_cache/有没有懂越南语的大佬，他们在吵什么.zh.srt"
cues = parse_srt(SRT)
print(f"  条数 {len(cues)}")
print(f"  首条 zh={cues[0][2][:24]!r}")
print(f"  首条 src={cues[0][3][:30]!r}")
assert len(cues) == 27, len(cues)
assert cues[0][2].startswith("你这么嫩"), cues[0]
assert cues[0][3].startswith("Mày"), cues[0]

print("=" * 60)
print("② 标题匹配（用 B 站 SMTC 真实标题形态）")
for t in ("有没有懂越南语的大佬，他们在吵什么_哔哩哔哩_bilibili",
          "有没有懂越南语的大佬，他们在吵什么",
          "【朝鲜各地】几内亚诗人激赞白头山"):
    hit = _find_cached_srt(t)
    print(f"  {t[:36]:38s} -> {os.path.basename(hit) if hit else None}")
assert _find_cached_srt("有没有懂越南语的大佬，他们在吵什么_哔哩哔哩_bilibili")

print("=" * 60)
print("③ 浮窗渲染（离屏）")
from watch_overlay import QtOverlay
ov = QtOverlay()
print(f"  窗口可见={ov.w.isVisible()} 尺寸={ov.w.width()}x{ov.w.height()}")
ov.clear()
_st, _en, zh, src = cues[0]
ov.set(src, zh, hold=3600)
ov.pump()
n = len(ov.entries)
print(f"  set 后 entries={n}")
if n:
    lb = ov.entries[0][3]
    print(f"  label 文本长度={len(lb.text())} 高度={lb.height()}")
    print(f"  label 文本前 80 字：{lb.text()[:80]!r}")
print(f"  窗口尺寸（set 后）={ov.w.width()}x{ov.w.height()}")

print("=" * 60)
print("④ SMTC 位置源")
from smtc_tracker import SmtcTracker, available
print(f"  winsdk 可用={available()}")
if available():
    t = SmtcTracker()
    t.start()
    import time
    time.sleep(1.0)
    print(f"  会话标题={t.current_title()!r}")
    print(f"  播放状态={t.playing()}  位置={t.position()}")
    t.close()
print("=" * 60)
print("DIAG DONE")
