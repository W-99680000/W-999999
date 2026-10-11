# -*- coding: utf-8 -*-
"""流式 vs 分块，**两边都用 GPU**，并且把"等句子说完"算进基线。

之前那次评估（CPU 时代）结论是"流式只快 0.18 秒"，那个结论有两个问题：
  ① 当时我误判"本机没有 GPU"，流式跑在 CPU 上（medium 2.13s/4 秒段）
  ② **漏算了"等句子说完"** —— 实测它占总链路 74.6%（约 1500ms）

本脚本按真实播放速率喂音频，量**每种方案在音频位置 t 处能显示出什么**，
即真正的"落后多少秒"。

两种方案：
  分块（当前默认）  攒到 max_seg 或静音 → 整段识别 → 翻译 → 显示
  流式（LocalAgreement-2）  每 update_interval 秒跑一次前向 → 出稳定增量 → 翻译 → 显示

判据：**首次出字时间** 与 **平均落后秒数**。
"""
import io
import os
import statistics
import sys
import time
import wave

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, "D:/NEW WORK/work/subtitle")

import numpy as np
from gpu_setup import enable_cuda_dlls
enable_cuda_dlls()
from live_subtitle_v2 import (to_mono16k, Segmenter, NoiseGate, TARGET_RATE,
                              Translator)
from llm_translate import translate_batch

# ── 素材 ────────────────────────────────────────────────────
SRC = None
for p in ("D:/NEW WORK/work/subtitle/samples/vi_long.wav",
          "D:/NEW WORK/work/subtitle/samples/vi_sample.wav"):
    if os.path.exists(p):
        SRC = p
        break
w = wave.open(SRC, "rb")
raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
base = to_mono16k(raw, w.getframerate(), w.getnchannels())
AUDIO = np.tile(base, 3)[: int(60 * TARGET_RATE)]
DUR = len(AUDIO) / TARGET_RATE
print("  素材 %s ×3 = %.1f 秒" % (os.path.basename(SRC), DUR))

tr = Translator("auto", "zh")


def run_chunked(max_seg=4.0, cap=3.0, use_gpu=True):
    """当前默认路线：分块识别（GPU），攒单元后合并翻译。"""
    from faster_whisper import WhisperModel
    dev = "cuda" if use_gpu else "cpu"
    ct = "float16" if use_gpu else "int8"
    m = WhisperModel("medium", device=dev, compute_type=ct)
    seg = Segmenter(min_len=0.5, max_len=max_seg, silence=0.45)
    gate = NoiseGate()
    step = int(0.01 * TARGET_RATE)
    pend, units = [], []
    first_out = None
    lags = []
    t0 = time.time()
    for i in range(0, len(AUDIO) - step, step):
        pos = (i + step) / TARGET_RATE
        now = time.time() - t0
        if pos > now:
            time.sleep(pos - now)
        for s in seg.push(AUDIO[i:i + step]):
            if gate.is_speech(s):
                pend.append(s)
        pt = sum(len(x) for x in pend) / TARGET_RATE
        if pend and pt >= cap:
            units.append(np.concatenate(pend))
            pend = []
            segs, _ = m.transcribe(units[-1], beam_size=5, vad_filter=True,
                                   condition_on_previous_text=False,
                                   temperature=0.0)
            txt = " ".join(s.text.strip() for s in segs).strip()
            if txt:
                zh = tr(txt)
                if zh and first_out is None:
                    first_out = time.time() - t0 - pos
                if zh:
                    lags.append(time.time() - t0 - pos)
    del m
    return first_out, lags


def run_streaming(update=1.2, model="medium", use_gpu=True):
    """流式（LocalAgreement-2）。"""
    from streaming_asr import StreamingTranscriber
    dev = "cuda" if use_gpu else "cpu"
    ct = "float16" if use_gpu else "int8"
    st = StreamingTranscriber(model=model, language=None, update_interval=update,
                              device=dev, compute_type=ct)
    step = int(0.01 * TARGET_RATE)
    first_out = None
    lags = []
    t0 = time.time()
    for i in range(0, len(AUDIO) - step, step):
        pos = (i + step) / TARGET_RATE
        now = time.time() - t0
        if pos > now:
            time.sleep(pos - now)
        st.feed(AUDIO[i:i + step])
        for inc in st.poll():
            if inc and inc.strip():
                zh = tr(inc)
                if zh:
                    if first_out is None:
                        first_out = time.time() - t0 - pos
                    lags.append(time.time() - t0 - pos)
    return first_out, lags


print()
print("=" * 78)
print("① 分块（当前默认，GPU）")
print("=" * 78)
f1, l1 = run_chunked(use_gpu=True)
if l1:
    l1s = sorted(l1)
    print("  首次出字落后  %.2f 秒" % f1)
    print("  落后 中位 %.2f　P90 %.2f　最大 %.2f  (n=%d)"
          % (statistics.median(l1s), l1s[int(len(l1s) * 0.9)], max(l1s), len(l1s)))
else:
    print("  没有产出")

print()
print("=" * 78)
print("② 流式（LocalAgreement-2，GPU）")
print("=" * 78)
f2, l2 = run_streaming(use_gpu=True)
if l2:
    l2s = sorted(l2)
    print("  首次出字落后  %.2f 秒" % f2)
    print("  落后 中位 %.2f　P90 %.2f　最大 %.2f  (n=%d)"
          % (statistics.median(l2s), l2s[int(len(l2s) * 0.9)], max(l2s), len(l2s)))
else:
    print("  没有产出")

print()
print("=" * 78)
print("③ 对照：流式在 CPU 上（复现之前那次评估的条件）")
print("=" * 78)
f3, l3 = run_streaming(use_gpu=False)
if l3:
    l3s = sorted(l3)
    print("  首次出字落后  %.2f 秒" % f3)
    print("  落后 中位 %.2f  (n=%d)" % (statistics.median(l3s), len(l3s)))
else:
    print("  没有产出")

print()
print("=" * 78)
print("结论")
print("=" * 78)
if l1 and l2:
    m1, m2 = statistics.median(l1), statistics.median(l2)
    print("  分块 中位 %.2f 秒　流式 中位 %.2f 秒　→ 流式%s %.2f 秒"
          % (m1, m2, "快" if m2 < m1 else "慢", abs(m1 - m2)))
    print("  首次出字：分块 %.2f 秒　流式 %.2f 秒" % (f1, f2))
if l3 and l2:
    print("  流式 CPU 中位 %.2f 秒 vs 流式 GPU 中位 %.2f 秒（GPU 快 %.2f 秒）"
          % (statistics.median(l3), statistics.median(l2),
             statistics.median(l3) - statistics.median(l2)))
