# -*- coding: utf-8 -*-
"""越南语识别与翻译测试：
   ① whisper 自动检测语言（看它认不认越南语）
   ② 强制 --lang vi 的识别质量
   ③ 强制 --lang en 的识别（复现"错得离谱"）
   ④ 翻译路径对比：MyMemory vi→zh / argos vi→en→zh / 直连其他端点
"""
import io
import os
import ssl
import sys
import time
import urllib.parse
import urllib.request
import wave

import numpy as np

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from live_subtitle_v2 import to_mono16k
from faster_whisper import WhisperModel

WAV = os.path.join(HERE, "vi_news.wav")
w = wave.open(WAV, "rb")
raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
audio = to_mono16k(raw, w.getframerate(), w.getnchannels())
print(f"样本 {len(audio)/16000:.1f} 秒\n")

m = WhisperModel("small", device="cpu", compute_type="int8", cpu_threads=8)

print("=" * 74)
print("【一】语言检测（不指定语言）")
print("=" * 74)
segs, info = m.transcribe(audio, language=None, beam_size=5, vad_filter=True)
segs = list(segs)
print(f"  检出语言 = {info.language}  置信度 {info.language_probability:.3f}")
print(f"  全部候选（前 5）:")
for lp in (info.all_language_probs or [])[:5]:
    print(f"    {lp[0]:<6} {lp[1]:.3f}")
txt_auto = " ".join(s.text.strip() for s in segs)
print(f"  识别结果：{txt_auto[:180]}")

print()
print("=" * 74)
print("【二】强制 vi（正确做法）")
print("=" * 74)
t0 = time.time()
segs, info = m.transcribe(audio, language="vi", beam_size=5, vad_filter=True)
segs = list(segs)
el = time.time() - t0
txt_vi = " ".join(s.text.strip() for s in segs)
print(f"  耗时 {el:.2f}s")
print(f"  识别结果：{txt_vi[:180]}")

print()
print("=" * 74)
print("【三】强制 en（复现你看到的错误）")
print("=" * 74)
segs, info = m.transcribe(audio, language="en", beam_size=5, vad_filter=True)
segs = list(segs)
txt_en = " ".join(s.text.strip() for s in segs)
print(f"  识别结果：{txt_en[:180]}")

print()
print("=" * 74)
print("【四】翻译路径")
print("=" * 74)
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
UA = "Mozilla/5.0"

# 4a MyMemory vi->zh
try:
    u = ("https://api.mymemory.translated.net/get?q="
         + urllib.parse.quote(txt_vi[:480]) + "&langpair=vi|zh-CN")
    r = urllib.request.Request(u, headers={"User-Agent": UA})
    b = urllib.request.urlopen(r, timeout=15, context=CTX).read().decode("utf-8", "replace")
    import json
    j = json.loads(b)
    print(f"  [MyMemory vi→zh] {(j.get('responseData') or {}).get('translatedText','')[:200]}")
except Exception as e:
    print(f"  [MyMemory vi→zh] 失败 {type(e).__name__} {str(e)[:70]}")

# 4b argos vi->en 需要先装
try:
    import argostranslate.translate as T
    langs = {l.code: l for l in T.get_installed_languages()}
    print(f"  已装语言: {sorted(langs)}")
    if "vi" in langs and "en" in langs:
        tr = langs["vi"].get_translation(langs["en"])
        if tr:
            print(f"  [argos vi→en] {tr.translate(txt_vi[:300])[:200]}")
    else:
        print("  [argos vi→en] 未安装 vi 包（可下载 vi->en，再用 en->zh）")
except Exception as e:
    print(f"  [argos] 失败 {type(e).__name__} {str(e)[:70]}")

print()
print("=" * 74)
print("【五】结论")
print("=" * 74)
print(f"  自动检测：{info.language}")
print(f"  强制 vi 与强制 en 的结果差异 → 见上")
