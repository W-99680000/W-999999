# -*- coding: utf-8 -*-
"""_bench_gpu_beam.py —— GPU 上 beam_size 到底值多少钱

回答的问题：
    主程序转写写死 beam_size=5。"beam 5→1 没省"的旧结论来自 **CPU**
    （remote_asr.py 头部注释），GPU 上 ctranslate2 的 beam search 成本
    结构不同，从没单独测过。这里在 GPU medium 上对比 beam 1/2/5 的
    耗时与文本差异，决定默认值。

用法：python tools/_bench_gpu_beam.py
"""
import io
import os
import sys
import time
import wave

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ.setdefault("HF_HUB_OFFLINE", "1")

import numpy as np
from gpu_setup import enable_cuda_dlls
from live_subtitle_v2 import to_mono16k, TARGET_RATE


def load_wav(name):
    p = os.path.join(ROOT, "samples", name)
    w = wave.open(p, "rb")
    raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return to_mono16k(raw, w.getframerate(), w.getnchannels())


def diff_ratio(a, b):
    """粗粒度文本差异：不同的字符占比（编辑距离太贵，用逐位近似）。"""
    a, b = a.strip(), b.strip()
    if a == b:
        return 0.0
    import difflib
    return 1.0 - difflib.SequenceMatcher(None, a, b).ratio()


def main():
    enable_cuda_dlls()
    from faster_whisper import WhisperModel
    m = WhisperModel("medium", device="cuda", compute_type="float16",
                     cpu_threads=4)
    clips = []
    for name in ("e2e_speech.wav", "loopback_test.wav"):
        try:
            a = load_wav(name)
        except Exception as e:
            print(f"  {name}: 读不了（{e}），跳过")
            continue
        # 按主程序的真实批量切成 4 秒一段，最多取 6 段
        for i in range(0, min(len(a), 6 * 4 * TARGET_RATE), 4 * TARGET_RATE):
            seg = a[i:i + 4 * TARGET_RATE]
            if len(seg) >= TARGET_RATE:
                clips.append((name, seg))
    print(f"素材 {len(clips)} 段（每段 ≤4s）")

    # 预热
    for _, seg in clips[:1]:
        list(m.transcribe(seg, beam_size=5, vad_filter=True,
                          condition_on_previous_text=False,
                          temperature=0.0)[0])

    results = {}
    for beam in (5, 2, 1):
        times, texts = [], []
        for _, seg in clips:
            t0 = time.time()
            segs, _ = m.transcribe(seg, beam_size=beam, vad_filter=True,
                                   condition_on_previous_text=False,
                                   temperature=0.0)
            txt = " ".join(s.text.strip() for s in segs).strip()
            times.append(time.time() - t0)
            texts.append(txt)
        results[beam] = (times, texts)
        times_s = sorted(times)
        med = times_s[len(times_s) // 2]
        print(f"beam={beam}  中位 {med:.3f}s  平均 "
              f"{sum(times)/len(times):.3f}s  最大 {max(times):.3f}s")

    base_texts = results[5][1]
    for beam in (2, 1):
        diffs = [diff_ratio(b, t) for b, t in zip(base_texts, results[beam][1])
                 if b]
        avg_d = sum(diffs) / max(1, len(diffs))
        print(f"beam={beam} vs beam=5  文本差异率均值 {avg_d:.1%}")
        for i, (b, t) in enumerate(zip(base_texts, results[beam][1])):
            if diff_ratio(b, t) > 0.15:
                print(f"  段{i} 差异大:\n    b5: {b[:70]}\n    b{beam}: {t[:70]}")


if __name__ == "__main__":
    main()
