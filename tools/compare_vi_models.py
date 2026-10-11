# -*- coding: utf-8 -*-
"""越南语识别质量：small vs medium（判据是"识别出的越南语本身对不对"）。
   翻译再好也救不回错的识别，所以先在识别这一层比。
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

w = wave.open(os.path.join(HERE, "vi_news.wav"), "rb")
raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
audio = to_mono16k(raw, w.getframerate(), w.getnchannels())
print(f"素材 {len(audio)/16000:.1f} 秒\n")

for name in ("small", "medium"):
    print("=" * 78)
    print(f"模型 {name}")
    print("=" * 78)
    try:
        t0 = time.time()
        m = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=8)
        load = time.time() - t0
        t0 = time.time()
        segs, info = m.transcribe(audio, language="vi", beam_size=5,
                                  vad_filter=True, word_timestamps=False)
        segs = list(segs)
        el = time.time() - t0
        print(f"  加载 {load:.1f}s　识别 {el:.1f}s（{len(audio)/16000/el:.2f}× 实时）")
        for s in segs:
            print(f"    [{s.start:5.1f}-{s.end:5.1f}] {s.text.strip()}")
    except Exception as e:
        print(f"  失败 {type(e).__name__}: {str(e)[:100]}")
    print()

print("=" * 78)
print("人工判据（这句新闻大意）")
print("=" * 78)
print("  下一集：Xung 把自行车拿去卖掉，然后买船回老家。")
print("  → 看哪个模型把 'mua tàu'（买船）听对，而不是 'giết'（杀）")
