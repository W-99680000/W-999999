# -*- coding: utf-8 -*-
"""bench/_poc_real_vi.py —— 真实越南语视频音频的 A/B 对比（无参照，定性 + 耗时）。

BV1mQ8EzsEpR.m4a 是此前 large-v3 处理过的越南语素材（见 url_cache meta）。
没有原文参照，比较方式：全文输出并排看、复读/幻觉迹象、分段数、耗时。
"""
from __future__ import annotations

import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

M4A = os.path.join(HERE, "pre_cache", "url_cache", "BV1mQ8EzsEpR.m4a")
QWEN_DIR = os.path.join(HERE, "models", "Qwen3-ASR-0.6B")


def load_audio(path, limit_s=0):
    import av
    import numpy as np
    from av.audio.resampler import AudioResampler
    resampler = AudioResampler(format="flt", layout="mono", rate=16000)
    chunks, nmax = [], int(limit_s * 16000) if limit_s else 0
    with av.open(path) as c:
        for frame in c.decode(audio=0):
            for rf in resampler.resample(frame):
                chunks.append(rf.to_ndarray().reshape(-1))
    a = np.concatenate(chunks).astype(np.float32)
    return a[:nmax] if nmax else a


def repeats(text, n=4):
    """粗略复读检测：连续 n-gram 重复出现的比例。"""
    ws = text.lower().split()
    if len(ws) < n * 2:
        return 0.0
    grams = [tuple(ws[i:i + n]) for i in range(len(ws) - n + 1)]
    from collections import Counter
    dup = sum(v - 1 for v in Counter(grams).values() if v > 1)
    return dup / max(1, len(grams))


def main():
    audio = load_audio(M4A)
    dur = len(audio) / 16000
    print(f"音频 {dur:.1f}s（越南语真实素材）\n")

    import pretranslate as P
    model = P.load_asr("medium")
    t0 = time.time()
    segs = list(model.transcribe(audio, language="vi", beam_size=5,
                                 vad_filter=True, word_timestamps=True,
                                 condition_on_previous_text=True)[0])
    dt = time.time() - t0
    wtext = " ".join(s.text for s in segs).strip()
    print(f"[whisper-medium] {dt:.1f}s, {len(segs)} 段, 复读率 {repeats(wtext):.2%}")
    print("  " + wtext[:400].replace("\n", " ") + "\n")
    del model

    import torch
    from qwen_asr import Qwen3ASRModel
    qmodel = Qwen3ASRModel.from_pretrained(
        QWEN_DIR, dtype=torch.float32, device_map="cpu",
        max_inference_batch_size=1, max_new_tokens=448)
    t0 = time.time()
    out = qmodel.transcribe(audio=(audio, 16000), language="Vietnamese")[0]
    dt = time.time() - t0
    qtext = (out.text or "").strip()
    print(f"[qwen3-asr-0.6b] {dt:.1f}s, lang={out.language}, "
          f"复读率 {repeats(qtext):.2%}")
    print("  " + qtext[:400].replace("\n", " "))


if __name__ == "__main__":
    raise SystemExit(main())
