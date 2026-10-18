# -*- coding: utf-8 -*-
"""bench/eval_asr.py —— ASR 准确性/可读性基线（WER + CPS）。

样本 samples/loopback_test.wav 是用 Windows TTS 合成的已知文本
（重复 6 遍）：

    "Hello, this is a test of the loopback capture system.
     One two three four five."

指标：
  · WER（词级编辑距离 / 参照词数；小写、去标点）
  · 顺序识别 vs 批推理的 WER 对比（批推理默认开启后的回归）
  · 每 13.8s 片段对应一遍文本（参照按实际时长平铺）
  · cue CPS（字符/秒，取 P50/P90 与 >20 的比例）
  · 耗时（仅供参考，延迟另有 metrics）

用法：
  python bench/eval_asr.py                     # medium + large-v3
  python bench/eval_asr.py --models medium --json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import wave

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

WAV = os.path.join(HERE, "samples", "loopback_test.wav")
UNIT = ("hello this is a test of the loopback capture system "
        "one two three four five")
UNIT_SEC = 6.9   # TTS 一遍 15 词约 6.9s（样本里重复了 2 遍）


_DIGITS = {"zero": "0", "one": "1", "two": "2", "three": "3",
           "four": "4", "five": "5", "six": "6", "seven": "7",
           "eight": "8", "nine": "9", "ten": "10"}


def words(s):
    # 数字词与阿拉伯数字归一：whisper 有时把 "one two" 写成 "1 2"，
    # 不归一会白算 10 个词错误（实测 WER 0.333 → 0）。
    ws = re.findall(r"[a-z0-9']+", (s or "").lower())
    return [_DIGITS.get(w, w) for w in ws]


def edit_dist(a, b):
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def load_wav(path=WAV):
    with wave.open(path, "rb") as w:
        n, ch, rate = w.getnframes(), w.getnchannels(), w.getframerate()
        raw = w.readframes(n)
    import numpy as np
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    if rate != 16000:
        idx = np.linspace(0, len(a) - 1, int(len(a) * 16000 / rate))
        a = np.interp(idx, np.arange(len(a)), a).astype(np.float32)
    return a


def pct(xs, q):
    if not xs:
        return None
    ys = sorted(xs)
    return ys[max(0, min(len(ys) - 1, int(round((len(ys) - 1) * q))))]


def cps_of_cues(cues):
    out = []
    for c in cues:
        dur = max(0.2, c.end - c.start)
        n = len(re.sub(r"\s+", "", c.text or ""))
        if n:
            out.append(n / dur)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="medium,large-v3")
    ap.add_argument("--audio", default=WAV,
                    help="任意 16k/单声道 wav（非默认样本时必须配 --ref）")
    ap.add_argument("--ref", default="",
                    help="参照原文（词序列，标点可留；数字词会归一）")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if not os.path.isfile(a.audio):
        print("缺样本:", a.audio)
        return 1
    audio = load_wav(a.audio)
    dur = len(audio) / 16000
    if a.audio != WAV:
        if not a.ref:
            print("非默认样本必须给 --ref 参照文本")
            return 2
        ref = words(a.ref)
        print(f"样本 {dur:.1f}s，参照 {len(ref)} 词（--ref）")
    else:
        reps = max(1, int(round(dur / UNIT_SEC)))
        ref = words(" ".join([UNIT] * reps))
        print(f"样本 {dur:.1f}s，参照 {len(ref)} 词（{reps} 遍 TTS 文本）")

    import pretranslate as P
    from faster_whisper import BatchedInferencePipeline

    report = {"sample": WAV, "duration_s": round(dur, 2),
              "ref_words": len(ref), "models": {}}
    for name in a.models.split(","):
        name = name.strip()
        if not name:
            continue
        print(f"\n模型 {name}")
        try:
            model = P.load_asr(name)
        except Exception as e:
            print("  加载失败:", e)
            continue
        # 顺序
        t0 = time.time()
        segs = list(model.transcribe(
            audio, beam_size=5, vad_filter=True, word_timestamps=True,
            condition_on_previous_text=True)[0])
        t_seq = time.time() - t0
        hyp = words(" ".join(s.text for s in segs))
        wer_seq = edit_dist(hyp, ref) / max(1, len(ref))
        cues_seq = P.build_cues(segs)
        cps_seq = cps_of_cues(cues_seq)
        cues_seq_shaped = P.fit_cues_cps(list(cues_seq), max_cps=20.0)
        cps_seq_shaped = cps_of_cues(cues_seq_shaped)
        # 批推
        t0 = time.time()
        pipe = BatchedInferencePipeline(model=model)
        bsegs = list(pipe.transcribe(
            audio, batch_size=8, beam_size=5, vad_filter=True,
            word_timestamps=True,
            condition_on_previous_text=True)[0])
        t_bat = time.time() - t0
        bhyp = words(" ".join(s.text for s in bsegs))
        wer_bat = edit_dist(bhyp, ref) / max(1, len(ref))
        bcues = P.shape_batched_cues(
            P.build_cues(P.split_batched_segments(bsegs)))
        cps_bat = cps_of_cues(bcues)
        bcues_shaped = P.fit_cues_cps(list(bcues), max_cps=20.0)
        cps_bat_shaped = cps_of_cues(bcues_shaped)

        def cps_stats(cps):
            return {"p50": round(pct(cps, .5) or 0, 2),
                    "p90": round(pct(cps, .9) or 0, 2),
                    "over_20_pct": round(
                        100.0 * sum(1 for x in cps if x > 20) / max(1, len(cps)), 1)}

        m = {
            "seq": {"wer": round(wer_seq, 4), "time_s": round(t_seq, 2),
                    "words": len(hyp), "cues": len(cues_seq),
                    "cps": cps_stats(cps_seq),
                    "cps_shaped": cps_stats(cps_seq_shaped),
                    "cues_shaped": len(cues_seq_shaped)},
            "batch": {"wer": round(wer_bat, 4), "time_s": round(t_bat, 2),
                      "words": len(bhyp), "cues": len(bcues),
                      "cps": cps_stats(cps_bat),
                      "cps_shaped": cps_stats(cps_bat_shaped),
                      "cues_shaped": len(bcues_shaped)},
        }
        report["models"][name] = m
        print(f"  顺序 WER {wer_seq:.3f}（{len(hyp)} 词，{t_seq:.1f}s）"
              f" CPS P50 {m['seq']['cps']['p50']} / P90 {m['seq']['cps']['p90']}"
              f" >20 {m['seq']['cps']['over_20_pct']}%"
              f" → 切句后 {m['seq']['cps_shaped']['over_20_pct']}%"
              f"（{m['seq']['cues_shaped']} 条）")
        print(f"  批推 WER {wer_bat:.3f}（{len(bhyp)} 词，{t_bat:.1f}s）"
              f" CPS P50 {m['batch']['cps']['p50']} / P90 {m['batch']['cps']['p90']}"
              f" >20 {m['batch']['cps']['over_20_pct']}%"
              f" → 切句后 {m['batch']['cps_shaped']['over_20_pct']}%"
              f"（{m['batch']['cues_shaped']} 条）")

    out = os.path.join(HERE, "work", "asr_eval.json")
    try:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        json.dump(report, open(out, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print("\n基线已写入", out)
    except OSError:
        pass
    if a.json:
        print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
