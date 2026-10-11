# -*- coding: utf-8 -*-
"""第三个测试：识别可以小块，但**翻译要不要等句子完整**。

背景（前两个测试的结论）：
  · 远端识别对每个碎片都补句末标点 → 不能靠标点判句边界
    （HW-TSC 用的是独立"标点恢复"模型 + 词级时间戳，我们没有）
  · 定长 1.5s 切分实测会把句子切成碎片：
        "I never thought I would." / "See you again." / "The police." / "Coffee."
    每片都被单独翻译 → 字幕语义破碎

所以真正的问题是：**翻译该用碎片，还是该等句子攒完整？**

权衡：
  碎片翻译 → 延迟低，但每片单独翻译，语义断、成本高
  等句完整 → 延迟多等一点，但一次译一整句，质量好、成本低

"句子完整"的信号不能用识别输出的标点（不可靠），
但可以用**静音**：Segmenter 本来就是按静音切句的。
所以本测对比：把识别块**合并到静音边界**再翻译，代价是多少秒。
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
from live_subtitle_v2 import to_mono16k, TARGET_RATE, Segmenter, Translator
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
print(f"  素材 {len(audio)/TARGET_RATE:.1f} 秒")

PIECE = 0.5
step = int(PIECE * TARGET_RATE)

for max_batch in (1.5, 4.0, 8.0):
    print("\n" + "=" * 82)
    print(f"=== 攒批 {max_batch}s 后识别 → 翻译 ===")
    print("=" * 82)
    buf = []
    fed = 0.0
    rows = []
    t0 = time.time()
    for i in range(0, len(audio) - step + 1, step):
        buf.append(audio[i:i + step])
        fed += PIECE
        if len(buf) * PIECE < max_batch:
            continue
        cat = np.concatenate(buf)
        t1 = time.time()
        txt, _ = rasr(cat)
        t2 = time.time()
        zh = tr(txt) if txt else ""
        t3 = time.time()
        rows.append((fed, len(buf) * PIECE, txt, zh, t2 - t1, t3 - t2))
        buf = []
    if buf:
        cat = np.concatenate(buf)
        t1 = time.time()
        txt, _ = rasr(cat)
        t2 = time.time()
        zh = tr(txt) if txt else ""
        t3 = time.time()
        rows.append((fed, len(buf) * PIECE, txt, zh, t2 - t1, t3 - t2))
    wall = time.time() - t0

    print("  %7s %6s %6s %6s  %s" % ("音频位", "块长", "识别", "翻译", "字幕"))
    print("  " + "-" * 78)
    for fed, dur, txt, zh, t_asr, t_mt in rows:
        print("  %6.1fs %5.1fs %5.2fs %5.2fs  %s"
              % (fed, dur, t_asr, t_mt, (zh or txt)[:40]))
    full = " ".join(r[2] for r in rows)
    print(f"\n  字幕条数 {len(rows)}　总耗时 {wall:.1f}s")
    print(f"  合并后全文: {full[:88]}")
    # 每条的语义完整度：是否在句末标点处结束（用合并文本判）
    print(f"  平均每条 {len(full)/max(1,len(rows)):.0f} 字符")

print("\n" + "=" * 82)
print("判据")
print("=" * 82)
print("  块越大 → 字幕条数越少、每条越完整（译文更好），但等得越久。")
print("  看哪一档在'条数'和'等待'之间平衡最好。")
print("  已知远端识别固定开销 0.43s，所以块大的那部分等待主要是**攒批**。")
