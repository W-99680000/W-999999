# -*- coding: utf-8 -*-
"""验证：点播浮窗是否与回环同观感 —— 字幕滚动保留、间隙不清屏。

做法：把 SMTC 位置源换成一个"随时间递增"的假会话，跑真实 watch_player，
然后读浮窗自报的状态文件 watch_overlay_state.json 的 n（当前屏上条数）。

旧行为（每句 clear + set、间隙 clear）：n 在 0/1 之间跳。
新行为（对齐回环）：n 累积到 max_lines（配置里 3）后被挤出去。
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)

import smtc_tracker as st


class FakeSmtc:
    """假会话：position 按真实时间 1x 递增，永不断流。"""

    def __init__(self, *a, **k):
        self.t0 = time.time()

    def start(self):
        pass

    def position(self):
        return time.time() - self.t0

    def has_session(self):
        return True

    def describe(self):
        return "fake-session"

    def duration(self):
        return None

    def close(self):
        pass


st.available = lambda: True
st.SmtcTracker = FakeSmtc

import watch_player  # noqa: E402

SRT = "pre_cache/有没有懂越南语的大佬，他们在吵什么.zh.srt"
sys.argv = ["watch_player.py", SRT, "--clock"]
print("模拟播放中，1x 推进位置…（外层用 timeout 掐断）", flush=True)
watch_player.main()
