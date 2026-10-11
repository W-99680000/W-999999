# -*- coding: utf-8 -*-
"""分离"识别节拍"与"翻译节拍"：量延迟代价。

问题（_bench_merge_translate.py 实测）：
  识别块 1.5s → 11 条碎片："我从没想过我会。" / "再见。" / "警察。"
  识别块 4.0s →  4 条完整："我从没想过还能再见到你。走吧。"
  识别块 8.0s →  2 条更完整
但块大 = 延迟高。二者绑在一起了。

HW-TSC 的思路是把两者**分开**：识别出声，句边界由另一套机制决定。

我们能用什么判定"语义单元完整了"？候选：
  ① 静音（Segmenter 本来就在做：说话停顿 ≥0.45s 就切句）
  ② 长度上限（防止一句话太长攒不住）

本测：识别继续小块（1.5s，延迟不变），但**翻译等静音边界**。
量这样做的延迟代价是多少秒。
"""
import io
import os
import statistics
import sys
import time
import wave

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import numpy as np
from live_subtitle_v2 import to_mono16k, TARGET_RATE, Translator
from remote_asr import from_env_or_key

rasr = from_env_or_key()
tr = Translator("auto", "zh")

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
print(f"  素材 {len(audio)/TARGET_RATE:.1f} 秒　"
      f"（连续朗读，句间停顿很短 —— 真实视频里停顿更长）")

PIECE = 0.5
step = int(PIECE * TARGET_RATE)


def rms_db(x):
    r = float(np.sqrt(np.mean(np.asarray(x, dtype=np.float64) ** 2)))
    return 20 * np.log10(max(r, 1e-10))


# 先摸清素材的停顿结构
fr = [rms_db(audio[i:i + step]) for i in range(0, len(audio) - step, step)]
med = statistics.median(fr)
print(f"  帧电平中位 {med:.1f} dBFS　最低 {min(fr):.1f}　最高 {max(fr):.1f}")
sil = [i * PIECE for i, v in enumerate(fr) if v < med - 12]
print(f"  疑似静音帧（低于中位 12dB）：{len(sil)} 个 → "
      f"{[round(x,1) for x in sil[:12]]}")

print("\n" + "=" * 82)
print("对比两种节拍")
print("=" * 82)

for mode in ("绑在一起 1.5s", "识别1.5s + 翻译等静音"):
    buf = []
    fed = 0.0
    rows = []
    t0 = time.time()
    pending_txt = []
    pend_start = None
    for i in range(0, len(audio) - step + 1, step):
        chunk = audio[i:i + step]
        buf.append(chunk)
        fed += PIECE
        # 识别节拍：恒定 1.5s（两种模式一样）
        if len(buf) * PIECE >= 1.5:
            cat = np.concatenate(buf)
            t1 = time.time()
            txt, _ = rasr(cat)
            t2 = time.time()
            buf = []
            if not txt:
                continue
            if mode.startswith("绑在一起"):
                zh = tr(txt)
                rows.append((fed, txt, zh, t2 - t1))
            else:
                pending_txt.append(txt)
                if pend_start is None:
                    pend_start = fed
                # 翻译节拍：只在这里判定"该不该翻"。
                # 判定用**当前这块音频的电平**（模拟静音检测）。
                quiet = rms_db(cat) < med - 12
                long_enough = (fed - pend_start) >= 4.0
                if quiet or long_enough:
                    merged = " ".join(pending_txt)
                    zh = tr(merged)
                    rows.append((fed - pend_start, merged, zh, t2 - t1))
                    pending_txt = []
                    pend_start = None
    if pending_txt:
        merged = " ".join(pending_txt)
        zh = tr(merged)
        rows.append((fed - (pend_start or fed), merged, zh, 0.0))
    wall = time.time() - t0
    print(f"\n--- {mode} ---")
    print(f"  字幕条数 {len(rows)}　总耗时 {wall:.1f}s")
    for dur, txt, zh, el in rows:
        print(f"    块长{dur:4.1f}s  {zh[:52]}")
    full = " ".join(r[1] for r in rows)
    print(f"  平均每条 {len(full)/max(1,len(rows)):.0f} 字符")

print("\n" + "=" * 82)
print("说明")
print("=" * 82)
print("  两种模式的**识别节拍完全相同**（都 1.5s 一次），")
print("  差别只在'翻译什么'：碎片 vs 攒到静音/上限的合并文本。")
print("  所以对比出的差异纯粹是**翻译节拍**带来的，不掺识别延迟。")
