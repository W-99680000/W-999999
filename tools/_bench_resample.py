#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/_bench_resample.py —— 48k→16k 重采样的混叠代价与 CPU 成本

回答的问题（README 里"实测数字"风格）：
  1. 现在 to_mono16k() 用的线性插值，把 8kHz 以上的能量折叠到 0-8kHz
     有多强？—— 用 12kHz / 10kHz 单音量化。
  2. 换成多相滤波（scipy.signal.resample_poly）能压到多少 dB？
  3. 代价多大？（每 10ms 采集块的 CPU 时间）

用法：
    python tools/_bench_resample.py
    python tools/_bench_resample.py --taps 127   # 无 scipy 时的 FIR 长度

结论（本机实测，2026-10）：
    线性插值：12kHz → 4kHz 折叠，相对 440Hz 基准仅 -4.4 dB（基本没滤）
    多相滤波：同一混叠 -72 dB（scipy resample_poly）
    代价：每 10ms 块 9µs → 83µs（约单核 0.8%），相对 ASR 可忽略
"""
from __future__ import annotations

import argparse
import time

import numpy as np

SR_IN, SR_OUT = 48000, 16000


def linear_16k(x: np.ndarray, sr: int = SR_IN) -> np.ndarray:
    """与 live_subtitle_v2.to_mono16k 相同的线性插值（对照基线）。"""
    n = int(len(x) / sr * SR_OUT)
    pos = np.arange(n, dtype=np.float64) * sr / SR_OUT
    return np.interp(pos, np.arange(len(x), dtype=np.float64), x).astype(np.float32)


def fir_16k(x: np.ndarray, taps: int = 127) -> np.ndarray:
    """无 scipy 时的多相近似：Kaiser 窗 sinc 低通 + 3 倍抽取。

    注意：真实流式实现要**带滤波器状态**（块间重叠），这里只测质量/成本。
    """
    m = np.arange(taps) - (taps - 1) / 2.0
    fc = 0.45 * SR_OUT / SR_IN          # 归一化截止（相对输入奈奎斯特）
    h = np.sinc(2 * fc * m) * np.kaiser(taps, 8.0)
    h /= h.sum()
    return np.convolve(x, h, mode="same")[:: SR_IN // SR_OUT].astype(np.float32)


def poly_16k(x: np.ndarray) -> np.ndarray:
    from scipy.signal import resample_poly
    return resample_poly(x, SR_OUT // 8000, SR_IN // 8000).astype(np.float32)


def peak_at(y: np.ndarray, f: float, sr: int = SR_OUT) -> float:
    w = np.hanning(len(y))
    Y = np.abs(np.fft.rfft(y * w))
    fr = np.fft.rfftfreq(len(y), 1.0 / sr)
    return float(Y[np.argmin(np.abs(fr - f))])


def quality(taps: int) -> None:
    t = np.arange(SR_IN) / SR_IN                 # 1 秒
    x = (0.5 * np.sin(2 * np.pi * 440 * t)
         + 0.3 * np.sin(2 * np.pi * 12000 * t)
         + 0.3 * np.sin(2 * np.pi * 10000 * t))
    variants = [("linear(现状)", linear_16k(x))]
    variants.append((f"FIR {taps} taps", fir_16k(x, taps)))
    try:
        variants.append(("scipy resample_poly", poly_16k(x)))
    except Exception as e:
        print("  (scipy 不可用，跳过 resample_poly:", type(e).__name__, ")")
    ref = peak_at(variants[0][1], 440)
    print("\n[混叠强度] 12k/10k 音折叠到 4k/6k 的峰值（相对 440Hz 基准）")
    print("  %-20s %10s %10s" % ("方案", "4k 折叠", "6k 折叠"))
    for name, y in variants:
        r4 = peak_at(y, 4000)
        r6 = peak_at(y, 6000)
        print("  %-20s %8.1f dB %8.1f dB" % (
            name, 20 * np.log10(max(r4, 1e-12) / ref),
            20 * np.log10(max(r6, 1e-12) / ref)))


def cost(taps: int) -> None:
    rng = np.random.default_rng(0)
    blk = (rng.standard_normal(int(SR_IN * 0.01)) * 3000).astype(np.int16)
    x = blk.astype(np.float32) / 32768.0
    variants = [("linear(现状)", lambda a: linear_16k(a)),
                (f"FIR {taps} taps", lambda a: fir_16k(a, taps))]
    try:
        poly_16k(x)
        variants.append(("scipy resample_poly", lambda a: poly_16k(a)))
    except Exception:
        pass
    N = 500
    print("\n[CPU 成本] 每 10ms 采集块（100 次/秒）")
    base = None
    for name, fn in variants:
        t0 = time.perf_counter()
        for _ in range(N):
            fn(x)
        dt = (time.perf_counter() - t0) / N
        base = base or dt
        print("  %-20s %7.1f µs/块  (单核 %.2f%%)" % (
            name, dt * 1e6, dt * 100 * 100))
    print("\n  参考：ASR 一次 0.3~0.9s/批（GPU），重采样成本可忽略。")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--taps", type=int, default=127)
    a = ap.parse_args()
    try:
        import sys
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    quality(a.taps)
    cost(a.taps)
