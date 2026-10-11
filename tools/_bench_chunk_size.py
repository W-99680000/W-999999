# -*- coding: utf-8 -*-
"""批次大小 vs 端到端延迟 —— 修正测量口径。

上一版我用"喂入进度指针"算延迟，那是错的：段在进度指针**之前**就
结束了，所以算出来是负数（字幕比音频还早，不可能）。

正确口径（主程序 _measure_e2e_lag.py 用的那个）：
  延迟 = 字幕产出时刻 - **该段音频在时间轴上的结束位置**
其中"结束位置"要在段被切出来的那一刻记下来，而不是用进度指针。

继续回答原问题：批次缩小能不能降低延迟，还是必须上真流式。
"""
import io
import os
import statistics
import sys
import time
import wave

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import numpy as np
from live_subtitle_v2 import to_mono16k, TARGET_RATE, Segmenter
from remote_asr import from_env_or_key

rasr = from_env_or_key()
if rasr is None:
    print("  没有 key")
    sys.exit(1)

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
if not os.path.exists(p):
    p = os.path.join(HERE, "samples", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples", "e2e_speech.wav"))
w = wave.open(p, "rb")
raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
base = to_mono16k(raw, w.getframerate(), w.getnchannels())
base = np.tile(base, 3)[: int(45 * TARGET_RATE)]
print(f"  素材 {len(base)/TARGET_RATE:.0f} 秒")
print(f"  {rasr.status()}\n")

print("=" * 76)
print("端到端延迟 vs 批次大小（正确口径：产出时刻 - 该段音频结束位置）")
print("=" * 76)
print(f"  {'max_batch':>10} {'段数':>5} {'中位':>7} {'P90':>7} {'平均':>7} "
      f"{'最差':>7} {'调用':>5} {'实耗时':>8}")
print("  " + "-" * 68)

for max_batch in (12.0, 8.0, 6.0, 4.0, 2.0, 1.5, 1.0):
    seg = Segmenter(min_len=0.6, max_len=max_batch)
    segs_in = []
    events = []
    fed = 0.0
    step = int(0.04 * TARGET_RATE)
    t0 = time.time()
    n0 = rasr.calls
    i = 0

    def flush(segs_in, end_pos):
        """把攒着的段发出去，返回 (events项 或 None)。"""
        if not segs_in:
            return None
        t_a = time.time()
        cat = np.concatenate(segs_in)
        txt, lg = rasr(cat)
        t_b = time.time()
        if txt:
            return (t_b - t0, end_pos, t_b - t_a, txt)
        return None

    while i < len(base):
        chunk = base[i:i + step]
        i += step
        fed += len(chunk) / TARGET_RATE
        for s in seg.push(chunk):
            segs_in.append(s)
        dur_in = sum(len(s) for s in segs_in) / TARGET_RATE
        if dur_in >= max_batch:
            ev = flush(segs_in, fed)
            if ev:
                events.append(ev)
            segs_in = []
        target = i / TARGET_RATE
        now = time.time() - t0
        if target > now:
            time.sleep(target - now)
    ev = flush(segs_in, fed)
    if ev:
        events.append(ev)

    if not events:
        print(f"  {max_batch:>10.1f}   （没产出）")
        continue
    lags = sorted(e[1] - e[0] for e in events)   # 音频结束位置 - 产出时刻
    # 延迟 = 产出时刻 - 音频结束位置 = -(lags)
    lat = sorted(e[0] - e[1] for e in events)
    med = statistics.median(lat)
    p90 = lat[min(len(lat) - 1, int(len(lat) * 0.9))]
    mean = sum(lat) / len(lat)
    print(f"  {max_batch:>10.1f} {len(events):>5} {med:>7.2f} {p90:>7.2f} "
          f"{mean:>7.2f} {max(lat):>7.2f} {rasr.calls - n0:>5} "
          f"{time.time()-t0:>7.1f}s")

print()
print("  口径说明：")
print("    音频结束位置 = 该段音频送完时的时间轴位置（实时喂入，等于墙上时钟）")
print("    延迟 = 产出时刻 - 音频结束位置")
print("    所以延迟包含：攒批等待 + 识别耗时 + （主程序里还有翻译）")
