# -*- coding: utf-8 -*-
"""翻译这 0.62 秒能省多少：分清「固定开销」与「真实工作量」。

为什么先量这个而不直接上本地模型：
  · llama.cpp 官方预编译包没有 Windows CUDA 二进制（查过）
  · llama-cpp-python 从源码编译失败（要完整 CUDA Toolkit + CMake）
  · 本机 torch 是 CPU 版（2.14.1+cpu），跑不了 GPU
  所以在"上本地翻译"之前，先看**远端这条路还有多少可压**。
  如果固定开销占大头，一次译多段就能省下来，不必换方案。

量三件事：
  ① 单段翻译耗时 vs 文本长度（斜率 = 真实工作量，截距 = 固定开销）
  ② 一次译多段（batch）能否摊薄固定开销
  ③ qwen-mt-lite / flash / plus 三档的延迟差
"""
import io
import os
import statistics
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from llm_translate import (LLMTranslator, DASHSCOPE_BASE,
                           _read_dashscope_key as read_key)

KEY = read_key()

# 真实字幕句（长短混合）
SENTS = [
    "Okay.",                                              # 极短
    "I never thought I would see you again.",              # 短
    "Get out of here before I call the police.",           # 中
    "The truth is I have been lying to you since the day we met.",
    "Once the operator locks the target correctly, the guidance system "
    "becomes immune to jamming, and the missile continues to the target.",  # 长
]

print("=" * 84)
print("① 单段翻译耗时 vs 文本长度（每档 3 次取中位）")
print("=" * 84)
for model in ("qwen-mt-lite", "qwen-mt-flash", "qwen-mt-plus"):
    t = LLMTranslator(DASHSCOPE_BASE, model, KEY, "openai", no_system=True)
    print(f"\n  【{model}】")
    rows = []
    for s in SENTS:
        ts = []
        out = ""
        for _ in range(3):
            t0 = time.time()
            out = t(s, target="Simplified Chinese")
            ts.append(time.time() - t0)
        ts.sort()
        rows.append((len(s), ts[1]))
        print(f"    {len(s):>3} 字符　{ts[1]:.3f}s　{out[:44]}")
    # 线性拟合：截距 = 固定开销
    n = len(rows)
    sx = sum(r[0] for r in rows); sy = sum(r[1] for r in rows)
    sxx = sum(r[0] * r[0] for r in rows); sxy = sum(r[0] * r[1] for r in rows)
    den = n * sxx - sx * sx
    if den:
        k = (n * sxy - sx * sy) / den
        b = (sy - k * sx) / n
        print(f"    → 拟合：耗时 ≈ {k*1000:.3f}ms/字符 × 长度 + {b:.3f}s")
        print(f"      **固定开销约 {b:.3f}s**（与文本长短无关）")

print()
print("=" * 84)
print("② 一次译多段（batch）能否摊薄固定开销")
print("=" * 84)
t = LLMTranslator(DASHSCOPE_BASE, "qwen-mt-flash", KEY, "openai", no_system=True)
for k in (1, 2, 4):
    grp = SENTS[:k]
    joined = "\n".join(grp)
    ts = []
    for _ in range(3):
        t0 = time.time()
        out = t(joined + "\n\n（逐行翻译上面每一行，只输出译文，每行一句）",
                target="Simplified Chinese")
        ts.append(time.time() - t0)
    ts.sort()
    # 对照：逐条各译一次的总耗时
    t0 = time.time()
    for s in grp:
        t(s, target="Simplified Chinese")
    seq = time.time() - t0
    print(f"\n  {k} 段一起：{ts[1]:.3f}s　逐条分开：{seq:.3f}s"
          f"　{'省 %.2fs' % (seq - ts[1]) if ts[1] < seq else '更慢'}")
    print(f"    合并后输出: {out[:120]!r}")

print()
print("=" * 84)
print("③ 结论")
print("=" * 84)
print("  看①的截距：如果固定开销占单段耗时的比例高，说明大部分时间花在")
print("  '建立请求 + 模型预热'上，而不是真正的翻译计算。")
print("  看②：如果合并多段明显更快，且输出仍可切分，那就能直接省时间。")
