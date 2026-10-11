# -*- coding: utf-8 -*-
"""混合方案 v2：累积整条文本流，按"句子结束"判定翻译时机。

v1 失败的原因（看清了）：流式交付的是**确认的增量**，有时是片段
（"i never thought i"）、有时是整句（"would see you again get out of
here before i call the police"）。我按"音频静音"判边界 → 失效。

v2 改成：把所有确认文本累积成**一条不断增长的文本流**，
在其中找**句末标点**作为翻译边界。这正好呼应之前的发现 ——
qwen-mt 是翻译引擎不是指令执行者，但对**完整句子**译得最好。

判据：译文要完整（不出现"的"/"我想我会"），落后要接近 0.3s。
"""
import io
import os
import re
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

SENT_END = re.compile(r"[.!?。！？]\s*")


def run_v2(hold_sec=0.9, min_chars=14, update=1.2):
    """累积文本流 + 句末标点判定 + 超时兜底。"""
    from streaming_asr import StreamingTranscriber
    st = StreamingTranscriber(model="medium", language=None,
                              update_interval=update,
                              device="cuda", compute_type="float16")
    buf = ""              # 累积的确认文本
    pend_since = None     # buf 里最后一段的到达时间
    outs = []
    t0 = time.time()
    for i in range(0, len(AUDIO) - step, step):
        pos = (i + step) / TARGET_RATE
        now = time.time() - t0
        if pos > now:
            time.sleep(pos - now)
        st.feed(AUDIO[i:i + step])
        new = [x.strip() for x in st.poll() if x and x.strip()]
        if new:
            buf = (buf + " " + " ".join(new)).strip()
            pend_since = time.time()
        # 判定可以翻译了：句末标点，或攒太久
        ready = False
        if buf:
            m = list(SENT_END.finditer(buf))
            if m and len(buf[:m[-1].end()].strip()) >= min_chars:
                ready = True
            elif pend_since and time.time() - pend_since >= hold_sec \
                    and len(buf) >= min_chars:
                ready = True
        if ready:
            txt, buf, pend_since = buf.strip(), "", None
            if txt:
                zh = tr(txt)
                if zh:
                    outs.append((pos, txt, zh, time.time() - t0 - pos))
    # 收尾
    if buf.strip():
        zh = tr(buf.strip())
        if zh:
            outs.append((pos, buf.strip(), zh, time.time() - t0 - pos))
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
print("=" * 90)
print("① 分块（当前默认）")
print("=" * 90)
c = run_chunked()
if c:
    L = [x[3] for x in c]
    print("  %d 次　首次 %.2fs　中位 %.2fs　译文中位 %d 字符"
          % (len(c), c[0][3], statistics.median(L),
             statistics.median([len(x[2]) for x in c])))
    for pos, en, zh, lag in c[:5]:
        print("    t=%5.1fs 落后%.2fs  %s" % (pos, lag, zh[:62]))

print()
print("=" * 90)
print("② 混合 v2：累积文本流 + 句末标点")
print("=" * 90)
for hold, mc in ((0.6, 12), (0.9, 14), (1.4, 16)):
    h = run_v2(hold_sec=hold, min_chars=mc)
    if not h:
        print("  hold=%.1f  没有产出" % hold)
        continue
    L = [x[3] for x in h]
    print()
    print("  【hold=%.1fs min=%d】%d 次　首次 %.2fs　中位 %.2fs　译文中位 %d 字符"
          % (hold, mc, len(h), h[0][3], statistics.median(L),
             statistics.median([len(x[2]) for x in h])))
    for pos, en, zh, lag in h[:7]:
        print("    t=%5.1fs 落后%.2fs  %s" % (pos, lag, zh[:64]))

print()
print("=" * 90)
print("对比（译文中位字符数太短 = 碎）")
print("=" * 90)
if c:
    print("  分块    ：落后中位 %.2fs　译文中位 %d 字符"
          % (statistics.median([x[3] for x in c]),
             statistics.median([len(x[2]) for x in c])))
