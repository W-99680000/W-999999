# -*- coding: utf-8 -*-
"""验证：预翻译"边译边长"时，观看端能不能把新增段落热重载上屏。

做法：
  ① 在 pre_cache 放一个只有前 5 条的 srt（模拟"刚译完第一块"）
  ② 用假 SMTC 会话把标题报成它 → watch 匹配加载
  ③ 6 秒后往 srt **追加** 5 条（模拟"后一块译完了"）
  ④ 检查 watch.log 是否出现"字幕增量"（说明热重载生效）
外层用 timeout 掐断。
"""
import os
import sys
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)

TITLE = "增量热重载测试视频"
SRT = os.path.join(HERE, "pre_cache", TITLE + ".zh.srt")


def cue(i, t0, t1, zh, src):
    return f"{i}\n{_ts(t0)} --> {_ts(t1)}\n{zh}\n{src}\n\n"


def _ts(t):
    return (f"00:{int(t)//60:02d}:{int(t)%60:02d},000")


def write(n_from, n_to):
    with open(SRT, "a", encoding="utf-8-sig") as f:
        for i in range(n_from, n_to + 1):
            t0 = (i - 1) * 2.0
            f.write(cue(i, t0, t0 + 1.8,
                        f"第 {i} 句译文", f"source line {i}"))


if os.path.exists(SRT):
    os.remove(SRT)
write(1, 5)                       # ① 第一块：5 条

import smtc_tracker as st         # noqa: E402


class FakeSmtc:
    def __init__(self, *a, **k):
        self.t0 = time.time()

    def start(self):
        pass

    def current_title(self):
        return TITLE

    def playing(self):
        return True

    def position(self):
        return time.time() - self.t0

    def has_session(self):
        return True

    def describe(self):
        return TITLE

    def duration(self):
        return None

    def close(self):
        pass


st.available = lambda: True
st.SmtcTracker = FakeSmtc

import watch_player              # noqa: E402


def grow():
    print("[t=6s] 追加第 6~10 条（模拟后一块译完）", flush=True)
    write(6, 10)


threading.Timer(6.0, grow).start()

print("[t=0s] 启动 watch（假会话标题 = 测试标题）", flush=True)
sys.argv = ["watch_player.py", "--auto"]
watch_player.main()
