# -*- coding: utf-8 -*-
"""bench/_bench_batched_asr.py —— 对比 faster-whisper 顺序识别 vs 批推理。

指标：
  · 首个 segment 产出时间（渐进性/首屏；越小越好）
  · 总耗时（吞吐；越小越好）
  · 文本相似度（批推理不能明显改内容）
  · segment 数/字数（粗看切分是否被改坏）

用法：python bench/_bench_batched_asr.py [--model large-v3] [--repeat 10]
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import wave

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)


def load_wav(path, repeat=1):
    with wave.open(path, "rb") as w:
        n, ch, rate = w.getnframes(), w.getnchannels(), w.getframerate()
        raw = w.readframes(n)
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    if rate != 16000:
        # 简单线性重采样（样本短，够用）
        idx = np.linspace(0, len(a) - 1, int(len(a) * 16000 / rate))
        a = np.interp(idx, np.arange(len(a)), a).astype(np.float32)
    if repeat > 1:
        a = np.concatenate([a] * repeat)
    return a


def words(text):
    import re
    return re.findall(r"[\w\u4e00-\u9fff]+", (text or "").lower())


def sim(a, b):
    wa, wb = words(a), words(b)
    if not wa or not wb:
        return 0.0
    sa, sb = set(wa), set(wb)
    return len(sa & sb) / max(1, len(sa | sb))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="large-v3")
    ap.add_argument("--repeat", type=int, default=10)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--audio", default="", help="真实音频文件（优先于样本重复）")
    ap.add_argument("--seconds", type=float, default=0,
                    help="只取音频前 N 秒（0=全部）")
    a = ap.parse_args()

    import pretranslate as P
    if a.audio:
        audio = P._decode_audio_16k(a.audio)
        if a.seconds and len(audio) > int(a.seconds * 16000):
            audio = audio[:int(a.seconds * 16000)]
        print(f"模型 {a.model}　音频 {len(audio)/16000:.1f}s（{a.audio}）")
    else:
        wav = os.path.join(HERE, "samples", "loopback_test.wav")
        if not os.path.isfile(wav):
            print("缺样本:", wav)
            return 1
        audio = load_wav(wav, a.repeat)
        print(f"模型 {a.model}　音频 {len(audio)/16000:.1f}s（重复 {a.repeat} 次）")

    model = P.load_asr(a.model)

    def run_baseline():
        t0 = time.time()
        first = None
        segs = []
        it, _info = model.transcribe(
            audio, beam_size=5, vad_filter=True, word_timestamps=True,
            condition_on_previous_text=True)
        for s in it:
            if first is None:
                first = time.time() - t0
            segs.append(s)
        return first, time.time() - t0, segs

    from faster_whisper import BatchedInferencePipeline
    pipe = BatchedInferencePipeline(model=model)

    def run_batch():
        t0 = time.time()
        first = None
        segs = []
        it, _info = pipe.transcribe(
            audio, batch_size=a.batch, beam_size=5, vad_filter=True,
            word_timestamps=True, condition_on_previous_text=True)
        for s in it:
            if first is None:
                first = time.time() - t0
            segs.append(s)
        return first, time.time() - t0, segs

    # 预热（避免首次 CUDA 初始化算进对比）
    print("预热…")
    try:
        list(model.transcribe(audio[:16000 * 3], beam_size=1,
                              vad_filter=True)[0])
    except Exception as e:
        print("预热失败:", e)

    f1, t1, s1 = run_baseline()
    f2, t2, s2 = run_batch()
    txt1 = " ".join(x.text for x in s1)
    txt2 = " ".join(x.text for x in s2)
    print("\n| 方案 | 首段产出 | 总耗时 | 段数 | 字符 | 文本相似度 |")
    print("|---|---|---|---|---|---|")
    print(f"| 顺序识别 | {f1:.2f}s | {t1:.2f}s | {len(s1)} | {len(txt1)} | — |")
    print(f"| 批推理 b={a.batch} | {f2:.2f}s | {t2:.2f}s | {len(s2)} | "
          f"{len(txt2)} | {sim(txt1, txt2):.3f} |")
    print(f"\n加速比：{t1 / max(t2, 1e-6):.2f}×　"
          f"首段延迟变化：{f2 - f1:+.2f}s")
    print("顺序首段:", (s1[0].text if s1 else "")[:80])
    print("批推首段:", (s2[0].text if s2 else "")[:80])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
