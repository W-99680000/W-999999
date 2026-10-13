# -*- coding: utf-8 -*-
"""诊断：分块识别为什么变慢？主嫌疑是 initial_prompt。

对同一段音频（金正恩演讲 45s~165s，内容密集）跑三次识别：
  ① 不带 prompt   ② 带 prompt   ③ 再不带 prompt
②③ 都是"模型已热"的状态，可以直接比较；③ 用来排除预热影响。
参考：整片识别是 ~11x 实时（120s 音频应约 11s）。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)

from pretranslate import load_asr, transcribe_chunk, _decode_audio_16k  # noqa

AUDIO = "pre_cache/url_cache/BV13kC6YUEHY.m4a"
PROMPT = ("열병부대 지휘관 병사들, 경축의 광장에 초대된 전쟁로병 동지들과 "
          "모범적인 군인 공로자 동지들,")

m = load_asr("large-v3")
audio = _decode_audio_16k(AUDIO)
seg = audio[45 * 16000:165 * 16000]
print(f"测试片段：{len(seg)/16000:.0f}s 音频\n")

runs = [("no-prompt-1", None), ("with-prompt", PROMPT), ("no-prompt-2", None)]
for tag, pr in runs:
    t = time.time()
    out, info = transcribe_chunk(m, seg, lang="ko", prompt=pr, tag=f"({tag})")
    el = time.time() - t
    txt = " ".join((s.text or "").strip() for s in out)[:60]
    print(f"\n[{tag}] 段数={len(out)} 用时={el:.1f}s "
          f"（{len(seg)/16000/el:.1f}x 实时）")
    print(f"   文本前 60 字: {txt}")
