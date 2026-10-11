# -*- coding: utf-8 -*-
"""连续盯屏观察：跑 3 分钟，记录浮窗每一秒的状态。

只解决问题、不做推断：把"屏幕上到底有没有字幕、状态怎么流转"
变成一条时间线。
"""
import io
import json
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, "D:/NEW WORK/work/subtitle")

from PIL import ImageGrab
import numpy as np

HERE = "D:/NEW WORK/work/subtitle"
CFG = os.path.join(HERE, "overlay_qt.json")
DUR = float(sys.argv[1]) if len(sys.argv) > 1 else 180.0


def settings():
    try:
        return json.load(open(CFG, encoding="utf-8"))
    except Exception:
        return {}


def box():
    d = settings()
    x = d.get("x") if d.get("x") is not None else 360
    y = d.get("y") if d.get("y") is not None else 831
    w = int(d.get("width") or 1200)
    fs = int(d.get("font_size") or 24)
    r = float(d.get("original_ratio") or 0.68)
    lh = float(d.get("line_height") or 1.15)
    ln = int(d.get("max_lines") or 3)
    return int(x), int(y), int(w), int((fs + fs * r) * lh * ln + 40)


def hex2rgb(h):
    h = (h or "#B7C4D8").lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


d = settings()
bx = box()
hint_rgb = hex2rgb(d.get("original_text_color"))
print("  box=%s hint=%s dur=%.0fs" % (bx, hint_rgb, DUR))
print()
print("  %7s %9s %8s %8s  %s" % ("t", "stroke", "hint%", "diff", "state"))
print("  " + "-" * 56)

t0 = time.time()
prev = None
rows = []
while time.time() - t0 < DUR:
    img = ImageGrab.grab(bbox=(bx[0], bx[1], bx[0] + bx[2], bx[1] + bx[3]))
    a = np.asarray(img.convert("RGB")).astype(np.int16)
    g = a.mean(axis=2)
    bright = g > 190
    dark = g < 70
    he = (bright[:, 1:] & dark[:, :-1]) | (dark[:, 1:] & bright[:, :-1])
    ve = (bright[1:, :] & dark[:-1, :]) | (dark[1:, :] & bright[:-1, :])
    stroke = (float(he.sum()) + float(ve.sum())) / g.size
    hd = np.abs(a - np.array(hint_rgb, dtype=np.int16)).max(axis=2)
    hint = float((hd < 14).sum()) / g.size
    diff = 0.0 if prev is None else float(np.abs(a - prev).mean())
    prev = a
    if hint > 0.15:
        st = "IDLE-HINT"
    elif stroke > 0.009:
        st = "SUBTITLE"
    elif stroke > 0.006:
        st = "faint"
    else:
        st = "EMPTY"
    rows.append((time.time() - t0, stroke, hint, diff, st))
    time.sleep(1.0)

print()
print("  状态统计：")
for st in ("SUBTITLE", "IDLE-HINT", "faint", "EMPTY"):
    n = sum(1 for r in rows if r[4] == st)
    print("    %-10s %4d 次  %5.1f%%" % (st, n, n / max(1, len(rows)) * 100))

# 状态切换时间线
print()
print("  状态切换时间线：")
last = None
for r in rows:
    if r[4] != last:
        print("    t=%6.1fs  → %s  (stroke=%.4f hint=%.3f%%)"
              % (r[0], r[4], r[1], r[2] * 100))
        last = r[4]

# 连续 EMPTY 的最长时间
worst, cur, start = 0.0, 0.0, None
for r in rows:
    if r[4] == "EMPTY":
        if start is None:
            start = r[0]
        cur = r[0] - start
        worst = max(worst, cur)
    else:
        start, cur = None, 0.0
print()
print("  **连续空屏最长时间 %.1f 秒**" % worst)
subs = sum(1 for i in range(1, len(rows))
           if rows[i][4] == "SUBTITLE" and rows[i - 1][4] != "SUBTITLE")
print("  字幕出现次数 %d　→ %.1f 秒/次" % (subs, DUR / max(1, subs)))
