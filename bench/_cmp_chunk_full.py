# -*- coding: utf-8 -*-
"""对比：分块流水产出的 srt vs 原来整片一次跑的 srt（同一段音频）。

判据不是"条数一样"，而是**按时间对齐后文本像不像** —— 分块的切点会
影响 cue 边界，条数差几条是正常的；观众在意的是一屏上的话是否一致。
"""
import bisect
import os
import sys
from difflib import SequenceMatcher

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)
from watch_player import parse_srt  # noqa: E402

A = ("pre_cache/【朝鲜演说】完整中文 2022.4.25朝鲜人民革命军建军90周年"
     "金正恩演讲.zh.srt")                       # 整片一次跑（基准）
B = "pre_cache/url_cache/BV13kC6YUEHY.zh.srt"   # 分块流水

ca, cb = parse_srt(A), parse_srt(B)
print(f"整片（基准）: {len(ca)} 条，覆盖到 {ca[-1][1]/60:.1f} 分钟")
print(f"分块（新）  : {len(cb)} 条，覆盖到 {cb[-1][1]/60:.1f} 分钟")

starts = [c[0] for c in cb]
sims = []
for s, e, zh, _src in ca:
    if not zh:
        continue
    i = bisect.bisect_right(starts, s) - 1
    best = 0.0
    for j in (i, i + 1, i - 1, i + 2):
        if 0 <= j < len(cb):
            s2, e2, zh2, _ = cb[j]
            if min(e, e2) - max(s, s2) > 0:
                best = max(best, SequenceMatcher(None, zh, zh2).ratio())
    sims.append(best)

avg = sum(sims) / max(1, len(sims))
low = sum(1 for x in sims if x < 0.6)
print(f"\n按时间对齐 {len(sims)} 条：平均文本相似度 {avg:.3f}")
print(f"相似度 <0.6 的条数：{low}"
      f"（{'质量相当' if low <= len(sims) * 0.1 else '需要复查'}）")
