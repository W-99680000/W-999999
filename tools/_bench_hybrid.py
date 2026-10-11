# -*- coding: utf-8 -*-
"""混合方案：流式识别（快） + 句子边界后才翻译（译文完整）。

问题（上一个脚本实测）：
    分块  首次 1.25s  中位 0.93s  译文完整 ✓
    流式  首次 0.30s  中位 0.30s  译文碎 ✗
          （"我从没想过我" / "事实是，我一直……" / "的"）

原因：流式交付的是**增量**，1.2 秒一小段，直接送翻译当然碎。

但这两件事本来就不必绑在一起：
    识别快  ← 流式（LocalAgreement-2，每 1.2 秒一轮）
    译文完整 ← 等**句子边界**再翻译（这一点分块路线早就做对了：
              靠 Segmenter 的静音切分判定自然句边界）

所以混合方案 = 流式出原文 + 同一条"等边界"逻辑决定何时翻译。

判据（两条都要）：
  · 落后秒数（要接近流式的 0.3s，不能退回 0.9s）
  · 译文是否完整（要接近分块的质量，不能出现"的"/"我想我会"）
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
from live_subtitle_v2 import to_mono16k, TARGET_RATE, Translator

HERE = "D:/NEW WORK/work/subtitle"


def load(rel, n):
    p = os.path.join(HERE, rel)
    w = wave.open(p, "rb")
    raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return np.tile(to_mono16k(raw, w.getframerate(), w.getnchannels()), n)


# ── 样本检查 ─────────────────────────────────────────────
# tests/e2e_speech.wav 随仓库发布（samples/ 下）。缺失就友好退出。
def _need_sample(p):
    """确认样本存在，否则给出提示后退出。"""
    if os.path.exists(p):
        return p
    print("跳过：缺少 " + p)
    print("本仓库自带 samples/e2e_speech.wav；请确认已完整 clone。")
    raise SystemExit(0)

A = load(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples", "e2e_speech.wav"), 3)
B = load("loopback_test.wav", 3)
n = min(len(A), len(B))
AUDIO = np.concatenate([A[:n], B[:n]])[: int(70 * TARGET_RATE)]
print("  素材 70 秒英文（识别干净）")
tr = Translator("auto", "zh")
step = int(0.01 * TARGET_RATE)


def run_hybrid(pause_sec=0.45, max_chars=90, update=1.2):
    """流式识别 + 静音边界判定后才翻译。"""
    from streaming_asr import StreamingTranscriber
    st = StreamingTranscriber(model="medium", language=None,
                              update_interval=update,
                              device="cuda", compute_type="float16")
    buf = []              # 待翻译的原文片段
    outs = []
    quiet = 0.0           # 连续静音秒数
    t0 = time.time()
    # 静音判定：用喂进去的音频自己算 RMS（和 Segmenter 同一个量纲）
    for i in range(0, len(AUDIO) - step, step):
        blk = AUDIO[i:i + step]
        pos = (i + step) / TARGET_RATE
        now = time.time() - t0
        if pos > now:
            time.sleep(pos - now)
        rms = float(np.sqrt((blk.astype(np.float64) ** 2).mean()))
        quiet = quiet + 0.01 if rms < 0.008 else 0.0
        st.feed(blk)
        new = [x.strip() for x in st.poll() if x and x.strip()]
        buf.extend(new)
        nchar = len(" ".join(buf))
        # 句子边界：静音够久，或攒得太长
        if buf and (quiet >= pause_sec or nchar >= max_chars):
            txt = " ".join(buf).strip()
            buf = []
            quiet = 0.0
            if txt:
                zh = tr(txt)
                if zh:
                    outs.append((pos, txt, zh, time.time() - t0 - pos))
    return outs


def run_chunked():
    from faster_whisper import WhisperModel
    from live_subtitle_v2 import Segmenter, NoiseGate
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


print()
print("=" * 88)
print("① 分块（当前默认）")
print("=" * 88)
c = run_chunked()
if c:
    L = [x[3] for x in c]
    print("  %d 次　首次 %.2fs　中位 %.2fs" % (len(c), c[0][3], statistics.median(L)))
    for pos, en, zh, lag in c[:6]:
        print("    t=%5.1fs 落后%.2fs  %s" % (pos, lag, zh[:60]))

print()
print("=" * 88)
print("② 混合：流式识别 + 句子边界后翻译")
print("=" * 88)
for pause in (0.30, 0.45, 0.70):
    h = run_hybrid(pause_sec=pause)
    if not h:
        print("  pause=%.2f  没有产出" % pause)
        continue
    L = [x[3] for x in h]
    print()
    print("  【静音阈值 %.2fs】%d 次　首次 %.2fs　中位 %.2fs"
          % (pause, len(h), h[0][3], statistics.median(L)))
    for pos, en, zh, lag in h[:6]:
        print("    t=%5.1fs 落后%.2fs  %s" % (pos, lag, zh[:62]))

print()
print("=" * 88)
print("判定")
print("=" * 88)
print("  要同时看两个数：落后秒数（越小越好）与译文长度（太短就是碎的）。")
if c:
    print("  分块基准：中位落后 %.2fs，译文中位 %d 字符"
          % (statistics.median([x[3] for x in c]),
             statistics.median([len(x[2]) for x in c])))
