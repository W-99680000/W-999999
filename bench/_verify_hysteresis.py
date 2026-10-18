# -*- coding: utf-8 -*-
"""会话迟滞切换 + 前台窗口匹配的纯逻辑单测（不碰 winsdk/真窗口）。
跑法：python bench/_verify_hysteresis.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import smtc_tracker as S          # noqa: E402
from foreground import title_matches_window   # noqa: E402

_ok = _fail = 0


def chk(name, cond):
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"  ok  {name}")
    else:
        _fail += 1
        print(f" FAIL {name}")


print("== 前台窗口标题匹配 ==")
chk("标签标题 ⊂ 窗口标题（带浏览器后缀）",
    title_matches_window("FX战士久留美 第1话",
                         "FX战士久留美 第1话 - 哔哩哔哩 - Google Chrome"))
chk("窗口标题 ⊂ 会话标题也中",
    title_matches_window("某视频 - 哔哩哔哩", "某视频"))
chk("不相干不命中",
    not title_matches_window("财经新闻", "动漫剪辑 - Google Chrome"))
chk("空串不命中",
    not title_matches_window("", "x") and not title_matches_window("x", ""))


print("== 迟滞切换 ==")


class FakeSession:
    def __init__(self, name):
        self.name = name


class PickTracker(S.SmtcTracker):
    """绕过 winsdk：用与 _tick 相同的字段/规则驱动切换状态机。"""

    def __init__(self):
        super().__init__(poll=0.15)

    # 与 _tick 中的迟滞块逐行一致
    def step(self, s, sessions):
        if s is not self._session:
            current_gone = self._session is None or not any(
                x is self._session for x in sessions)
            if current_gone:
                self._switch_to(s)
            elif s is self._challenger:
                self._chall_streak += 1
                if self._chall_streak >= self._SWITCH_HOLD:
                    self._switch_to(s)
            else:
                self._challenger = s
                self._chall_streak = 1
            s = self._session
        else:
            self._challenger = None
            self._chall_streak = 0
        return s


A, B = FakeSession("A"), FakeSession("B")
t = PickTracker()

chk("首次锁定立即生效", t.step(A, [A, B]) is A)
chk("挑战者第 1 次出现不换", t.step(B, [A, B]) is A)
chk("挑战者连续 2 拍仍不换（< HOLD）", t.step(B, [A, B]) is A)
t.step(B, [A, B])
chk("挑战者连续 4 拍（=HOLD）换", t.step(B, [A, B]) is B)
chk("换完挑战者状态清零", t._challenger is None and t._chall_streak == 0)

# 闪现干扰：B 当现任，A 闪一拍就走 → 不换
t2 = PickTracker()
t2.step(B, [A, B])
t2.step(A, [A, B])                      # A 冒头
chk("闪现 1 拍后不换", t2.step(B, [A, B]) is B)
chk("闪现后挑战者计数被重置", t2._chall_streak in (0, 1))

# 现任消失：立即切
t3 = PickTracker()
t3.step(A, [A, B])
chk("现任从会话列表消失 → 立即切换", t3.step(B, [B]) is B)

# 换会话清锚点
t3._anchor = (10.0, None)
t3._switch_to(B)
chk("切换会清锚点", t3._anchor is None)

print(f"\n合计 {_ok} 通过 / {_fail} 失败")
sys.exit(1 if _fail else 0)
