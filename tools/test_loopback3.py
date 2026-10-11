# -*- coding: utf-8 -*-
"""环回验证 v3：自己生成测试音频 → 播放 → 同时环回捕获。
   上一版教训：WASAPI 环回只在系统真在出声时才回数据，
   没播放 = 0 回调 = 峰值 0。所以必须自带音源。
   音源用一段可识别的语音更好：用 Windows 内置 TTS 生成 wav，内容已知，
   这样连 whisper 的识别都能一起验。
"""
import os
import subprocess
import sys
import threading
import time
import wave

import numpy as np
import pyaudiowpatch as pyaudio

OUT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.makedirs(OUT, exist_ok=True)
SRC_WAV = os.path.join(OUT, "tts_source.wav")
CAP_WAV = os.path.join(OUT, "loopback_test.wav")
SECONDS = 14
TEXT = "Hello, this is a test of the loopback capture system. One two three four five."

# ── ① 用 Windows 内置 TTS 生成音源（内容已知，便于验证识别）─────
ps = f'''
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.SetOutputToWaveFile("{SRC_WAV}")
$s.Speak("{TEXT}")
$s.Dispose()
'''
r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
print("TTS 生成:", "OK" if os.path.exists(SRC_WAV) else f"失败 {r.stderr[:200]}")
if os.path.exists(SRC_WAV):
    w = wave.open(SRC_WAV, "rb")
    print(f"  音源 {w.getnframes()/w.getframerate():.1f} 秒 {w.getframerate()}Hz {w.getnchannels()}ch")

# ── ② 环回捕获 ────────────────────────────────────────────────
p = pyaudio.PyAudio()
lb = p.get_wasapi_loopback_analogue_by_dict(p.get_default_output_device_info())
CH = min(2, lb["maxInputChannels"]); RATE = int(lb["defaultSampleRate"])
print(f"环回 [{lb['index']}] rate={RATE} ch={CH}")

chunks, err = [], []


def cb(in_data, fc, ti, status):
    try:
        if in_data:
            chunks.append(np.frombuffer(in_data, dtype=np.int16).copy())
    except Exception as e:
        err.append(f"{type(e).__name__}: {e}")
    return (None, pyaudio.paContinue)


st = p.open(format=pyaudio.paInt16, channels=CH, rate=RATE, input=True,
            input_device_index=lb["index"], frames_per_buffer=1024,
            stream_callback=cb)
st.start_stream()

# ── ③ 播放（异步，循环播放以覆盖整个捕获窗）────────────────────
print("播放音源…")
play = subprocess.Popen(
    ["powershell", "-NoProfile", "-Command",
     f'$pl = New-Object System.Media.SoundPlayer "{SRC_WAV}"; '
     f'1..3 | ForEach-Object {{ $pl.PlaySync() }}'],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

last = 0
t0 = time.time()
while time.time() - t0 < SECONDS:
    time.sleep(1)
    tot = sum(len(c) for c in chunks)
    print(f"  t={time.time()-t0:4.1f}s  样本 {tot:>8}  (+{tot-last})  active={st.is_active()}")
    last = tot

st.stop_stream(); st.close(); p.terminate()
try:
    play.wait(timeout=3)
except Exception:
    play.kill()

if err:
    print("回调异常:", err[:3])

raw = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.int16)
dur = len(raw) / RATE / CH if RATE and CH else 0
peak = int(np.abs(raw).max()) if len(raw) else 0
rms = float(np.sqrt((raw.astype(np.float64) ** 2).mean())) if len(raw) else 0.0
print(f"\n** 抓到 {dur:.1f} 秒　峰值 {peak}　RMS {rms:.1f} **")

with wave.open(CAP_WAV, "wb") as w:
    w.setnchannels(CH); w.setsampwidth(2); w.setframerate(RATE)
    w.writeframes(raw.tobytes())

# ── ④ whisper 验证（识别结果应接近已知文本）────────────────────
if peak > 300:
    print("\n=== whisper 识别 ===")
    from faster_whisper import WhisperModel
    a = raw.astype(np.float32) / 32768.0
    if CH == 2:
        a = a.reshape(-1, 2).mean(axis=1)
    if RATE != 16000:
        idx = np.clip((np.arange(int(len(a)/RATE*16000))*RATE/16000).astype(np.int64),
                      0, len(a)-1)
        a = a[idx]
    m = WhisperModel("small", device="cpu", compute_type="int8", cpu_threads=4)
    t1 = time.time()
    segs, info = m.transcribe(a, language="en", beam_size=5, vad_filter=True)
    segs = list(segs)
    el = time.time() - t1
    got = " ".join(s.text.strip() for s in segs)
    print(f"  语言 {info.language} {info.language_probability:.2f}  转写耗时 {el:.1f}s"
          f"  实时比 {dur/el if el else 0:.1f}×")
    print(f"  识别：{got[:220]}")
    print(f"  原文：{TEXT}")
    # 简单词重合
    kw = set(TEXT.lower().replace(",", "").replace(".", "").split())
    gw = set(got.lower().replace(",", "").replace(".", "").split())
    print(f"  词重合度 {len(kw & gw)/len(kw)*100:.0f}%")
else:
    print("\n** 仍然静音：环回没拿到系统声音（可能播放器没走默认输出设备）**")
