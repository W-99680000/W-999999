# -*- coding: utf-8 -*-
"""回归测试：不发声，用已存的 TTS 语音文件过一遍**主循环的同一段代码路径**。
   为什么需要它：主循环里曾有一个 bug（把音频数组当文本传给翻译器），
   而 --selftest 只测了 tr("hello")，没测主循环，所以没抓住。
   **要点：测部件不等于测管道。**
"""
import os
import sys
import queue
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live_subtitle_v2 import Segmenter, BatchTranscriber, Translator, to_mono16k

# ── 样本检查 ─────────────────────────────────────────────
# tests/e2e_speech.wav 随仓库发布（samples/ 下）。缺失就友好退出。
def _need_sample(p):
    """确认样本存在，否则给出提示后退出。"""
    if os.path.exists(p):
        return p
    print("跳过：缺少 " + p)
    print("本仓库自带 samples/e2e_speech.wav；请确认已完整 clone。")
    raise SystemExit(0)

WAV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples", "e2e_speech.wav"))
ok = fail = 0


def chk(n, c):
    global ok, fail
    if c:
        ok += 1; print(f"  ok   {n}")
    else:
        fail += 1; print(f"  FAIL {n}")


print("一、素材")
chk("有测试语音", os.path.exists(WAV))
w = wave.open(_need_sample(WAV), "rb")
sr, ch = w.getframerate(), w.getnchannels()
raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
print(f"     {len(raw)/sr/ch:.1f} 秒 {sr}Hz {ch}ch")

print("\n二、走主循环的同一路径")
tr = Translator("en", "zh")
th = BatchTranscriber("small", 4)
seg = Segmenter(min_len=0.8, max_len=8.0, silence=0.55)
print(f"     翻译器 {tr.mode}")

# 模拟主循环：分块喂，切段，转写，翻译
audio = to_mono16k(raw, sr, ch)
BLK = int(16000 * 0.04)           # 40ms 一块，和真实回调一致
pairs = []
errs = []
i = 0
while i < len(audio):
    for s in seg.push(audio[i:i + BLK]):
        try:
            # 主程序用的是批量接口 transcribe_batch(list)（见 live_subtitle_v2 主循环）。
            # 单段就传单元素列表，保持与主循环同一路径。
            _texts = th.transcribe_batch([s], lang="en")
            txt = _texts[0] if _texts else ""
            lang, prob = th.last_lang, th.last_lang_prob
            if not txt:
                continue
            # —— 就是这一行曾经传错（传 s 而不是 txt）——
            zh = tr(txt)
            pairs.append((txt, zh))
        except Exception as e:
            errs.append(f"{type(e).__name__}: {e}")
    i += BLK

chk("主循环路径无异常", not errs)
if errs:
    for e in errs[:3]:
        print("       ", e)
chk("至少切出 3 段", len(pairs) >= 3)
chk("每段都有译文", all(z and not z.startswith("[") for _, z in pairs))

print("\n三、结果")
for en, zh in pairs:
    print(f"     {en}")
    print(f"     → {zh}")

print(f"\n合计 {ok} 通过 / {fail} 失败")
sys.exit(0 if fail == 0 else 1)
