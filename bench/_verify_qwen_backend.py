# -*- coding: utf-8 -*-
"""bench/_verify_qwen_backend.py —— Qwen3-ASR 后端契约回归

把 2026-10-19 接入时真实踩到的坑固化成断言，换模型（0.6B→1.7B）或改
后端时先跑这个：

  ① **语言在返回前就绪**：`iter_chunk_cues` 在迭代前读 info.language，
     晚了就"只出原文不出译文"（端到端实测踩到：5 条字幕 0 条译文）。
  ② **段/词时间戳自洽**：单调、不重叠、词落在段内 —— 下游 cue 切分
     全靠这个，合成词戳错了会静默产出错位字幕。
  ③ **复读闸在位**：LLM 系 ASR 没有 nsp/logprob 信号，闸没了就等于
     把幻觉直接写进 srt。

用法：
  python bench/_verify_qwen_backend.py [--model qwen3-asr-0.6b]
"""
from __future__ import annotations

import argparse
import os
import sys
import wave

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)


def load_wav(path):
    import numpy as np
    with wave.open(path, "rb") as w:
        n, ch, rate = w.getnframes(), w.getnchannels(), w.getframerate()
        raw = w.readframes(n)
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    return a


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3-asr-0.6b")
    ap.add_argument("--wav", default=os.path.join(HERE, "samples",
                                                  "tts_vi.wav"))
    a = ap.parse_args()

    import pretranslate as P
    import qwen_asr_backend as Q

    audio = load_wav(a.wav)
    dur = len(audio) / 16000.0
    m = P.load_asr(a.model)

    ok = bad = 0

    def chk(name, cond, extra=""):
        nonlocal ok, bad
        if cond:
            ok += 1
        else:
            bad += 1
            print(f"  ✗ {name} {extra}")

    # ① 语言必须先就绪（**不迭代**就检查）
    segs_iter, info = m.transcribe(audio)          # 不给 language → 自动检测
    chk("transcribe() 返回时 info.language 已就绪（否则译器不会创建）",
        bool(info.language), repr(info.language))
    segs = list(segs_iter)
    chk("识别出段", len(segs) >= 1, str(len(segs)))

    # ② 时间轴自洽
    chk("段起点<终点", all(s.end > s.start for s in segs))
    chk("段单调不重叠",
        all(segs[i].start >= segs[i - 1].end - 0.01
            for i in range(1, len(segs))),
        str([(round(s.start, 2), round(s.end, 2)) for s in segs]))
    chk("段不越界（≤ 音频时长 + 0.2s）",
        all(s.end <= dur + 0.2 for s in segs), f"dur={dur:.2f}")
    word_bad = []
    for s in segs:
        if not s.words:
            word_bad.append((round(s.start, 2), "no words"))
            continue
        if s.words[0].start < s.start - 0.01 or s.words[-1].end > s.end + 0.01:
            word_bad.append((round(s.start, 2), "word out of seg"))
        if any(s.words[i].start < s.words[i - 1].start - 0.01
               for i in range(1, len(s.words))):
            word_bad.append((round(s.start, 2), "word not monotonic"))
    chk("词级时间戳落在段内且单调", not word_bad, str(word_bad[:3]))
    chk("词能拼回原文（去空格后一致）",
        all("".join(w.word for w in s.words).replace(" ", "")
            == s.text.replace(" ", "") for s in segs))

    # ③ 复读闸在位
    chk("复读闸可调用", callable(getattr(m, "_is_hal", None)))
    if callable(getattr(m, "_is_hal", None)):
        chk("整段复读被判幻觉", m._is_hal("xin chào xin chào xin chào xin chào xin chào"))
        chk("正常句不误杀", not m._is_hal("hôm nay chúng tôi sẽ đưa tin về tình hình"))

    print(f"qwen3 后端契约回归：{ok} 项通过，{bad} 项失败")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
