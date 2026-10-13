# -*- coding: utf-8 -*-
"""_verify_stitch.py —— 跨块断句缝合的纯逻辑单测（不需要模型/音频）。

覆盖：_should_stitch 的全部分支 + stitch_boundary 的副作用
（cues 弹出、done 索引作废、合并文本与空格规则）。

跑法：python bench/_verify_stitch.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pretranslate import Cue, _should_stitch, stitch_boundary, \
    MAX_CUE_CHARS, MAX_CUE_DUR

ok = fail = 0


def chk(name, cond):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ok   {name}")
    else:
        fail += 1
        print(f"  FAIL {name}")


print("== _should_stitch ==")
p = Cue(10.0, 12.0, "and then he said")
n = Cue(12.1, 13.5, "hello everyone")
chk("半句+小间隙 → 缝", _should_stitch(p, n) is True)
chk("上块末是完整句 → 不缝",
    _should_stitch(Cue(10, 12, "他说完了。"), Cue(12.1, 13, "下一句")) is False)
chk("间隙 >0.4s → 不缝",
    _should_stitch(p, Cue(12.9, 14.0, "hello")) is False)
chk("时间倒挂 → 不缝",
    _should_stitch(p, Cue(9.0, 12.5, "hello")) is False)
chk("None 安全", _should_stitch(None, n) is False
    and _should_stitch(p, None) is False)
# 超长不缝
long_txt = "x" * int(MAX_CUE_CHARS)
chk("合并后超字符上限 → 不缝",
    _should_stitch(Cue(10, 12, long_txt), Cue(12.1, 13, long_txt)) is False)
chk("合并后超时长上限 → 不缝",
    _should_stitch(Cue(0, MAX_CUE_DUR + 1.5, "aaa"),
                   Cue(MAX_CUE_DUR + 1.6, MAX_CUE_DUR + 3.0, "bbb")) is False)

print("== stitch_boundary ==")
# 拉丁系：合并补空格
cues = [Cue(0, 2, "第一句。"), Cue(10.0, 12.0, "and then he said")]
part = [Cue(12.1, 13.5, "hello everyone"), Cue(14.0, 15.0, "Next one.")]
done = {0: "第一句。", 1: "然后他说"}
hit = stitch_boundary(cues, part, done)
chk("发生缝合", hit is True)
chk("cues 弹出末条", len(cues) == 1)
chk("旧译文索引作废", 1 not in done and done.get(0) == "第一句。")
chk("合并时间取并集", part[0].start == 10.0 and part[0].end == 13.5)
chk("拉丁系合并补空格", part[0].text == "and then he said hello everyone")
chk("后续 cue 不动", part[1].text == "Next one.")

# CJK：合并不补空格
cues = [Cue(5.0, 6.0, "他说了一半")]
part = [Cue(6.1, 7.0, "就停住了")]
done = {0: "旧译文"}
chk("CJK 缝合", stitch_boundary(cues, part, done) is True)
chk("CJK 不补空格", part[0].text == "他说了一半就停住了")
chk("CJK 旧译文作废", done == {})

# 混合（原文尾巴拉丁、下句 CJK）不补空格
cues = [Cue(5.0, 6.0, "叫 me")]
part = [Cue(6.1, 7.0, "的名字")]
chk("拉丁尾+CJK头不补空格",
    stitch_boundary(cues, part, {}) and part[0].text == "叫 me的名字")

# 不满足条件：无副作用
cues = [Cue(0, 2, "完整句。")]
part = [Cue(2.1, 3.0, "下一句")]
done = {0: "译"}
chk("完整句不缝", stitch_boundary(cues, part, done) is False)
chk("无副作用", len(cues) == 1 and part[0].text == "下一句" and done == {0: "译"})

# 空列表安全
chk("空 cues 安全", stitch_boundary([], [Cue(0, 1, "x")], {}) is False)
chk("空 part 安全", stitch_boundary([Cue(0, 1, "x")], [], {}) is False)

print(f"\n合计 {ok} 通过 / {fail} 失败")
sys.exit(0 if fail == 0 else 1)
