# -*- coding: utf-8 -*-
"""tools/test_subtitle_layout.py —— 视频字幕断行/切条回归。"""
from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import subtitle_layout as L  # noqa: E402

n_ok = n_bad = 0


def chk(name, cond, detail=""):
    global n_ok, n_bad
    if cond:
        n_ok += 1
        print("  ok   " + name)
    else:
        n_bad += 1
        print("  FAIL " + name + "  " + str(detail))


def main():
    print("=" * 56)
    print("字幕布局：长 cue 切短 + 不断英文词")

    long_zh = ("人的问题其实并不是钱真的不够而是他永远觉得钱不够"
               "真实的人生当然比这个公式复杂得多可有了")
    parts = L.split_cue(0.0, 9.0, long_zh, long_zh)
    chk("长中文 cue 切成多条", len(parts) >= 2, len(parts))
    chk("每条不超过 25 个视觉字",
        all(L._visual_len(z) <= 25 for _a, _b, z, _s in parts),
        [round(L._visual_len(z), 1) for _a, _b, z, _s in parts])
    chk("时间单调不重叠",
        all(parts[i][1] <= parts[i + 1][0] + 1e-6
            for i in range(len(parts) - 1)),
        [(parts[i][1], parts[i + 1][0]) for i in range(len(parts) - 1)])
    chk("最后一条结束时间保留", abs(parts[-1][1] - 9.0) < 1e-6, parts[-1])
    chk("中文不丢字",
        re.sub(r"\s+", "", "".join(p[2] for p in parts)) ==
        re.sub(r"\s+", "", long_zh))

    short = L.split_cue(0.0, 3.0, "这个选择会直接改变目标",
                        "这个选择会直接改变目标")
    chk("短 cue 不切", len(short) == 1, len(short))

    latin = ("This is a long English sentence that should never be cut "
             "in the middle of a word")
    lp = L.split_cue(0.0, 8.0, "", latin)
    chk("英文长句切成多条", len(lp) >= 2, len(lp))
    chk("英文不丢字",
        re.sub(r"\s+", " ", " ".join(p[3] for p in lp)).strip() ==
        re.sub(r"\s+", " ", latin).strip(),
        [p[3] for p in lp])
    # _find_cut 的切点必须在空格/标点处，不能落在 ASCII 单词内部
    txt = "This is a long English sentence that should never be cut"
    cut = L._find_cut(txt, 18)
    at_space = (cut >= len(txt) or txt[cut].isspace()
                or txt[cut - 1].isspace())
    chk("英文切点落在词边界", at_space,
        (cut, txt[max(0, cut - 5):cut + 5]))

    print(f"\n合计 {n_ok} 通过 / {n_bad} 失败")
    return 0 if n_bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
