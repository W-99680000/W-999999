# -*- coding: utf-8 -*-
"""确证最优配置：90 秒长跑，每 2 秒采样缓冲深度，看是否稳定有界。"""
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
n = int(RATE * 90)
audio = np.tile(base, n // len(base) + 1)[:n]
ADUR = len(audio) / RATE
print(f"素材 {ADUR:.0f} 秒（循环同一段语音）\n")

CFG = dict(model="base", threads=8, language="en", update_interval=0.5,
           tail_seconds=3.0, min_buffer=1.0, max_buffer=10.0)
print(f"配置：{CFG}\n")

st = StreamingTranscriber(**CFG)
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
el = time.time() - t0
st.stop()

u = st.stats["updates"]
avg = st.stats["t_upd"] / max(1, u)
print(f"墙钟 {el:.1f}s，音频 {ADUR:.0f}s")
print(f"更新 {u} 次，均耗 {avg:.2f}s，裁剪 {st.stats['trims']} 次")
print(f"缓冲深度每2s：{' '.join(f'{d:.0f}' for d in depths)}")
print(f"  最大 {max(depths):.0f}s   中位 {np.median(depths):.0f}s")
h = len(depths) // 2
f, s = np.mean(depths[:h]), np.mean(depths[h:])
print(f"  前半 {f:.1f}s  后半 {s:.1f}s  → "
      f"{'** 有界稳定 **' if max(depths) <= CFG['max_buffer'] + 1 else '** 越界 **'}")
print()
print(f"稳定全文（前 200 字）：{st.stable_text[:200]}")
print()
print(f"错误：{st.stats['errors'][:2] if st.stats['errors'] else '无'}")

print()
print("=" * 70)
print("延迟构成（实测）")
print("=" * 70)
print(f"  首字延迟 ≈ 一次更新耗时 = {avg:.2f}s")
print(f"  缓冲最深 {max(depths):.0f}s → 最坏情况下字幕落后音频约 {max(depths):.0f}s")
print(f"  对照原分块方案（攒 4s 再转写）：4 + 1.5 = 5.5s")
