# -*- coding: utf-8 -*-
"""_verify_pipeline.py —— 块内"边识别边翻译"流水线的纯逻辑单测（假模型，不加载权重）。

用 FakeModel 替身 faster-whisper：transcribe 返回预置 segments 的生成器。
验证：
  ① iter_chunk_cues 产出的 cue 序列与 build_cues（同输入）完全一致
  ② 边识别边产出（生成器是惰性的，不会一次性消费完所有 seg）
  ③ info_box 被填充语言检测结果
  ④ transcribe 调用带上了 VAD 放松 + 幻觉抑制参数
  ⑤ _min_duration_fixup 幂等

跑法：python bench/_verify_pipeline.py
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from pretranslate import (Cue, build_cues, iter_chunk_cues,
                          _min_duration_fixup)

ok = fail = 0


def chk(name, cond):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ok   {name}")
    else:
        fail += 1
        print(f"  FAIL {name}")


def fake_seg(start, end, text):
    return types.SimpleNamespace(start=start, end=end, text=text, words=None)


class FakeModel:
    def __init__(self, segs):
        self.segs = segs
        self.kwargs = None
        self.consumed = 0

    def transcribe(self, audio, **kw):
        self.kwargs = kw
        outer = self

        def gen():
            for s in outer.segs:
                outer.consumed += 1
                yield s

        info = types.SimpleNamespace(language="ko", language_probability=0.97)
        return gen(), info


# 每句 30+ 字符：两两合并会超 MAX_CUE_CHARS，所以每句都是独立 cue ——
# 这样才能验证"取第一条时只消费了部分 seg"（否则前几句并成一条，
# 第一条要等到最后一个 seg 才产出，断言就不成立了）。
SEGS = [fake_seg(0.0, 2.5, "첫 번째 문장입니다 아주 길게 말합니다."),
        fake_seg(3.0, 5.5, "두 번째 문장도 마찬가지로 깁니다."),
        fake_seg(6.0, 8.5, "세 번째 문장 역시 길게 이어집니다."),
        fake_seg(9.0, 11.5, "네 번째 문장으로 마무리합니다.")]

print("== 与 build_cues 等价性 ==")
audio = np.zeros(16_000 * 15, dtype=np.float32)
m1 = FakeModel(list(SEGS))
streamed = list(iter_chunk_cues(m1, audio, lang="ko"))
built = build_cues([fake_seg(s.start, s.end, s.text) for s in SEGS])
chk("条数一致", len(streamed) == len(built))
same = all(abs(a.start - b.start) < 1e-6 and a.text == b.text
           for a, b in zip(streamed, built))
chk("每条 start/text 一致", same)

print("== 惰性产出（边识别边出）==")
m2 = FakeModel(list(SEGS))
gen = iter_chunk_cues(m2, audio, lang="ko")
first = next(gen)
chk("取出第一条时只消费了部分 seg",
    0 < m2.consumed < len(SEGS))
chk("第一条 start 正确", abs(first.start - 0.0) < 1e-6)

print("== info_box / 识别参数 ==")
chk("kwargs 已记录", m2.kwargs is not None)
ib = {}
m3 = FakeModel(list(SEGS))
list(iter_chunk_cues(m3, audio, lang="ko", info_box=ib))
chk("info_box 填充语言", ib.get("language") == "ko")
chk("info_box 填充置信", abs(ib.get("language_probability") - 0.97) < 1e-6)
vp = m3.kwargs.get("vad_parameters") or {}
chk("VAD 放松参数", abs(vp.get("threshold", 0) - 0.35) < 1e-6
    and vp.get("speech_pad_ms") == 200)
chk("幻觉抑制开启",
    m3.kwargs.get("hallucination_silence_threshold") == 2.0)
chk("词级时间戳开启", m3.kwargs.get("word_timestamps") is True)

print("== _min_duration_fixup 幂等 ==")
cs = [Cue(0.0, 0.4, "短"), Cue(2.0, 4.0, "正常长度的句子")]
_min_duration_fixup(cs)
e1 = (cs[0].end, cs[1].end)
chk("短条被延长到 1.2s", abs(cs[0].end - 1.2) < 1e-6)
_min_duration_fixup(cs)
chk("二次调用结果不变", (cs[0].end, cs[1].end) == e1)
cs2 = [Cue(0.0, 0.2, "短"), Cue(0.5, 2.0, "下一条很近")]
_min_duration_fixup(cs2)
chk("延长不压下一条 start", cs2[0].end <= cs2[1].start + 1e-9)

print(f"\n合计 {ok} 通过 / {fail} 失败")
sys.exit(0 if fail == 0 else 1)
