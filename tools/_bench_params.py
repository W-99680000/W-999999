# -*- coding: utf-8 -*-
"""把"段长"和"翻译合并上限"两个参数一起扫，找出质量/延迟的平衡点。

背景：我连续调了几次参数，每次只量一个维度，导致来回改。
这次一次性把两个维度都摆出来：

  段长 max_seg      → 决定"多久切一段、送去识别"（延迟的主要来源）
  合并上限 mt_cap   → 决定"翻译攒多久"，太短 → 译文碎，太长 → 等得久

注意两者交互：段长小 → 段多 → 累计文本长 → 提前触发 mt_cap。
所以要一起扫，不能单独调。

评价指标（都要看）：
  · 字幕条数 / 平均长度  → 质量（越少越长越完整）
  · 每条的等待            → 延迟
  · 是否能读出完整句子    → 人眼判断
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
from live_subtitle_v2 import to_mono16k, TARGET_RATE, Segmenter, Translator
from remote_asr import from_env_or_key

rasr = from_env_or_key()
tr = Translator("auto", "zh")

# 用真实的长素材（16.4 秒英语）拼到 45 秒，样本更充分
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
base = to_mono16k(raw, w.getframerate(), w.getnchannels())
audio = np.tile(base, 3)[: int(45 * TARGET_RATE)]
print(f"  素材 {len(audio)/TARGET_RATE:.0f} 秒　{rasr.status()}")
print()
print("  %-8s %-8s %6s %8s %8s  %s"
      % ("段长", "合并上限", "条数", "平均字", "中位等待", "样例"))
print("  " + "-" * 96)

step = int(0.05 * TARGET_RATE)
for max_seg in (1.0, 2.0, 4.0):
    for mt_cap in (2.0, 4.0, 8.0):
        seg = Segmenter(min_len=0.5, max_len=max_seg, silence=0.45)
        pending = []          # [(段, 原文)]
        rows = []
        fed = 0.0
        t0 = time.time()
        for i in range(0, len(audio) - step + 1, step):
            chunk = audio[i:i + step]
            fed += len(chunk) / TARGET_RATE
            for s in seg.push(chunk):
                if len(s) / TARGET_RATE < 0.3:
                    continue
                txt, _ = rasr(s)
                if not txt:
                    continue
                is_nat = len(s) / TARGET_RATE < max_seg - 0.05
                pending.append((s, txt))
                pt = sum(len(x) / TARGET_RATE for x, _ in pending)
                if is_nat or pt >= mt_cap:
                    merged = " ".join(t for _s, t in pending)
                    zh = tr(merged)
                    rows.append((fed, pt, merged, zh))
                    pending = []
        if pending:
            merged = " ".join(t for _s, t in pending)
            rows.append((fed, sum(len(x) / TARGET_RATE for x, _ in pending),
                         merged, tr(merged)))
        if not rows:
            continue
        # "等待" = 该次翻译触发时，音频已经走到哪（近似延迟上界）
        # 用每条的音频长度作为"攒了多久"的代理
        waits = sorted(r[1] for r in rows)
        med_wait = waits[len(waits) // 2]
        total_chars = sum(len(r[3]) for r in rows)
        sample = rows[len(rows) // 2][3][:34] if rows else ""
        print("  %-8.1f %-8.1f %6d %8.0f %7.1fs  %s"
              % (max_seg, mt_cap, len(rows), total_chars / len(rows),
                 med_wait, sample))

print()
print("  怎么读这张表：")
print("    条数少 + 平均字多 = 译文完整（好）")
print("    中位等待小 = 延迟低（好）")
print("    两者不可兼得，要选平衡点。")
