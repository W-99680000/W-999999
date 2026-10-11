# -*- coding: utf-8 -*-
"""自适应延迟：先量"字幕落后音频多少秒"（论文的 local agreement 延迟定义）。

Whisper-Streaming 论文（arXiv 2307.14743）的自适应延迟思路：
  延迟不该是固定值，而是**跟着"部分结果有多稳定"走**。
  稳定（连续两轮假设一致）就往前推，不稳定就把裁剪收紧。

我们现在的问题：句子边界裁剪让缓冲涨到 6~9 秒，字幕就落后 6~9 秒。
这个脚本测出"当前实际延迟"，作为改动前的基线。

指标：每次交付新词时，该词在音频里的结束时间 → 到交付时刻的时间差。
"""
import io
import os
import sys
import time
import wave

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import numpy as np
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

w = wave.open(os.path.join(HERE, "samples", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples", "e2e_speech.wav"))
              if os.path.exists(os.path.join(HERE, "samples", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples", "e2e_speech.wav")))
              else os.path.join(HERE, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples", "e2e_speech.wav")), "rb")
raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
base = to_mono16k(raw, w.getframerate(), w.getnchannels())

TOTAL = 60.0
audio = np.tile(base, int(np.ceil(TOTAL * RATE / len(base))))[:int(TOTAL * RATE)]
print(f"  素材 {len(audio)/RATE:.1f} 秒　model=base")

th = StreamingTranscriber(model="base", threads=8, language="en",
                          update_interval=0.5, tail_seconds=3.0,
                          max_buffer=12.0)

# 记录：每次出现新输出时的（墙钟，音频已喂入位置，缓冲深度）
marks = []
seen = 0
t0 = time.time()
i = 0
step = int(RATE * 0.04)
fed_sec = 0.0
while i < len(audio):
    th.feed(audio[i:i + step])
    i += step
    fed_sec = i / RATE
    time.sleep(0.04)
    ev = th.poll()
    if ev:
        seen += len(ev)
        with th.lock:
            buf = len(th.buf) / RATE
        marks.append((round(time.time() - t0, 2), round(fed_sec, 2),
                      round(buf, 2), len(ev)))

th.running = False
time.sleep(0.3)
try:
    th._th.join(timeout=3)
except Exception:
    pass

print(f"\n  共 {len(marks)} 次交付，{seen} 个新词/片段")
print(f"\n  {'墙钟':>6} {'已喂音频':>8} {'缓冲':>6} {'本次片段':>8}  落后(秒)")
print("  " + "-" * 52)
lags = []
for wall, fed, buf, n in marks:
    lag = fed - wall                      # 已喂音频 - 墙钟 = 当前落后量
    lags.append(buf)
    print(f"  {wall:6.1f} {fed:8.1f} {buf:6.1f} {n:8d}  {lag:+6.2f}")

st = th.stats
print("  " + "-" * 52)
print(f"\n  缓冲：平均 {sum(lags)/len(lags):.1f}s　最大 {max(lags):.1f}s　最小 {min(lags):.1f}s")
print(f"  更新 {st['updates']} 次　裁剪 {st['trims']} 次　错误 {len(st['errors'])}")
print(f"  **基线：字幕平均落后约 {sum(lags)/len(lags):.1f} 秒**")
print(f"\n  论文目标：3.3 秒（GPU）／我们 CPU 上能做到多少要看下一版实测")
