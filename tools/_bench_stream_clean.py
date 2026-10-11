# -*- coding: utf-8 -*-
"""流式 vs 分块 —— 用**识别干净**的素材重测（可信结论）。

上一版用 vi_long.wav，那段 ASR 本身糊（重复、错字），两边都被上游污染，
比不出下游差别；而且我把"落后"测在了翻译之前（恒为 0）。

这次：
# ── 样本检查 ─────────────────────────────────────────────
# tests/e2e_speech.wav 随仓库发布（samples/ 下）。缺失就友好退出。
def _need_sample(p):
    """确认样本存在，否则给出提示后退出。"""
    if os.path.exists(p):
        return p
    print("跳过：缺少 " + p)
    print("本仓库自带 samples/e2e_speech.wav；请确认已完整 clone。")
    raise SystemExit(0)

  · 素材用 e2e_speech.wav（英文，whisper 全对）+ loopback_test.wav 交替
  · 落后测在**译文产出之后**（这才是用户感知的延迟）
  · 两边都走 GPU + 同一套翻译

判据：译文产出的落后秒数（中位/首次），以及译文本身是否可读。
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
from live_subtitle_v2 import to_mono16k, Segmenter, NoiseGate, TARGET_RATE, Translator

HERE = "D:/NEW WORK/work/subtitle"


def load(rel, n=4):
    p = os.path.join(HERE, rel)
    w = wave.open(p, "rb")
    raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    a = to_mono16k(raw, w.getframerate(), w.getnchannels())
    return np.tile(a, n)


A = load(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples", "e2e_speech.wav"), 3)
B = load("loopback_test.wav", 3)
# 交替拼接，模拟"连续说话"
n = min(len(A), len(B))
AUDIO = np.concatenate([A[:n], B[:n]])
AUDIO = AUDIO[: int(70 * TARGET_RATE)]
DUR = len(AUDIO) / TARGET_RATE
print("  素材 e2e_speech + loopback_test 交替 = %.1f 秒（英文，识别干净）" % DUR)

tr = Translator("auto", "zh")
step = int(0.01 * TARGET_RATE)


def run_chunked():
    from faster_whisper import WhisperModel
    m = WhisperModel("medium", device="cuda", compute_type="float16")
    seg = Segmenter(min_len=0.5, max_len=4.0, silence=0.45)
    gate = NoiseGate()
    pend, outs = [], []
    t0 = time.time()
    for i in range(0, len(AUDIO) - step, step):
        pos = (i + step) / TARGET_RATE
        now = time.time() - t0
        if pos > now:
            time.sleep(pos - now)
        for s in seg.push(AUDIO[i:i + step]):
            if gate.is_speech(s):
                pend.append(s)
        if pend and sum(len(x) for x in pend) / TARGET_RATE >= 3.0:
            segs, _ = m.transcribe(np.concatenate(pend), beam_size=5,
                                   vad_filter=True,
                                   condition_on_previous_text=False,
                                   temperature=0.0)
            txt = " ".join(s.text.strip() for s in segs).strip()
            pend = []
            if txt:
                zh = tr(txt)
                if zh:
                    outs.append((pos, txt, zh, time.time() - t0 - pos))
    del m
    return outs


def run_streaming():
    from streaming_asr import StreamingTranscriber
    st = StreamingTranscriber(model="medium", language=None,
                              update_interval=1.2,
                              device="cuda", compute_type="float16")
    outs = []
    t0 = time.time()
    for i in range(0, len(AUDIO) - step, step):
        pos = (i + step) / TARGET_RATE
        now = time.time() - t0
        if pos > now:
            time.sleep(pos - now)
        st.feed(AUDIO[i:i + step])
        for inc in st.poll():
            if inc and inc.strip():
                zh = tr(inc.strip())
                if zh:
                    outs.append((pos, inc.strip(), zh, time.time() - t0 - pos))
    return outs


print()
print("=" * 86)
print("① 分块（当前默认，GPU + 翻译）")
print("=" * 86)
c = run_chunked()
print("  产出 %d 次" % len(c))
if c:
    lags = sorted(x[3] for x in c)
    print("  落后：首次 %.2fs　中位 %.2fs　P90 %.2fs　最大 %.2fs"
          % (c[0][3], statistics.median(lags), lags[int(len(lags) * 0.9)],
             max(lags)))
print()
for pos, en, zh, lag in c[:8]:
    print("    t=%5.1fs 落后%.2fs" % (pos, lag))
    print("        原: %s" % en[:66])
    print("        译: %s" % zh[:66])

print()
print("=" * 86)
print("② 流式（LocalAgreement-2，GPU + 翻译）")
print("=" * 86)
s = run_streaming()
print("  产出 %d 次" % len(s))
if s:
    lags = sorted(x[3] for x in s)
    print("  落后：首次 %.2fs　中位 %.2fs　P90 %.2fs　最大 %.2fs"
          % (s[0][3], statistics.median(lags), lags[int(len(lags) * 0.9)],
             max(lags)))
print()
for pos, en, zh, lag in s[:10]:
    print("    t=%5.1fs 落后%.2fs" % (pos, lag))
    print("        原: %s" % en[:66])
    print("        译: %s" % zh[:66])

print()
print("=" * 86)
print("对比")
print("=" * 86)
if c and s:
    print("  分块：%d 次　落后中位 %.2fs　首次 %.2fs"
          % (len(c), statistics.median([x[3] for x in c]), c[0][3]))
    print("  流式：%d 次　落后中位 %.2fs　首次 %.2fs"
          % (len(s), statistics.median([x[3] for x in s]), s[0][3]))
