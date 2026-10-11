# -*- coding: utf-8 -*-
"""small 是不是过度配置？用真实素材量速度与准确度。

动机（_bench_bottleneck.py 的结论）：
    一次 transcribe 的固定开销约 2 秒，**与音频长短无关** ——
    这是模型前向的成本，不是"处理 4 秒音频"的成本。
    所以延迟下限 = 2 秒 + 等句子说完。
    参数/线程/compute_type 都动不了它（实测 ±3%）。
    唯一有效的是换模型：
        small 2.363s / base 0.741s / tiny 0.394s（4 秒音频）
    但降模型会掉准确度。**掉多少？** 这个脚本量出来。

做法：同一段真实音频，三个模型各转一次，
  · 量耗时
  · 和 small 的输出做**词级对比**，算一致率
  · 打印各自文本，人眼也能判断
素材：开发目录里的真实音频（越南语/俄语新闻样本、e2e 英语样本）。
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
from faster_whisper import WhisperModel

# 找一个真实素材：优先俄语/越南语（操作者实际在看的），退英语
CANDS = [
    ("越南语新闻", os.path.join(HERE, "samples", "vi_sample.wav")),
    ("越南语长样本", os.path.join(HERE, "samples", "vi_long.wav")),
# ── 样本检查 ─────────────────────────────────────────────
# tests/e2e_speech.wav 随仓库发布（samples/ 下）。缺失就友好退出。
def _need_sample(p):
    """确认样本存在，否则给出提示后退出。"""
    if os.path.exists(p):
        return p
    print("跳过：缺少 " + p)
    print("本仓库自带 samples/e2e_speech.wav；请确认已完整 clone。")
    raise SystemExit(0)

    ("英语 e2e", os.path.join(HERE, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples", "e2e_speech.wav"))),
    ("英语 e2e", os.path.join(HERE, "samples", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples", "e2e_speech.wav"))),
    ("环回测试", os.path.join(HERE, "samples", "loopback_test.wav")),
]

MODELS = ["small", "base", "tiny"]
SEG = 4.0        # 按 4 秒切，和主程序批次一致


def words(s):
    import re
    return [w for w in re.findall(r"\S+", (s or "").lower())
            if w.strip(".,!?;:")]


def agree(a, b):
    """两个转写结果的词级一致率（按位置对齐，粗略但够比较）。"""
    wa, wb = words(a), words(b)
    if not wa and not wb:
        return 1.0
    n = min(len(wa), len(wb))
    if n == 0:
        return 0.0
    same = sum(1 for i in range(n) if wa[i] == wb[i])
    return same / max(len(wa), len(wb))


models = {}
print("加载模型...")
for m in MODELS:
    t0 = time.time()
    models[m] = WhisperModel(m, device="cpu", compute_type="int8",
                             cpu_threads=12)
    print(f"  {m:<6} 加载 {time.time()-t0:.1f}s")

for label, path in CANDS:
    if not os.path.exists(path):
        continue
    try:
        w = wave.open(path, "rb")
        raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        audio = to_mono16k(raw, w.getframerate(), w.getnchannels())
    except Exception as e:
        print(f"\n跳过 {label}（{type(e).__name__}）")
        continue
    if len(audio) < TARGET_RATE * 3:
        continue

    dur = len(audio) / TARGET_RATE
    print("\n" + "=" * 74)
    print(f"素材：{label}　{dur:.1f} 秒　按 {SEG:.0f} 秒切")
    print("=" * 74)

    # 语言：让它自己判
    a = audio[: int(SEG * TARGET_RATE)]
    out = {}
    print(f"\n  {'模型':<7} {'耗时':>7} {'语言':>5} {'语概':>5}  文本")
    print("  " + "-" * 68)
    for m in MODELS:
        t0 = time.time()
        segs, info = models[m].transcribe(
            a, language=None, beam_size=5, vad_filter=True,
            condition_on_previous_text=False, temperature=0.0)
        txt = " ".join(s.text.strip() for s in segs).strip()
        dt = time.time() - t0
        out[m] = txt
        print(f"  {m:<7} {dt:>6.2f}s {info.language:>5} "
              f"{info.language_probability:>5.2f}  {txt[:44]}")

    print(f"\n  与 small 的一致率（1.0 = 完全相同）:")
    for m in MODELS[1:]:
        print(f"    {m:<6} {agree(out['small'], out[m]):.2f}")

    # 全段耗时对比
    print(f"\n  整段 {dur:.0f} 秒逐 {SEG:.0f} 秒转写（模拟真实批次）：")
    for m in MODELS:
        t0 = time.time()
        n = 0
        for i in range(0, len(audio) - int(SEG * TARGET_RATE) + 1,
                       int(SEG * TARGET_RATE)):
            segs, _ = models[m].transcribe(
                audio[i:i + int(SEG * TARGET_RATE)], language=None,
                beam_size=5, vad_filter=True,
                condition_on_previous_text=False, temperature=0.0)
            list(segs)
            n += 1
        dt = time.time() - t0
        per = dt / max(1, n)
        print(f"    {m:<6} {n} 批 / {dt:.2f}s　每批 {per:.2f}s　"
              f"{'跟得上实时' if dt <= dur else '**跟不上**'}")
    break        # 只测第一个可用素材，够做判断
