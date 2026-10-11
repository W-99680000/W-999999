# -*- coding: utf-8 -*-
"""眼睛 v3 —— 自适应定位，不再依赖设置文件里的坐标。

前两版失败的原因（都是靠截图才发现的）：
  ① v1 用"亮像素占比"，把背后浏览器窗口的亮色也算成字幕（25%）。
  ② v2 用设置文件里的 x/y/w/h 裁剪，但**那是 Qt 逻辑坐标**，
     而截屏是物理像素。系统缩放 125% 时实测：
         程序自报 360,831 1200x165
         Win32 实测 288,734  960x77
     位置和尺寸都不一样 → 我一直在裁错的区域，所以"看不见"字幕。

v3 的做法：**全屏截图，自己找字幕条**。
    字幕特征：靠近屏幕底部、水平一条、含大量"亮字 + 暗描边"的强反差笔画。
    直接在整幅物理像素图里搜，不依赖任何坐标换算。

判据（只认字幕，不认视频画面）：
  逐行统计"亮像素紧邻暗像素"的跳变数 → 得到每行的笔画数曲线。
  字幕行的笔画数会明显高于周围。取曲线峰值区域作为字幕带。
"""
import io
import os
import sys
import json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from PIL import ImageGrab
import numpy as np

HERE = "D:/NEW WORK/work/subtitle"


def settings():
    try:
        return json.load(open(os.path.join(HERE, "overlay_qt.json"),
                              encoding="utf-8"))
    except Exception:
        return {}


def stroke_rows(a):
    """逐行算笔画数（亮↔暗紧邻跳变）。返回长度 = 高度的数组。"""
    g = a.mean(axis=2)
    bright = g > 185
    dark = g < 75
    h_edge = (bright[:, 1:] & dark[:, :-1]) | (dark[:, 1:] & bright[:, :-1])
    v_edge = (bright[1:, :] & dark[:-1, :]) | (dark[1:, :] & bright[:-1, :])
    rows = h_edge.sum(axis=1)
    rows2 = np.zeros(len(g))
    rows2[:-1] += v_edge.sum(axis=1)
    return rows + rows2


def locate(img):
    """在整幅图里找字幕带。返回 (y0, y1, 笔画总量) 或 None。"""
    a = np.asarray(img.convert("RGB")).astype(np.int16)
    h, w, _ = a.shape
    rows = stroke_rows(a)
    # 只看下三分之二（字幕通常在下方；不看顶部浏览器标签栏）
    lo = int(h * 0.55)
    band = rows[lo:]
    if band.max() < 8:
        return None
    thr = max(8.0, band.max() * 0.30)
    idx = np.where(band >= thr)[0]
    if len(idx) == 0:
        return None
    y0, y1 = lo + int(idx[0]), lo + int(idx[-1]) + 1
    return y0, y1, float(band.sum())


if __name__ == "__main__":
    d = settings()
    print("  设置文件坐标（Qt 逻辑，仅参考）: x=%s y=%s w=%s"
          % (d.get("x"), d.get("y"), d.get("width")))
    print()
    print("  %5s %10s %10s   %s" % ("帧", "笔画总量", "字幕带", "判断"))
    print("  " + "-" * 54)
    import time
    for i in range(1, 9):
        img = ImageGrab.grab()
        a = np.asarray(img.convert("RGB")).astype(np.int16)
        rows = stroke_rows(a)
        h = a.shape[0]
        lo = int(h * 0.55)
        band = rows[lo:]
        loc = locate(img)
        if loc is None:
            verdict = "未找到字幕带"
            bandtxt = "-"
            tot = float(band.sum())
        else:
            y0, y1, tot = loc
            verdict = "**有字幕**" if tot > 60 else "有内容（弱）"
            bandtxt = "y=%d~%d (%dpx)" % (y0, y1, y1 - y0)
        print("  %5d %10.0f %10s   %s" % (i, tot, bandtxt, verdict))
        if i == 3:
            img.crop((0, lo, a.shape[1], h)).save(
                os.path.join(HERE, "band_shot.png"))
        time.sleep(1.2)
    print()
    print("  已存一张带子图 %s" % os.path.join(HERE, "band_shot.png"))
