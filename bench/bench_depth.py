# -*- coding: utf-8 -*-
"""正确判据：**缓冲深度是否随时间增长**。
   追平实时的定义 = 喂进去的音频都被及时处理，缓冲深度稳定（不单调增长）。
   之前"墙钟 < 音频时长"是伪指标 —— 那只是喂音频的循环还没走完。
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
n = int(RATE * 30)
audio = np.tile(base, n // len(base) + 1)[:n]
ADUR = len(audio) / RATE
print(f"素材 {ADUR:.1f} 秒\n")


def run(model, th, interval, tail=2.5):
    """按真实速度喂 30 秒音频，每 3 秒采一次缓冲深度。"""
    st = StreamingTranscriber(model=model, threads=th, language="en",
                              update_interval=interval, tail_seconds=tail,
                              min_buffer=1.0, max_buffer=10.0)
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
        if el - last >= 3.0:
            last = el
            with st.lock:
                depths.append(round(len(st.buf) / RATE, 1))
    st.stop()
    stable = st.stats["updates"] > 0
    # 追平判据：后半段均值 不显著高于 前半段（不单调增长）
    if len(depths) >= 4:
        h = len(depths) // 2
        first, second = np.mean(depths[:h]), np.mean(depths[h:])
        growing = second > first + 1.5
    else:
        first = second = 0.0
        growing = True
    return st, depths, first, second, growing


print("=" * 100)
print(f"{'模型':<7}{'线程':>4}{'间隔':>6}{'更新':>5}{'均耗':>7}  缓冲深度随时间（每3秒）          判据")
print("=" * 100)

best = None
for model, th in (("tiny", 8), ("base", 8), ("base", 4), ("small", 8), ("small", 4)):
    for interval in (0.5, 1.0):
        st, depths, f, s, growing = run(model, th, interval)
        avg = st.stats["t_upd"] / max(1, st.stats["updates"])
        trail = " ".join(f"{d:5.1f}" for d in depths[:10])
        verdict = "增长(追不上)" if growing else "**稳定(追平)**"
        print(f"{model:<7}{th:>4}{interval:>6.1f}{st.stats['updates']:>5}{avg:>7.2f}  "
              f"{trail:<32}{verdict}")
        if st.stats["errors"]:
            print("      异常:", st.stats["errors"][:2])
        if not growing and (best is None or avg < best[0]):
            best = (avg, model, th, interval)

print()
if best:
    print(f"** 能追平实时的最省配置：{best[1]} / {best[2]} 线程 / 间隔 {best[3]}s "
          f"→ 平均每次更新 {best[0]:.2f}s **")
else:
    print("** 没有配置能追平实时 **")
