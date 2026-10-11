# -*- coding: utf-8 -*-
"""把延迟拆到每一段，找出哪一段是瓶颈。

已知（早先 bench）：耗时 ≈ 0.04 × 音频秒 + 1.08s
  → 1.08s 是**每次调用的固定开销**，与音频长短无关。
  → 4 秒批：1.08 + 0.16 = 1.24s？但实测转写要 2.1-2.3s，对不上。
    所以固定开销比 1.08s 大，或者还有别的成分。

这次要量准：
  A. 纯解码/重采样（数组运算）
  B. 一次 transcribe 的**固定开销**（喂极短音频，比如 0.2 秒）
  C. transcribe 的**可变开销**（音频长度 0.5/1/2/4/8 秒的斜率）
  D. beam_size / vad_filter / word_timestamps 各自代价
  E. 线程数影响
  F. compute_type 影响（int8 vs int8_float32 vs float32）
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
from live_subtitle_v2 import to_mono16k, TARGET_RATE

# 用真实语音素材
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
print(f"  素材 {len(base)/TARGET_RATE:.1f} 秒")

from faster_whisper import WhisperModel


def bench(m, audio, n=3, **kw):
    """跑 n 次取中位。"""
    ts = []
    for _ in range(n):
        t0 = time.time()
        segs, info = m.transcribe(audio, **kw)
        list(segs)                       # 必须消费生成器，否则没真跑
        ts.append(time.time() - t0)
    ts.sort()
    return ts[len(ts) // 2]


print("\n" + "=" * 70)
print("A. API 形态的固定开销（喂极短音频）")
print("=" * 70)
m = WhisperModel("small", device="cpu", compute_type="int8", cpu_threads=8)
tiny = base[: int(0.2 * TARGET_RATE)]
short = base[: int(0.5 * TARGET_RATE)]
print(f"  0.2s 音频: {bench(m, tiny, 3, beam_size=5, vad_filter=True, condition_on_previous_text=False):.3f}s")
print(f"  0.5s 音频: {bench(m, short, 3, beam_size=5, vad_filter=True, condition_on_previous_text=False):.3f}s")
print("  → 两者之差 ≈ 0.3s 音频的可变开销；绝对值 ≈ 固定开销")

print("\n" + "=" * 70)
print("B. 可变开销：音频长度 vs 耗时")
print("=" * 70)
print(f"  {'音频秒':>7} {'耗时':>8} {'秒/音频秒':>10}")
print("  " + "-" * 30)
for dur in (0.5, 1.0, 2.0, 4.0, 8.0):
    a = base[: int(dur * TARGET_RATE)]
    if len(a) < int(dur * TARGET_RATE):
        a = np.tile(base, 3)[: int(dur * TARGET_RATE)]
    t = bench(m, a, 3, beam_size=5, vad_filter=True,
              condition_on_previous_text=False)
    print(f"  {dur:>7.1f} {t:>8.3f} {t/dur:>10.2f}")

print("\n" + "=" * 70)
print("C. 参数代价（4 秒音频）")
print("=" * 70)
a4 = base[: int(4.0 * TARGET_RATE)]
variants = [
    ("当前配置 beam=5 vad=T", dict(beam_size=5, vad_filter=True)),
    ("beam=1", dict(beam_size=1, vad_filter=True)),
    ("beam=5 vad=F", dict(beam_size=5, vad_filter=False)),
    ("beam=1 vad=F", dict(beam_size=1, vad_filter=False)),
    ("beam=5 vad=T +字级时间戳", dict(beam_size=5, vad_filter=True,
                                      word_timestamps=True)),
    ("beam=1 vad=T +字级时间戳", dict(beam_size=1, vad_filter=True,
                                      word_timestamps=True)),
]
print(f"  {'配置':<28} {'耗时':>8}")
print("  " + "-" * 38)
for name, kw in variants:
    kw.setdefault("condition_on_previous_text", False)
    kw.setdefault("temperature", 0.0)
    t = bench(m, a4, 3, **kw)
    print(f"  {name:<28} {t:>8.3f}")

print("\n" + "=" * 70)
print("D. 线程数 / compute_type")
print("=" * 70)
for threads in (4, 8, 12, 16):
    try:
        mm = WhisperModel("small", device="cpu", compute_type="int8",
                          cpu_threads=threads)
        t = bench(mm, a4, 2, beam_size=5, vad_filter=True,
                  condition_on_previous_text=False, temperature=0.0)
        print(f"  threads={threads:<3} {t:.3f}s")
        del mm
    except Exception as e:
        print(f"  threads={threads:<3} 失败 {type(e).__name__}")
print()
for ct in ("int8", "int8_float32", "float32"):
    try:
        mm = WhisperModel("small", device="cpu", compute_type=ct, cpu_threads=8)
        t = bench(mm, a4, 2, beam_size=5, vad_filter=True,
                  condition_on_previous_text=False, temperature=0.0)
        print(f"  compute_type={ct:<14} {t:.3f}s")
        del mm
    except Exception as e:
        print(f"  compute_type={ct:<14} 失败 {type(e).__name__}: {str(e)[:40]}")

print("\n" + "=" * 70)
print("E. 模型大小（4 秒音频，当前配置）")
print("=" * 70)
for size in ("tiny", "base", "small"):
    try:
        mm = WhisperModel(size, device="cpu", compute_type="int8", cpu_threads=8)
        t = bench(mm, a4, 2, beam_size=5, vad_filter=True,
                  condition_on_previous_text=False, temperature=0.0)
        segs, _ = mm.transcribe(a4, beam_size=5, vad_filter=True,
                                condition_on_previous_text=False,
                                temperature=0.0)
        txt = " ".join(s.text.strip() for s in segs).strip()
        print(f"  {size:<6} {t:.3f}s   {txt[:52]}")
        del mm
    except Exception as e:
        print(f"  {size:<6} 失败 {type(e).__name__}")
