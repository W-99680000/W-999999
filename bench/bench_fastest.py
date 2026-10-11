# -*- coding: utf-8 -*-
"""找能在 CPU 上超过实时的 whisper 配置。
   判据只有一个：音视频时长 T，处理 T 秒音频只要 < T 秒 → 追得上实时。
   测：模型大小 × compute_type × threads，用固定 4 秒音频测单次耗时。
"""
import io
import os
import sys
import time
import wave

import numpy as np

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from live_subtitle_v2 import to_mono16k
from faster_whisper import WhisperModel

# ── 样本检查 ─────────────────────────────────────────────
# tests/e2e_speech.wav 随仓库发布（samples/ 下）。缺失就友好退出。
def _need_sample(p):
    """确认样本存在，否则给出提示后退出。"""
    if os.path.exists(p):
        return p
    print("跳过：缺少 " + p)
    print("本仓库自带 samples/e2e_speech.wav；请确认已完整 clone。")
    raise SystemExit(0)

w = wave.open(os.path.join(HERE, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples", "e2e_speech.wav")), "rb")
raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
audio = to_mono16k(raw, w.getframerate(), w.getnchannels())
seg = audio[:int(16000 * 4.0)]
print(f"测试音频 4.0 秒\n")

print("=" * 78)
print(f"{'模型':<10} {'精度':<14} {'线程':>4} {'耗时':>7} {'实时比':>7}  文本")
print("=" * 78)
best = None
for model in ("tiny", "base", "small"):
    for ct in ("int8", "float32"):
        for th in (2, 4, 8):
            try:
                m = WhisperModel(model, device="cpu", compute_type=ct,
                                 cpu_threads=th)
                ts = []
                txt = ""
                for _ in range(2):
                    t0 = time.time()
                    segs, _ = m.transcribe(seg, language="en", beam_size=1,
                                           vad_filter=False,
                                           condition_on_previous_text=False)
                    segs = list(segs)
                    ts.append(time.time() - t0)
                    txt = " ".join(s.text.strip() for s in segs)
                el = min(ts)
                rtf = 4.0 / el
                flag = "  ← 追得上" if rtf >= 1.0 else ""
                print(f"{model:<10} {ct:<14} {th:>4} {el:>7.2f} {rtf:>7.2f}  {txt[:36]}{flag}")
                if best is None or rtf > best[0]:
                    best = (rtf, model, ct, th, el)
            except Exception as e:
                print(f"{model:<10} {ct:<14} {th:>4}  失败 {type(e).__name__} {str(e)[:40]}")

print()
if best:
    print(f"** 最快：{best[1]} / {best[2]} / {best[3]} 线程 → "
          f"{best[4]:.2f}s，实时比 {best[0]:.2f}× **")
    print(f"   若 >= 1.0，说明该配置能追上实时；否则 CPU 上只能用更短的段")
