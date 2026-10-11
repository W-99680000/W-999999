# -*- coding: utf-8 -*-
"""按"攒够新音频就更新"重新测：不同模型 + 不同间隔，看哪个能真正追平实时。
   判据：墙钟耗时 ≈ 音频时长（追平），且缓冲不涨、识别正确。
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
KW = ("never", "thought", "again", "coffee", "honey", "temper")

print(f"素材 {ADUR:.1f} 秒（循环同一段 4 句）\n")
print("=" * 96)
print(f"{'模型':<7}{'线程':>4}{'间隔':>6}{'更新':>5}{'次/秒':>7}{'均耗':>7}{'总墙钟':>8}"
      f"{'追平?':>7}{'均缓冲':>8}{'裁剪':>5}  关键词")
print("=" * 96)

results = []
for model, th in (("tiny", 8), ("base", 8), ("base", 4), ("small", 8)):
    for interval in (0.5, 1.0, 2.0):
        try:
            st = StreamingTranscriber(model=model, threads=th, language="en",
                                      update_interval=interval,
                                      tail_seconds=2.5, min_buffer=1.0,
                                      max_buffer=10.0)
            t0 = time.time()
            i = 0
            CH = int(RATE * 0.2)
            while i < len(audio):
                target = t0 + i / RATE
                if time.time() < target:
                    time.sleep(min(0.05, target - time.time()))
                st.feed(audio[i:i + CH])
                i += CH
                st.poll()
            for _ in st.flush():
                pass
            el = time.time() - t0
            st.stop()
            u = st.stats["updates"]
            avg = st.stats["t_upd"] / max(1, u)
            abuf = st.stats["buf_sec"] / max(1, u)
            full = st.stable_text.lower()
            hits = sum(1 for k in KW if k in full)
            reached = el < ADUR * 1.05
            print(f"{model:<7}{th:>4}{interval:>6.1f}{u:>5}{u/el:>7.2f}{avg:>7.2f}"
                  f"{el:>8.1f}{'  是' if reached else '  否':>7}{abuf:>8.1f}"
                  f"{st.stats['trims']:>5}  {hits}/{len(KW)}")
            results.append((reached, abuf, hits, model, th, interval, avg, el))
            if st.stats["errors"]:
                print("      异常:", st.stats["errors"][:2])
        except Exception as e:
            print(f"{model:<7}{th:>4}{interval:>6.1f}  失败 {type(e).__name__} {str(e)[:50]}")

print()
print("=" * 96)
ok = [r for r in results if r[0] and r[1] < 6.0 and r[2] >= 5]
if ok:
    ok.sort(key=lambda r: (r[1], -r[2]))
    print("** 追平实时 且 缓冲<6s 且 关键词>=5 的配置（按缓冲升序）：**")
    for r in ok:
        print(f"   {r[3]} / {r[4]} 线程 / 间隔 {r[5]}s → 均耗 {r[6]:.2f}s，"
              f"均缓冲 {r[1]:.1f}s，关键词 {r[2]}/6，总 {r[7]:.1f}s")
else:
    print("** 没有配置同时满足：请放宽条件或换方案 **")
