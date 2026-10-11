# -*- coding: utf-8 -*-
"""越南语→中文：对比两种"更少中间环节"的路子。
   P1 argos 中继 vi→en→zh（已知会崩）
   P2 whisper task=translate（越南语→英语是 whisper 的在行方向）+ argos en→zh
   P3 whisper task=translate + 术语修正 + argos en→zh
   用真实新闻音频段（whisper 识别本身已校正为 vi）。
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
from faster_whisper import WhisperModel
import argostranslate.translate as AT

w = wave.open(os.path.join(HERE, "vi_news.wav"), "rb")
raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
audio = to_mono16k(raw, w.getframerate(), w.getnchannels())

m = WhisperModel("small", device="cpu", compute_type="int8", cpu_threads=8)
langs = {l.code: l for l in AT.get_installed_languages()}
en2zh = langs["en"].get_translation(langs["zh"])
vi2en = langs["vi"].get_translation(langs["en"])

SEG = 4.0
n = int(len(audio) / 16000 / SEG)
print(f"素材 {len(audio)/16000:.1f}s，切 {n} 段\n")
print("=" * 100)
i = 0
for k in range(n):
    seg = audio[int(k * SEG * 16000):int((k + 1) * SEG * 16000)]
    vi = " ".join(s.text.strip() for s in
                  list(m.transcribe(seg, language="vi", beam_size=5, vad_filter=True)[0]))
    if len(vi) < 10:
        continue
    i += 1
    t0 = time.time()
    p1 = en2zh.translate(vi2en.translate(vi))
    t1 = time.time() - t0
    t0 = time.time()
    en2 = " ".join(s.text.strip() for s in
                   list(m.transcribe(seg, task="translate", beam_size=5,
                                     vad_filter=True)[0]))
    p2 = en2zh.translate(en2)
    t2 = time.time() - t0
    print(f"[{i}] VI: {vi[:70]}")
    print(f"    P1 中继 ({t1:.2f}s): {p1[:72]}")
    print(f"    P2 whisper英译 ({t2:.2f}s): EN={en2[:60]}")
    print(f"                        ZH={p2[:72]}")
    print()
    if i >= 6:
        break
