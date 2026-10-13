# -*- coding: utf-8 -*-
"""_verify_seek.py —— 进度拖动检测的纯逻辑单测（不需要 winsdk / Qt / 真视频）。

覆盖：
  ① _JumpDetector：正常播放不误报、前拖/后拖都检出、暂停恢复不误报、
     位置为 None 不污染基准、阈值随自然流逝放宽。
  ② SmtcTracker._anchor_jumped：锚点外推判定（播放中/暂停中/无时间戳）。

跑法：python bench/_verify_seek.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from watch_player import _JumpDetector
from smtc_tracker import SmtcTracker

ok = fail = 0


def chk(name, cond):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ok   {name}")
    else:
        fail += 1
        print(f"  FAIL {name}")


print("== _JumpDetector ==")
d = _JumpDetector(threshold=2.0)
t = 1000.0
# 正常播放：每 0.1s 走 0.1s，连喂 50 拍都不该报
jumps = 0
for i in range(50):
    t += 0.1
    jumps += d.feed(10.0 + i * 0.1, now=t)
chk("正常播放 50 拍零误报", jumps == 0)

# 前拖 30s
t += 0.1
chk("前拖 30s 检出", d.feed(15.0 + 30.0, now=t) is True)
# 拖动后正常播放不再报
t += 0.1
chk("拖动后第一拍即重建基准", d.feed(45.1, now=t) is False)

# 后拖 20s
t += 0.1
chk("后拖 20s 检出", d.feed(25.0, now=t) is True)

# 小幅拖动（1.5s，小于阈值）不报 —— 由 bisect 自己换 cue 即可
t += 0.1
chk("1.5s 小拖不误报", d.feed(26.5, now=t) is False)

# 暂停 60s 再恢复：位置没动，drift 大，不该误报
d2 = _JumpDetector(threshold=2.0)
d2.feed(100.0, now=2000.0)
chk("暂停 60s 后同位置不误报", d2.feed(100.0, now=2060.0) is False)

# 位置 None 不污染基准
d3 = _JumpDetector(threshold=2.0)
d3.feed(50.0, now=3000.0)
chk("None 位置不算跳变", d3.feed(None, now=3000.1) is False)
chk("None 之后基准仍在", d3.feed(50.1, now=3000.2) is False)

# reset 后基准清空
d3.reset()
chk("reset 后第一拍不报", d3.feed(999.0, now=3000.3) is False)

print("== SmtcTracker._anchor_jumped ==")
aj = SmtcTracker._anchor_jumped
t0 = datetime(2026, 10, 13, 22, 0, 0, tzinfo=timezone.utc)
# 播放中：旧锚点 (10s, t0)，2s 后新锚点 (12s) → 正常
chk("锚点正常前进不报",
    aj((10.0, t0), (12.0, t0 + timedelta(seconds=2)), True) is False)
# 播放中：2s 后新锚点却在 40s → 前拖
chk("锚点前拖检出",
    aj((10.0, t0), (40.0, t0 + timedelta(seconds=2)), True) is True)
# 播放中：2s 后新锚点回到 3s → 后拖
chk("锚点后拖检出",
    aj((10.0, t0), (3.0, t0 + timedelta(seconds=2)), True) is True)
# 暂停中：位置不变 → 不报
chk("暂停中同位置不报",
    aj((10.0, t0), (10.0, t0 + timedelta(seconds=60)), False) is False)
# 暂停中拖动（暂停态 seek，浏览器也会上报新锚点）→ 检出
chk("暂停中拖动检出",
    aj((10.0, t0), (55.0, t0 + timedelta(seconds=60)), False) is True)
# 无时间戳：只看位置差
chk("无时间戳大跳检出", aj((10.0, None), (40.0, None), True) is True)
chk("无时间戳小差不报", aj((10.0, None), (10.5, None), True) is False)
# naive datetime（没带时区）也能算
chk("naive 时间戳兼容",
    aj((10.0, datetime(2026, 10, 13, 22, 0, 0)),
       (12.0, datetime(2026, 10, 13, 22, 0, 2)), True) is False)
# 边界：None 锚点
chk("空锚点安全", aj(None, (1.0, t0), True) is False
    and aj((1.0, t0), None, True) is False)

print(f"\n合计 {ok} 通过 / {fail} 失败")
sys.exit(0 if fail == 0 else 1)
