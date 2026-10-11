# -*- coding: utf-8 -*-
"""长跑 60 秒，每 2 秒采样缓冲深度，看它是否收敛到平台（而不是一直涨）。
   这是判断"追不追得上实时"的唯一可靠方法。
"""
import io
import os
import sys
import time
import wave

import numpy as np

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from live_subtitle_v2 import to_mono16k
from streaming_asr import StreamingTranscriber, RATE

# ── 样本检查 ─────────────────────────────────────────────
# tests/e2e_speech.wav 随仓库发布（samples/ 下）。缺失就友好退出。
def _need_sample(p):
    """确认样本存在，否则给出提示后退出。"""
    if os.path.exists(p):
        return p
    print("跳过：缺少 " + p)
    print("本仓库自带 samples/e2e_speech.wav；请确认已完整 clone。")
    raise SystemExit(0)

w = wave.open(os.path.join(HERE, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples", "e2e_speech.wav")), "rb")
raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
base = to_mono16k(raw, w.getframerate(), w.getnchannels())
n = int(RATE * 60)
audio = np.tile(base, n // len(base) + 1)[:n]
ADUR = len(audio) / RATE
print(f"素材 {ADUR:.0f} 秒（循环）\n")

for model, th, interval, tail in (("tiny", 8, 0.5, 3.0),
                                  ("base", 8, 0.5, 3.0),
                                  ("base", 8, 0.5, 5.0)):
    st = StreamingTranscriber(model=model, threads=th, language="en",
                              update_interval=interval, tail_seconds=tail,
                              min_buffer=1.0, max_buffer=30.0)
    depths = []
    t0 = time.time()
    i = 0
    CH = int(RATE * 0.2)
    last = 0.0
    while i < len(audio):
        target = t0 + i / RATE
        if time.time() < target:
            time.sleep(min(0.05, target - time.time()))
        st.feed(audio[i:i + CH])
        i += CH
        st.poll()
        el = time.time() - t0
        if el - last >= 2.0:
            last = el
            with st.lock:
                depths.append(round(len(st.buf) / RATE, 1))
    for _ in st.flush():
        pass
    st.stop()
    u = st.stats["updates"]
    avg = st.stats["t_upd"] / max(1, u)
    print(f"=== {model} / {th}线程 / 间隔{interval}s / tail{tail}s ===")
    print(f"  更新 {u} 次，均耗 {avg:.2f}s，裁剪 {st.stats['trims']} 次")
    print(f"  缓冲深度每2s：{' '.join(f'{d:.0f}' for d in depths)}")
    if len(depths) >= 6:
        first3 = np.mean(depths[:3]); last3 = np.mean(depths[-3:])
        print(f"  前3点均值 {first3:.1f}s  后3点均值 {last3:.1f}s  "
              f"→ {'** 收敛(追平) **' if last3 < first3 * 1.35 else '仍在增长'}")
    if st.stats["errors"]:
        print("  异常:", st.stats["errors"][:2])
    print()
