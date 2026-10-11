# -*- coding: utf-8 -*-
"""量清楚到底慢在哪：whisper 的固定开销 vs 可变开销。
   上次我说"约 1.2 秒固定开销"是估的，不是测的。这次分开测：
     · 纯解码（音频数组 → 16k float32）要多久
     · 模型调用（不同长度音频）各要多久
     · beam_size / vad_filter / condition 各关掉能省多少
"""
import os
import sys
import time
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# ── 样本检查 ─────────────────────────────────────────────
# tests/e2e_speech.wav 随仓库发布（samples/ 下）。缺失就友好退出。
def _need_sample(p):
    """确认样本存在，否则给出提示后退出。"""
    if os.path.exists(p):
        return p
    print("跳过：缺少 " + p)
    print("本仓库自带 samples/e2e_speech.wav；请确认已完整 clone。")
    raise SystemExit(0)

WAV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples", "e2e_speech.wav"))
from live_subtitle_v2 import to_mono16k
from faster_whisper import WhisperModel

w = wave.open(_need_sample(WAV), "rb")
raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
audio = to_mono16k(raw, w.getframerate(), w.getnchannels())
print(f"素材 {len(audio)/16000:.1f} 秒\n")

print("=" * 70)
print("【一】模型加载")
print("=" * 70)
for name in ("tiny", "base", "small"):
    t0 = time.time()
    m = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=4)
    print(f"  {name:<8} 加载 {time.time()-t0:.2f}s")

print()
print("=" * 70)
print("【二】不同长度音频的调用耗时（分固定开销与可变开销）")
print("=" * 70)
m = WhisperModel("small", device="cpu", compute_type="int8", cpu_threads=4)
rows = []
for dur in (0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0):
    n = int(16000 * dur)
    seg = audio[:n] if n <= len(audio) else np.tile(audio, 3)[:n]
    ts = []
    for _ in range(3):
        t0 = time.time()
        list(m.transcribe(seg, language="en", beam_size=5, vad_filter=True,
                          condition_on_previous_text=False)[0])
        ts.append(time.time() - t0)
    el = min(ts)
    rows.append((dur, el))
    print(f"  {dur:>4.1f}s 音频 → {el:5.2f}s  ({dur/el:4.2f}×)")

# 线性拟合：截距=固定开销，斜率=每秒音频的可变开销
xs = np.array([r[0] for r in rows]); ys = np.array([r[1] for r in rows])
A = np.vstack([xs, np.ones(len(xs))]).T
k, b = np.linalg.lstsq(A, ys, rcond=None)[0]
print(f"\n  ** 拟合：耗时 = {k:.2f}×音频秒 + {b:.2f}s **")
print(f"     固定开销 {b:.2f}s　可变 {k:.2f} 秒/秒音频")

print()
print("=" * 70)
print("【三】各参数能省多少（用 1.5 秒短音频，固定开销最吃亏的场景）")
print("=" * 70)
seg = audio[:int(16000 * 1.5)]
cfgs = [
    ("beam5+vad",       dict(beam_size=5, vad_filter=True)),
    ("beam5-vad",       dict(beam_size=5, vad_filter=False)),
    ("beam1+vad",       dict(beam_size=1, vad_filter=True)),
    ("beam1-vad",       dict(beam_size=1, vad_filter=False)),
    ("greedy-novad-nocond",
                        dict(beam_size=1, vad_filter=False, condition_on_previous_text=False)),
]
for label, kw in cfgs:
    ts = []
    for _ in range(3):
        t0 = time.time()
        s = list(m.transcribe(seg, language="en",
                              condition_on_previous_text=False, **kw)[0])
        ts.append(time.time() - t0)
    el = min(ts)
    txt = " ".join(x.text.strip() for x in s)
    print(f"  {label:<22} {el:5.2f}s  ({1.5/el:4.2f}×)  {txt[:44]}")

print()
print("=" * 70)
print("【四】换更小的模型（1.5 秒音频）")
print("=" * 70)
for name in ("tiny", "base", "small"):
    mm = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=4)
    ts = []
    for _ in range(3):
        t0 = time.time()
        s = list(mm.transcribe(seg, language="en", beam_size=1, vad_filter=False,
                               condition_on_previous_text=False)[0])
        ts.append(time.time() - t0)
    el = min(ts)
    txt = " ".join(x.text.strip() for x in s)
    print(f"  {name:<8} {el:5.2f}s ({1.5/el:4.2f}×)  {txt[:50]}")
