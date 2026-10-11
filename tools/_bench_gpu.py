# -*- coding: utf-8 -*-
"""GPU 可用性被低估了：量本地 whisper 在 RTX 4070 上的真实延迟。

背景（必须承认的判断失误）：
  我之前反复说"本机无 GPU"，依据是早先一次 cuBLAS 缺失的报错。
  那是**从一次失败推断硬件不存在**，而不是去查硬件。实际是：
      NVIDIA GeForce RTX 4070 Laptop GPU   8188 MiB   CC 8.9
  （Win32_VideoController 报 4GB 是 Windows 已知 bug，nvidia-smi 才准）

  这个误判影响了架构决策 —— 我把识别搬到远端，正是因为
  "本地 CPU 有 2 秒固定成本"。而 GPU 上那个成本可能根本不是 2 秒。

本脚本量：
  ① GPU 上一次 transcribe 的固定成本（按音频长度扫）
  ② 对比 CPU 的同口径数字（那是 2.0s）
  ③ 对比远端 qwen3-asr-flash（12 秒音频 0.76s）
  ④ 多语种（俄语/越南语）在 GPU 上的表现
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
from live_subtitle_v2 import to_mono16k, TARGET_RATE

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
print(f"  素材 {len(base)/TARGET_RATE:.1f} 秒（英语）")

from faster_whisper import WhisperModel


def bench(m, audio, n=3, **kw):
    ts = []
    txt = ""
    for _ in range(n):
        t0 = time.time()
        segs, info = m.transcribe(audio, **kw)
        txt = " ".join(s.text.strip() for s in segs).strip()
        ts.append(time.time() - t0)
    ts.sort()
    return ts[len(ts) // 2], txt


print()
print("=" * 84)
print("① GPU（RTX 4070, float16）—— 固定成本 vs 音频长度")
print("=" * 84)
mg = WhisperModel("small", device="cuda", compute_type="float16")
print(f"  {'音频秒':>7} {'耗时':>8} {'文本长度':>8}")
print("  " + "-" * 30)
for dur in (0.2, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0):
    a = base[: int(dur * TARGET_RATE)]
    if len(a) < int(dur * TARGET_RATE):
        a = np.tile(base, 4)[: int(dur * TARGET_RATE)]
    t, txt = bench(mg, a, 3, beam_size=5, vad_filter=True,
                   condition_on_previous_text=False, temperature=0.0)
    print(f"  {dur:>7.1f} {t:>8.3f} {len(txt):>8}")

print()
print("=" * 84)
print("② 对照：CPU int8（那是我们原来的路径）")
print("=" * 84)
mc = WhisperModel("small", device="cpu", compute_type="int8", cpu_threads=8)
print(f"  {'音频秒':>7} {'GPU':>8} {'CPU':>8} {'加速比':>7}")
print("  " + "-" * 36)
for dur in (1.0, 4.0, 8.0):
    a = base[: int(dur * TARGET_RATE)]
    if len(a) < int(dur * TARGET_RATE):
        a = np.tile(base, 4)[: int(dur * TARGET_RATE)]
    tg, _ = bench(mg, a, 3, beam_size=5, vad_filter=True,
                  condition_on_previous_text=False, temperature=0.0)
    tc, _ = bench(mc, a, 3, beam_size=5, vad_filter=True,
                  condition_on_previous_text=False, temperature=0.0)
    print(f"  {dur:>7.1f} {tg:>8.3f} {tc:>8.3f} {tc/tg:>6.1f}x")
del mc

print()
print("=" * 84)
print("③ 大模型在 GPU 上可行吗（CPU 上 small 是上限）")
print("=" * 84)
for size in ("small", "medium", "large-v3"):
    try:
        t0 = time.time()
        m = WhisperModel(size, device="cuda", compute_type="float16")
        load = time.time() - t0
        a4 = np.tile(base, 3)[: int(4 * TARGET_RATE)]
        t, txt = bench(m, a4, 2, beam_size=5, vad_filter=True,
                       condition_on_previous_text=False, temperature=0.0)
        print(f"  {size:<10} 加载 {load:5.1f}s　4秒音频 {t:.3f}s　{txt[:50]}")
        del m
    except Exception as e:
        print(f"  {size:<10} ✗ {type(e).__name__}: {str(e)[:90]}")

print()
print("=" * 84)
print("④ 多语种（俄语/越南语）")
print("=" * 84)
for name, path in (("越南语", "samples/vi_sample.wav"),
                   ("越南语长", "samples/vi_long.wav")):
    fp = os.path.join(HERE, path)
    if not os.path.exists(fp):
        continue
    w2 = wave.open(fp, "rb")
    r2 = np.frombuffer(w2.readframes(w2.getnframes()), dtype=np.int16)
    a = to_mono16k(r2, w2.getframerate(), w2.getnchannels())
    for size in ("small", "large-v3"):
        try:
            m = WhisperModel(size, device="cuda", compute_type="float16")
            t, txt = bench(m, a, 2, beam_size=5, vad_filter=True,
                           condition_on_previous_text=False, temperature=0.0)
            print(f"  {name} {len(a)/TARGET_RATE:.1f}s  {size:<10} {t:.2f}s  {txt[:58]}")
            del m
        except Exception as e:
            print(f"  {name} {size} ✗ {str(e)[:60]}")

print()
print("=" * 84)
print("⑤ 对照：远端 qwen3-asr-flash（当前在用）")
print("=" * 84)
try:
    from remote_asr import from_env_or_key
    r = from_env_or_key()
    for dur in (4.0, 12.0):
        a = np.tile(base, 4)[: int(dur * TARGET_RATE)]
        ts = []
        for _ in range(3):
            t0 = time.time()
            r(a)
            ts.append(time.time() - t0)
        ts.sort()
        print(f"  {dur:>5.1f}s 音频 → {ts[1]:.3f}s（中位）")
except Exception as e:
    print(f"  ✗ {e}")
