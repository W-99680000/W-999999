# -*- coding: utf-8 -*-
"""测试"标点恢复切句"能否取代"定长批次"。

来源：HW-TSC（华为翻译团队）IWSLT 2024 字幕赛道方案的摘要写明其级联策略为
    "ASR with word-level timestamps, sentence segmentation based on
     punctuation restoration"
即：**用标点恢复来决定句子边界**，不是等静音。

我们的现状（`--max-batch 1.5`）：攒够 1.5 秒音频就发一次，靠**定长**切。
问题：
  · 攒批等待平均 0.75 秒（实测 0.49s 中位延迟里这部分占大头）
  · 1.5 秒的块经常把一句话切成两半 → 译文更差

假设：如果攒批条件改成"**累计文本出现句末标点就发**"，
  · 边界与语义对齐 → 译文质量更好
  · 句末标点通常在语义结束时出现，等待可能更短

这个脚本用真实音频离线验证：把音频切成 0.5 秒小块，
逐块送远端识别（模拟流式增量），比较两种切分策略的：
  · 块数（= 调用次数 = 延迟次数）
  · 每块文本是否以句末标点结尾（= 语义完整性）
"""
import io
import os
import re
import statistics
import sys
import time
import wave

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import numpy as np
from live_subtitle_v2 import to_mono16k, TARGET_RATE
from remote_asr import from_env_or_key

END_PUNCT = re.compile(r"[.。！？!?…]['\"”’)]?\s*$")


def ends_sentence(t):
    return bool(t) and bool(END_PUNCT.search(t.strip()))


rasr = from_env_or_key()

# 用英语素材（e2e_speech 是 3 句连续语音，句末标点齐全）
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
audio = to_mono16k(raw, w.getframerate(), w.getnchannels())
print(f"  素材 {len(audio)/TARGET_RATE:.1f} 秒　{rasr.status()}")

PIECE = 0.5          # 每次识别的音频粒度


def run(strategy, max_wait=6.0):
    """strategy: 'fixed' 定长 max_batch 秒 | 'punct' 见句末标点就发。"""
    max_batch = 1.5 if strategy == "fixed" else 99.0
    buf = []
    chunks = []
    fed = 0.0
    step = int(PIECE * TARGET_RATE)
    t0 = time.time()
    n_calls = 0
    for i in range(0, len(audio) - step + 1, step):
        buf.append(audio[i:i + step])
        fed += PIECE
        dur = len(buf) * PIECE
        txt = ""
        if dur >= max_batch:
            t = time.time()
            txt, _ = rasr(np.concatenate(buf))
            n_calls += 1
            el = time.time() - t
            if strategy == "fixed":
                chunks.append((fed, txt, el))
                buf = []
            else:
                # 标点策略：有句末标点才切；否则继续攒（但有上限防积压）
                if ends_sentence(txt) or dur >= max_wait:
                    chunks.append((fed, txt, el))
                    buf = []
                else:
                    pass          # 保留 buf，下一块继续加
    if buf:
        txt, _ = rasr(np.concatenate(buf))
        n_calls += 1
        chunks.append((fed, txt, rasr.t_last))
    return chunks, n_calls, time.time() - t0


for strat, label in (("fixed", "定长 1.5s（现状）"),
                     ("punct", "句末标点切分（HW-TSC 思路）")):
    chunks, n, wall = run(strat)
    good = sum(1 for _f, t, _e in chunks if ends_sentence(t))
    print(f"\n=== {label} ===")
    print(f"  切出 {len(chunks)} 块　识别调用 {n} 次　耗时 {wall:.1f}s")
    print(f"  以句末标点结尾的块: {good}/{len(chunks)}"
          f"（{good/max(1,len(chunks))*100:.0f}%）")
    lat = [e for _f, _t, e in chunks]
    if lat:
        print(f"  单次识别耗时 中位 {statistics.median(lat):.2f}s")
    print("  各块内容:")
    for fed, t, e in chunks:
        mark = "✓句末" if ends_sentence(t) else "  未完"
        print(f"    {fed:5.1f}s 调用{ e:4.2f}s {mark}  {t[:56]}")

print("\n" + "=" * 74)
print("判据")
print("=" * 74)
print("  如果'标点策略'的句末完成率明显更高、块数没有暴增，")
print("  那么它同时改善**译文质量**（不切断句子）和**延迟**（不必为凑长度等）。")
