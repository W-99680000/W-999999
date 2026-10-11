# -*- coding: utf-8 -*-
"""越南语→中文：测更稳的两条路，对比 MyMemory 的垃圾输出。
   A MyMemory 直译（已知会返回维基存档模板）
   B argos 中继 vi→en→zh（纯离线，0.4s）
   C whisper task=translate 直接英译 + argos en→zh（whisper 对越南语→英语是在行任务）
   用真实新闻音频，抓长一点以覆盖多个句子。
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
import argostranslate.translate as AT

# 用之前抓的样本 + 新抓一段，凑够内容
# ── 样本检查 ─────────────────────────────────────────────────────
# 这里用的越南语新闻音频是操作者自录的，未随仓库发布。
# 想跑：把任意越南语音频命名成 vi_news.wav 放进 samples/ 即可。
_S = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples")
_VI = next((os.path.join(_S, n) for n in ("vi_news.wav", "vi_news.wav",
                                          "vi_news.wav")
            if os.path.exists(os.path.join(_S, n))), None)
if _VI is None:
    print("跳过：samples/ 下没有越南语测试音频。")
    print("把任意越南语音频命名成 vi_news.wav 放进 samples/ 即可运行。")
    raise SystemExit(0)
WAVS = [_VI]

audios = []
for p in WAVS:
    if os.path.exists(p):
        w = wave.open(p, "rb")
        raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        audios.append(to_mono16k(raw, w.getframerate(), w.getnchannels()))
audio = np.concatenate(audios)
print(f"素材 {len(audio)/16000:.1f} 秒\n")

m = WhisperModel("small", device="cpu", compute_type="int8", cpu_threads=8)
langs = {l.code: l for l in AT.get_installed_languages()}
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE


def mymemory(text, pair="vi|zh-CN"):
    try:
        u = ("https://api.mymemory.translated.net/get?q="
             + urllib.parse.quote(text[:480]) + f"&langpair={pair}")
        r = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
        b = urllib.request.urlopen(r, timeout=15, context=CTX).read().decode("utf-8", "replace")
        import json
        return (json.loads(b).get("responseData") or {}).get("translatedText", "")
    except Exception as e:
        return f"[失败 {type(e).__name__}]"


# 把音频切成 4 秒段，逐段对比三条路
SEG = 4.0
print(f"{'原（越南语）':<52}{'A MyMemory':<40}{'B argos中继':<34}{'C whisper英译→zh'}")
print("-" * 170)
for k in range(0, int(len(audio) / 16000 / SEG)):
    seg = audio[int(k * SEG * 16000):int((k + 1) * SEG * 16000)]
    vi = " ".join(s.text.strip() for s in
                  list(m.transcribe(seg, language="vi", beam_size=5, vad_filter=True)[0]))
    if len(vi) < 8:
        continue
    a = mymemory(vi)
    try:
        b = langs["en"].get_translation(langs["zh"]).translate(
            langs["vi"].get_translation(langs["en"]).translate(vi))
    except Exception as e:
        b = f"[失败 {type(e).__name__}]"
    try:
        en = " ".join(s.text.strip() for s in
                      list(m.transcribe(seg, task="translate", beam_size=5,
                                        vad_filter=True)[0]))
        c = langs["en"].get_translation(langs["zh"]).translate(en)
    except Exception as e:
        c = f"[失败 {type(e).__name__}]"
    bad = "**垃圾**" if ("存档" in a or "英语" in a and len(a) < 40) else ""
    print(f"{vi[:50]:<52}{a[:38]:<40}{b[:32]:<34}{c[:38]} {bad}")

print()
print("=" * 170)
print("结论看：哪一列是可读的中文。带**垃圾**的是 MyMemory 返回了维基模板。")
