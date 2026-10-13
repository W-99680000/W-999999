# -*- coding: utf-8 -*-
"""真实平台冒烟：QtOverlay 能不能真的显示、渲染、写状态。

与 _diag_watch.py 的区别：那个用 offscreen（测不出"屏幕上有没有"），
这个用真实平台，起来后 set 一条字幕，等 600ms 再检查：
  · 窗口 isVisible / geo
  · 标签实际高度（0 就是没渲染出来）
  · overlay_state.json 有没有被写（浮窗自报"我显示了"）
"""
import os
import sys
import json
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)

from PyQt5.QtCore import QTimer
from watch_overlay import QtOverlay, STATE

# 清掉旧状态，好判断这次有没有真的写
if os.path.exists(STATE):
    os.remove(STATE)

ov = QtOverlay()
print(f"[构造] visible={ov.w.isVisible()} geo={ov.geo} "
      f"plat={ov.app.platformName()}", flush=True)

ov.clear()
ov.set("Mày tơ hơn thế hả em?", "你这么嫩啊，妹妹？", hold=3600)
ov.pump()
print(f"[set] entries={len(ov.entries)} "
      f"win={ov.w.width()}x{ov.w.height()}", flush=True)


def check():
    print(f"[600ms] visible={ov.w.isVisible()} "
          f"geo={ov.geo} entries={len(ov.entries)}", flush=True)
    if ov.entries:
        lb = ov.entries[0][3]
        print(f"        label h={lb.height()} text[:60]={lb.text()[:60]!r}",
              flush=True)
    ok_state = os.path.exists(STATE)
    print(f"[状态文件] 存在={ok_state}", flush=True)
    if ok_state:
        print("        " + json.dumps(
            json.load(open(STATE, encoding="utf-8")),
            ensure_ascii=False)[:220], flush=True)
    ov.close()
    ov.app.quit()


QTimer.singleShot(600, check)
ov.app.exec_()
print("[结束]", flush=True)
