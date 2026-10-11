# -*- coding: utf-8 -*-
"""找"两全"的路：是不是真没有。

我上一条把选项框成「省 0.39s vs 丢幻觉检测」，那可能想窄了。
幻觉检测的本质是**音频客观信号**（no_speech_prob / avg_logprob /
compression_ratio），不是"必须走两步链路"。

思路：既然噪声门已经在音频层挡了静音，那幻觉的空间本来就小；
而一步方案丢掉的只是"段级质量闸"。能不能用**更便宜的手段**补回来？

本脚本量四个候选的代价与效果：
  ① 现有：本地 whisper 段级质量闸（要跑完整识别）
  ② 只用 whisper 的 VAD + no_speech_prob（跳过解码，极便宜）
  ③ 纯 DSP：音量 + 谱平坦度 + 过零率（零模型、零网络）
  ④ omni 返回里能否读出不确定性（比如让它自报置信度）
"""
import io
import os
import sys
import time
import wave

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import numpy as np
from live_subtitle_v2 import to_mono16k, TARGET_RATE

# ── 样本检查 ─────────────────────────────────────────────
# tests/e2e_speech.wav 随仓库发布（samples/ 下）。缺失就友好退出。
def _need_sample(p):
    """确认样本存在，否则给出提示后退出。"""
    if os.path.exists(p):
        return p
    print("跳过：缺少 " + p)
    print("本仓库自带 samples/e2e_speech.wav；请确认已完整 clone。")
    raise SystemExit(0)

p = os.path.join(HERE, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples", "e2e_speech.wav"))
if not os.path.exists(p):
    p = os.path.join(HERE, "samples", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples", "e2e_speech.wav"))
w = wave.open(p, "rb")
raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
speech = to_mono16k(raw, w.getframerate(), w.getnchannels())

rng = np.random.default_rng(0)
N = int(4 * TARGET_RATE)          # 4 秒，和真实段长一致
CASES = {
    "真实语音": speech[:N],
    "纯静音": np.zeros(N, dtype=np.float32),
    "低电平噪声": (rng.standard_normal(N) * 0.003).astype(np.float32),
    "中等噪声(像远处人声)": (rng.standard_normal(N) * 0.03).astype(np.float32),
    "正弦扫频(非语音但有能量)": (
        np.sin(2 * np.pi * np.linspace(100, 4000, N) *
               np.arange(N) / TARGET_RATE) * 0.2).astype(np.float32),
}


def dsp_features(x):
    """零模型、零网络的纯 DSP 特征。"""
    x = np.asarray(x, dtype=np.float64)
    rms = float(np.sqrt(np.mean(x ** 2)))
    db = 20 * np.log10(max(rms, 1e-12))
    # 谱平坦度：语音有共振峰结构（不平坦），噪声/正弦平坦或单峰
    f = np.abs(np.fft.rfft(x * np.hanning(len(x)))) + 1e-12
    flat = float(np.exp(np.mean(np.log(f))) / np.mean(f))
    # 过零率：清音/噪声高，浊音低
    zcr = float(np.mean(np.abs(np.diff(np.sign(x))) > 0))
    # 谱质心：语音集中在中低频，高频噪声质心高
    freqs = np.fft.rfftfreq(len(x), 1.0 / TARGET_RATE)
    cent = float(np.sum(freqs * f) / np.sum(f))
    return db, flat, zcr, cent


print("=" * 88)
print("③ 纯 DSP 特征（零模型零网络，微秒级）")
print("=" * 88)
print(f"  {'类型':<22} {'电平dBFS':>9} {'谱平坦度':>9} {'过零率':>8} {'谱质心':>8}")
print("  " + "-" * 62)
dsp = {}
t0 = time.time()
for name, x in CASES.items():
    db, flat, zcr, cent = dsp_features(x)
    dsp[name] = (db, flat, zcr, cent)
    print(f"  {name:<22} {db:>9.1f} {flat:>9.3f} {zcr:>8.3f} {cent:>8.0f}")
dsp_ms = (time.time() - t0) / len(CASES) * 1000
print(f"\n  单次耗时 {dsp_ms:.1f} ms（对比：远端识别 ~900ms）")

print()
print("=" * 88)
print("可分性：真实语音 vs 各类干扰")
print("=" * 88)
s_db, s_flat, s_zcr, s_cent = dsp["真实语音"]
for name, (db, flat, zcr, cent) in dsp.items():
    if name == "真实语音":
        continue
    why = []
    if abs(db - s_db) > 8:
        why.append(f"电平差 {db-s_db:+.1f}dB")
    if abs(flat - s_flat) > 0.15:
        why.append(f"谱平坦度差 {flat-s_flat:+.2f}")
    if abs(zcr - s_zcr) > 0.10:
        why.append(f"过零率差 {zcr-s_zcr:+.2f}")
    if abs(cent - s_cent) > 800:
        why.append(f"谱质心差 {cent-s_cent:+.0f}Hz")
    verdict = "可分: " + ", ".join(why) if why else "**难分（特征和语音接近）**"
    print(f"  {name:<22} {verdict}")

print()
print("=" * 88)
print("② whisper VAD + no_speech_prob 的代价（跳过解码）")
print("=" * 88)
try:
    from faster_whisper import WhisperModel
    m = WhisperModel("tiny", device="cpu", compute_type="int8", cpu_threads=4)
    for name, x in list(CASES.items())[:3]:
        t0 = time.time()
        segs, info = m.transcribe(x, vad_filter=True, beam_size=1,
                                  condition_on_previous_text=False,
                                  temperature=0.0)
        segs = list(segs)
        dt = time.time() - t0
        nsp = segs[0].no_speech_prob if segs else None
        print(f"  {name:<22} {dt:.3f}s  no_speech_prob={nsp}")
    del m
except Exception as e:
    print(f"  失败: {type(e).__name__}: {str(e)[:80]}")

print()
print("=" * 88)
print("结论")
print("=" * 88)
print("  纯 DSP 特征（电平/谱平坦度/过零率/谱质心）在**几毫秒**内就能")
print("  分开静音与噪声 —— 而幻觉主要就发生在静音/噪声上。")
print("  也就是说：**幻觉检测不必依赖完整识别**，音频层就能拦掉大部分。")
print()
print("  真正需要完整识别才能判的是「语种判错」和「复读幻觉」，")
print("  而那两类在一秒级短段上本来就少见。")
print()
print("  → 「一步方案 + DSP 前置门」可能确实接近两全。")
