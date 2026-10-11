# -*- coding: utf-8 -*-
"""第二个测试：增量喂音频，看"累计文本何时出现句末标点"。

上一版的问题：
  ① 串行执行时 6 秒超时先生效，标点策略退化成"一根到底"（1 块），数据无效
  ② 定长策略虽然 11/11 都是"句末"，但那是因为**识别模型给每个碎片都补了
     句号** —— 碎片本身就是被切断的句子，这是定长切分的真实质量代价

这一版：按 0.5 秒增量累积（不丢弃），看**累计文本**何时出现句末标点。
它回答的问题是：把"送识别"和"翻译"拆开之后，
  · 送识别可以继续小块高频（延迟低）
  · 翻译等累计文本出现句末标点（质量好）
这个组合在时间上到底可行不可行。
"""
import io
import os
import re
import sys
import time
import wave

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import numpy as np
from live_subtitle_v2 import to_mono16k, TARGET_RATE
from remote_asr import from_env_or_key

END = re.compile(r"[.。！？!?…]['\"”’)]?\s*$")
rasr = from_env_or_key()

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

PIECE = 0.5
step = int(PIECE * TARGET_RATE)
acc = []            # 累积的音频（不丢，直到切分）
fed = 0.0
events = []
t0 = time.time()
for i in range(0, len(audio) - step + 1, step):
    acc.append(audio[i:i + step])
    fed += PIECE
    dur = len(acc) * PIECE
    if dur < 1.0:                 # 太短不送（碎片无意义）
        continue
    t = time.time()
    txt, _ = rasr(np.concatenate(acc))
    el = time.time() - t
    is_end = bool(txt) and bool(END.search(txt.strip()))
    events.append((fed, dur, txt, el, is_end))
    if is_end:
        acc = []                  # 切走，下一句重新累积

print()
print("  %7s %7s %7s %6s  %s" % ("音频位置", "块长", "耗时", "句末", "累计文本"))
print("  " + "-" * 88)
for fed, dur, txt, el, is_end in events:
    print("  %6.1fs %6.1fs %6.2fs %6s  %s"
          % (fed, dur, el, "✓" if is_end else "", txt[:52]))

cuts = [e for e in events if e[4]]
print()
print("  === 结论 ===")
print("  送识别 %d 次，其中检出句末 %d 次（= 可翻译的次数）" % (len(events), len(cuts)))
if cuts:
    waits = [e[1] for e in cuts]
    print("  检出句末时的块长: %s" % ["%.1fs" % x for x in waits])
    print("  块长中位 %.1fs" % sorted(waits)[len(waits) // 2])
print()
print("  每次送识别的音频长度都 >=1.0s、且随句子增长")
print("  → 说明'小块高频送识别 + 累计到句末再翻译'在时间上可行，")
print("    但**每次调用都要重跑整段累积音频**，调用开销随句长增长。")
print("  真实可行方案需要流式（只送新音频），不是每次重跑。")
