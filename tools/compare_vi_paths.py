# -*- coding: utf-8 -*-
"""比较越南语→中文的三条路径，看哪条真的准。
   用刚才抓的真实新闻音频（有已知语义）。
"""
import io
import json
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
import argostranslate.translate as AT

w = wave.open(os.path.join(HERE, "vi_news.wav"), "rb")
raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
audio = to_mono16k(raw, w.getframerate(), w.getnchannels())

m = WhisperModel("small", device="cpu", compute_type="int8", cpu_threads=8)
segs, info = m.transcribe(audio, language="vi", beam_size=5, vad_filter=True)
vi = " ".join(s.text.strip() for s in list(segs))
print(f"越南语原文（whisper small / vi）：\n  {vi}\n")

langs = {l.code: l for l in AT.get_installed_languages()}
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
UA = "Mozilla/5.0"


def mymemory(text, pair):
    try:
        u = ("https://api.mymemory.translated.net/get?q="
             + urllib.parse.quote(text[:480]) + f"&langpair={pair}")
        r = urllib.request.Request(u, headers={"User-Agent": UA})
        b = urllib.request.urlopen(r, timeout=15, context=CTX).read().decode("utf-8", "replace")
        return (json.loads(b).get("responseData") or {}).get("translatedText", "")
    except Exception as e:
        return f"[失败 {type(e).__name__}]"


print("=" * 74)
print("路径 A：MyMemory 直译 vi→zh-CN")
print("=" * 74)
t0 = time.time()
a = mymemory(vi, "vi|zh-CN")
print(f"  {a}\n  （{time.time()-t0:.2f}s）")

print("=" * 74)
print("路径 B：argos 中继 vi→en→zh")
print("=" * 74)
t0 = time.time()
try:
    en = langs["vi"].get_translation(langs["en"]).translate(vi)
    zh = langs["en"].get_translation(langs["zh"]).translate(en)
    print(f"  中间英文：{en}")
    print(f"  中文：{zh}\n  （{time.time()-t0:.2f}s）")
except Exception as e:
    print(f"  失败 {type(e).__name__} {e}")

print("=" * 74)
print("路径 C：whisper 直接翻成英文（task=translate），再 argos en→zh")
print("=" * 74)
t0 = time.time()
try:
    segs2, _ = m.transcribe(audio, task="translate", beam_size=5, vad_filter=True)
    en2 = " ".join(s.text.strip() for s in list(segs2))
    zh2 = langs["en"].get_translation(langs["zh"]).translate(en2)
    print(f"  whisper 英译：{en2}")
    print(f"  中文：{zh2}\n  （{time.time()-t0:.2f}s）")
except Exception as e:
    print(f"  失败 {type(e).__name__} {e}")

print("=" * 74)
print("参考：这句越南语大意（人工判断）")
print("=" * 74)
print("  下一集：Xung 把自行车拿去卖掉，然后买船回老家。")
