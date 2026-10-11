# -*- coding: utf-8 -*-
"""追：为什么前两句整个丢了。

只跑 8 秒（覆盖第一二句），把每次更新的中间状态打出来：
  base_words / buf_stable / 本轮新确认 / 裁剪点
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
import streaming_asr as S
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

p = os.path.join(HERE, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples", "e2e_speech.wav"))
w = wave.open(p, "rb")
raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
base = to_mono16k(raw, w.getframerate(), w.getnchannels())
seg = base[:int(9.0 * RATE)]
print(f"  只喂前 {len(seg)/RATE:.1f} 秒（第一二句所在位置）")

th = StreamingTranscriber(model="base", threads=8, language="en",
                          update_interval=0.5, tail_seconds=3.0, max_buffer=12.0)

# 包一层，把 _cut_buffer 的调用记下来
orig_cut = S.StreamingTranscriber._cut_buffer


def traced(self, cut_sec, n_confirmed_in_buf=0, words=None):
    before = (len(self.base_words), len(self.buf_stable),
              round(len(self.buf) / RATE, 2))
    r = orig_cut(self, cut_sec, n_confirmed_in_buf, words)
    after = (len(self.base_words), len(self.buf_stable),
             round(len(self.buf) / RATE, 2))
    print(f"    [CUT] 请求 {cut_sec:5.2f}s nconf={n_confirmed_in_buf}"
          f"  返回={r}")
    print(f"          base_words {before[0]}→{after[0]}"
          f"　buf_stable {before[1]}→{after[1]}"
          f"　缓冲 {before[2]}→{after[2]}s")
    return r


S.StreamingTranscriber._cut_buffer = traced

i = 0
step = int(RATE * 0.04)
last_upd = 0
while i < len(seg):
    th.feed(seg[i:i + step])
    i += step
    time.sleep(0.04)
    if th.stats["updates"] != last_upd:
        last_upd = th.stats["updates"]
        with th.lock:
            bw, bs = len(th.base_words), len(th.buf_stable)
            bt = th.hyp_text[:70]
            buf = len(th.buf) / RATE
        print(f"  更新#{last_upd:<3} base={bw:<3} buf_stable={bs:<3} "
              f"缓冲={buf:5.1f}s  假设={bt!r}")

time.sleep(3.5)
th.running = False
time.sleep(0.3)
try:
    th._th.join(timeout=3)
except Exception:
    pass
print()
print("  稳定全文:", th.stable_text)
print("  全部输出:", th.out_everything)
