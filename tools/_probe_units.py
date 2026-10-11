# -*- coding: utf-8 -*-
"""插探针量清"为什么只显示几条"。

不靠读代码推断，直接在关键点打时间戳：
   · 每批切出几段、是否自然边界、门过了几段、pending 累积多少秒
   · 每批结束时 mt_units 有几个、mt_first 是否设置
   · 什么时候提交翻译

这样能直接看出断在哪一环。
"""
import io
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from live_subtitle_v2 import (LoopbackCapture, to_mono16k, Segmenter,
                              NoiseGate, TARGET_RATE)

BATCH, WAIT, CAP, MAXSEG = 3, 0.35, 8.0, 4.0

cap = LoopbackCapture()
seg = Segmenter(min_len=0.5, max_len=MAXSEG, silence=0.45)
gate = NoiseGate()

t0 = time.time()
mt_pending, mt_units, mt_first = [], [], None
events = []
n = 0
cap.start()
while time.time() - t0 < 45:
    b = cap.read(timeout=0.2)
    if b is None:
        continue
    segs_in = seg.push(to_mono16k(b, cap.rate, cap.ch))
    if not segs_in:
        time.sleep(0.005)
        continue
    n += 1
    t = time.time() - t0
    kept = [s for s in segs_in if gate.is_speech(s)]
    natural = any(len(s) / TARGET_RATE < MAXSEG - 0.05 for s in segs_in)
    durs = [round(len(s) / TARGET_RATE, 1) for s in segs_in]
    mt_pending.extend((s, "<x>") for s in kept)
    pt = sum(len(s) / TARGET_RATE for s, _ in mt_pending)
    flush = (CAP <= 0 or natural or pt >= CAP)
    events.append("  b%-3d t=%5.1fs segs=%d dur=%s natural=%s pass=%d "
                  "pending=%.1fs flush=%s"
                  % (n, t, len(segs_in), durs, natural, len(kept), pt, flush))
    if mt_pending and flush:
        mt_units.append(" ".join(x for _s, x in mt_pending))
        mt_pending = []
    now = time.time() - t0
    if mt_units and (len(mt_units) >= BATCH
                     or (mt_first is not None and now - mt_first >= WAIT)):
        units = mt_units[:]
        mt_units = []
        mt_first = None
        events.append("      >>> submit %d unit(s)  t=%.1fs" % (len(units), now))
    elif mt_units and mt_first is None:
        mt_first = now
        events.append("      >>> start timer  t=%.1fs" % now)
    time.sleep(0.005)
cap.stop()

print("  ran 45s, %d recognition batches" % n)
print()
for e in events:
    print(e)
print()
c_unit = sum(1 for e in events if "flush=True" in e)
c_sub = sum(1 for e in events if "submit" in e)
print("  semantic units %d   submits %d" % (c_unit, c_sub))
print()
if c_unit <= 2:
    print("  -> very few units: segments rarely hit a natural boundary and")
    print("     pending never reaches mt_cap. That is a segmentation issue,")
    print("     not a batching issue.")
elif c_sub < c_unit - 1:
    print("  -> units are not submitted promptly; they wait for %d units." % BATCH)
else:
    print("  -> units and submits match; batching logic is fine.")
