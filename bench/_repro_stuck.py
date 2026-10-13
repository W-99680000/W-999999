# -*- coding: utf-8 -*-
"""复现：SMTC 标题瞬时为空 → 浮窗被状态提示清屏 + 留屏 3600s，字幕不再回来。

现象（用户报的"没有内容 / 浮窗炸了"）：
  ① 标题一空，_status() 就 clear() + flash(msg, 3600)
  ② 提示条 hold=3600 赖在屏上，还把真字幕顶掉
  ③ 清屏后 cur["idx"] 没变，_show() 直接 return → 字幕永远不回来

做法：假 SMTC 会话，标题在 t=4.0~5.5s 之间报空（模拟 winsdk 读元数据
未就绪），其余时间正常；位置按真实时间 1x 走。监控浮窗 entries。
"""
import os
import sys
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)

TITLE = "卡死复现测试视频"
SRT = os.path.join(HERE, "pre_cache", TITLE + ".zh.srt")


def _ts(t):
    return f"00:{int(t)//60:02d}:{int(t)%60:02d},{int(t % 1 * 1000):03d}"


# 3 秒一条的长句：闪烁的 1.5s 内 cue 序号不会变（这正是卡死的条件）
with open(SRT, "w", encoding="utf-8-sig") as f:
    for i in range(1, 15):
        t0 = (i - 1) * 3.0
        f.write(f"{i}\n{_ts(t0)} --> {_ts(t0 + 2.8)}\n"
                f"第 {i} 句中文译文\nsource line number {i}\n\n")

import smtc_tracker as st          # noqa: E402

T0 = time.time()
FLICKER = (4.0, 5.5)               # 标题报空的窗口


class FakeSmtc:
    def __init__(self, *a, **k):
        pass

    def start(self):
        pass

    def _t(self):
        return time.time() - T0

    def current_title(self):
        a, b = FLICKER
        return "" if a <= self._t() <= b else TITLE

    def playing(self):
        return True

    def position(self):
        return self._t()

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

import watch_overlay               # noqa: E402
import watch_player                # noqa: E402

# 抓浮窗实例，好在外面看屏幕上到底有什么
_insts = []
_orig_init = watch_overlay.QtOverlay.__init__


def _init(self, *a, **k):
    _orig_init(self, *a, **k)
    _insts.append(self)


watch_overlay.QtOverlay.__init__ = _init


def snap(tag):
    if not _insts:
        print(f"[{tag}] 还没有浮窗", flush=True)
        return
    ov = _insts[0]
    rows = []
    for e in ov.entries:
        en = (e[1] or "")[:22]
        zh = (e[2] or "")[:22]
        kind = ("idle" if e[1] == ov.IDLE_HINT
                else ("hint" if not e[1] else "sub"))
        rows.append(f"{kind}:{en}|{zh}")
    print(f"[{tag}] entries={rows}", flush=True)


def mon():
    for tag, t in (("t=3s 播放中", 3.0), ("t=6s 闪烁刚结束", 6.0),
                   ("t=9s 之后", 9.0), ("t=13s 之后", 13.0)):
        while time.time() - T0 < t:
            time.sleep(0.05)
        snap(tag)


threading.Thread(target=mon, daemon=True).start()
sys.argv = ["watch_player.py", "--auto", "--no-pretranslate"]
watch_player.main()
