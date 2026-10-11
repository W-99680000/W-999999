# -*- coding: utf-8 -*-
"""调参实测：怎么让字幕**出得够勤**，又不把句子切碎。

背景（探针 _probe_units.py 实测）：单个"语义单元"最长 8 秒
（mt_cap=8.0 才强制 flush，max_seg=4.0 让自然边界很少），
于是字幕平均 13 秒才出一条 —— 观感就是"字幕消失"。

但 max_seg 不能随便调小：更早实测过 1.5s 会切出
"我从没想过我会。/ 再见。/ 警察。" 这种碎片。
所以要找的是**折中**，判据两条：
  · 出字幕频率（秒/条）
  · 译文是否完整（碎片会让译文变成半句）

做法：不改程序，直接复现主循环的切段+攒单元逻辑，用录下来的
真实音频跑不同参数，量频率与句子完整度。
"""
import io
import os
import statistics
import sys
import time
import wave

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from live_subtitle_v2 import to_mono16k, Segmenter, NoiseGate, TARGET_RATE

# 用真实越南语音频当素材
SRC = None
for p in (os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples\vi_long.wav"),
          os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples\vi_sample.wav")):
    if os.path.exists(p):
        SRC = p
        break
if not SRC:
    print("  没有越南语素材")
    sys.exit(1)

w = wave.open(SRC, "rb")
raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
audio = to_mono16k(raw, w.getframerate(), w.getnchannels())
# 拼长一点，模拟持续播放
audio = np.tile(audio, 4)
print(f"  素材 {os.path.basename(SRC)} ×4 = {len(audio)/TARGET_RATE:.1f} 秒")
print()

CONFIGS = [
    ("当前默认",            4.0, 8.0, 0.45),
    ("mt_cap=3",            4.0, 3.0, 0.45),
    ("mt_cap=2.5",          4.0, 2.5, 0.45),
    ("max_seg=3+mt_cap=3",  3.0, 3.0, 0.45),
    ("max_seg=2.5+cap=2.5", 2.5, 2.5, 0.40),
    ("vad=0.3+cap=3",       4.0, 3.0, 0.30),
]

print("  %-22s %8s %10s %12s" % ("配置", "单元数", "秒/单元", "平均段数/单元"))
print("  " + "-" * 58)
for label, maxseg, cap, sil in CONFIGS:
    seg = Segmenter(min_len=0.5, max_len=maxseg, silence=sil)
    gate = NoiseGate()
    units = []
    pend = []
    # 按 10ms 块喂，模拟真实采集
    step = int(0.01 * TARGET_RATE)
    for i in range(0, len(audio) - step, step):
        for s in seg.push(audio[i:i + step]):
            if gate.is_speech(s):
                pend.append(s)
            pt = sum(len(x) for x in pend) / TARGET_RATE
            if pend and pt >= cap:
                units.append(pend)
                pend = []
    if pend:
        units.append(pend)
    if not units:
        print("  %-22s %8d" % (label, 0))
        continue
    durs = [sum(len(x) for x in u) / TARGET_RATE for u in units]
    nsegs = [len(u) for u in units]
    print("  %-22s %8d %10.1f %12.1f" % (
        label, len(units), statistics.median(durs), statistics.mean(nsegs)))

print()
print("  说明：秒/单元越小 → 字幕出得越勤。")
print("        平均段数/单元越大 → 单元里拼的碎段越多，译文越可能被切碎。")
print("        目标是**秒/单元 2~4 秒**（正常字幕节奏），同时段数别涨太多。")
