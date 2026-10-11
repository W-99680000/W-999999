# -*- coding: utf-8 -*-
"""量 LocalAgreement 的实际可行性：重跑整个缓冲的成本。
   实测依据：耗时 = 0.04×音频秒 + 1.08s。
   缓冲到 8 秒时每次要 1.4s，到 16 秒要 1.7s —— 关键是 MinChunkSize 怎么设。
   这里用真实语音模拟：缓冲从 1 秒涨到 12 秒，每次加 0.5 秒，看每步耗时。
"""
import io
import os
import sys
import time
import wave

import numpy as np

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live_subtitle_v2 import to_mono16k
from faster_whisper import WhisperModel

# ── 样本检查 ─────────────────────────────────────────────
# tests/e2e_speech.wav 随仓库发布（samples/ 下）。缺失就友好退出。
def _need_sample(p):
    """确认样本存在，否则给出提示后退出。"""
    if os.path.exists(p):
        return p
    print("跳过：缺少 " + p)
    print("本仓库自带 samples/e2e_speech.wav；请确认已完整 clone。")
    raise SystemExit(0)

WAV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples", "e2e_speech.wav"))
w = wave.open(_need_sample(WAV), "rb")
raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
audio = to_mono16k(raw, w.getframerate(), w.getnchannels())
# 拼长一点，便于做长缓冲测试
audio = np.concatenate([audio, audio, audio])[:int(16000 * 24)]
print(f"素材 {len(audio)/16000:.1f} 秒\n")

m = WhisperModel("small", device="cpu", compute_type="int8", cpu_threads=4)


def run(buf):
    t0 = time.time()
    segs, info = m.transcribe(buf, language="en", beam_size=5, vad_filter=True,
                              condition_on_previous_text=False,
                              word_timestamps=True)
    segs = list(segs)
    return time.time() - t0, " ".join(s.text.strip() for s in segs).strip(), segs


print("=" * 72)
print("【一】缓冲长度 vs 每次更新耗时（MinChunkSize=0.5s 模拟）")
print("=" * 72)
print(f"{'缓冲秒':>7} {'耗时':>7} {'实时比':>7}   文本前 40 字")
buf_len = 0.5
while buf_len <= 12.0001:
    buf = audio[:int(16000 * buf_len)]
    el, txt, _ = run(buf)
    print(f"{buf_len:>7.1f} {el:>7.2f} {buf_len/el:>7.2f}   {txt[:40]}")
    buf_len += 0.5

print()
print("=" * 72)
print("【二】MinChunkSize 决定延迟：从说话开始到首字确认要多久")
print("=" * 72)
print("  模型：LocalAgreement-2 —— 需要连续两次更新结果一致才确认")
print("  所以最小延迟 ≈ 2 × MinChunkSize + 单次处理耗时")
for mcs in (0.3, 0.5, 1.0, 2.0):
    need = mcs * 2
    buf = audio[:int(16000 * need)]
    el, _, _ = run(buf)
    print(f"  MinChunkSize={mcs:>4.1f}s → 至少 {need:>4.1f}s 音频才够两次更新，"
          f"处理 {el:.2f}s，**首字延迟 ≈ {need + el:.2f}s**")

print()
print("=" * 72)
print("【三】缓冲裁剪的效果：确认到句末就裁，缓冲不会无限涨")
print("=" * 72)
print("  文本实测（同一段音频，缓冲 4/8/12 秒）：")
for d in (4, 8, 12):
    el, txt, segs = run(audio[:int(16000 * d)])
    nwords = sum(len(getattr(s, "words", []) or []) for s in segs)
    print(f"    缓冲 {d:>2}s → {el:.2f}s，{nwords} 词：{txt[:56]}")
print()
print("  结论：如果不裁，缓冲到 12 秒时每次更新 1.7 秒，而音频只推进 0.5 秒")
print("        → 处理追不上输入，会持续积压。**必须裁。**")
