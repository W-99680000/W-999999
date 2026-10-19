# -*- coding: utf-8 -*-
"""bench/eval_qwen3asr.py —— Qwen3-ASR-0.6B POC：与 faster-whisper 对照。

样本：
  loopback_test.wav / jfk.wav   英文（已知参照）
  tts_zh.wav                    中文 TTS（bench/_gen_tts_samples.py 生成）
  tts_vi.wav                    越南语 TTS（同上）

指标：英文/越南语用词级 WER，中文用字级 CER（另附词级参考值）。
注意：TTS 样本干净，绝对值偏乐观；意义在两个模型的**相对差**。

用法：
  python bench/eval_qwen3asr.py                 # qwen3-0.6B vs whisper medium
  python bench/eval_qwen3asr.py --whisper medium,large-v3-turbo
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

QWEN_DIR = os.path.join(HERE, "models", "Qwen3-ASR-0.6B")

_DIGITS = {"zero": "0", "one": "1", "two": "2", "three": "3",
           "four": "4", "five": "5", "six": "6", "seven": "7",
           "eight": "8", "nine": "9", "ten": "10"}

SAMPLES = [
    {"id": "loopback_en", "wav": "samples/loopback_test.wav", "lang": "en",
     "ref": None},  # 参照由 UNIT 平铺生成（同 eval_asr.py）
    {"id": "jfk_en", "wav": "samples/jfk.wav", "lang": "en",
     "ref": ("and so my fellow americans ask not what your country can do "
             "for you ask what you can do for your country")},
    {"id": "tts_zh", "wav": "samples/tts_zh.wav", "lang": "zh",
     "ref_file": "samples/tts_zh.txt"},
    {"id": "tts_vi", "wav": "samples/tts_vi.wav", "lang": "vi",
     "ref_file": "samples/tts_vi.txt"},
]

UNIT = ("hello this is a test of the loopback capture system "
        "one two three four five")
UNIT_SEC = 6.9

QWEN_LANG = {"en": "English", "zh": "Chinese", "vi": "Vietnamese"}


def load_wav(path):
    import wave

    import numpy as np
    with wave.open(path, "rb") as w:
        n, ch, rate = w.getnframes(), w.getnchannels(), w.getframerate()
        raw = w.readframes(n)
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    if rate != 16000:
        idx = np.linspace(0, len(a) - 1, int(len(a) * 16000 / rate))
        a = np.interp(idx, np.arange(len(a)), a).astype(np.float32)
    return a


def words(s):
    ws = re.findall(r"[a-z0-9'àâăêôơưđáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩị"
                    r"óòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ]+",
                    (s or "").lower())
    return [_DIGITS.get(w, w) for w in ws]


def chars(s):
    # 中文字级序列：只留 CJK 与字母数字
    return [c for c in (s or "").lower()
            if "一" <= c <= "鿿" or c.isalnum()]


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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--whisper", default="medium")
    ap.add_argument("--no-qwen", action="store_true")
    a = ap.parse_args()

    import pretranslate as P  # noqa: F401  (复用其缓存定位)

    # 解析样本与参照
    cases = []
    for s in SAMPLES:
        wav = os.path.join(HERE, s["wav"])
        if not os.path.isfile(wav):
            print("缺样本，跳过:", wav)
            continue
        audio = load_wav(wav)
        dur = len(audio) / 16000
        if s["id"] == "loopback_en":
            reps = max(1, int(round(dur / UNIT_SEC)))
            ref_text = " ".join([UNIT] * reps)
        elif "ref_file" in s:
            ref_text = open(os.path.join(HERE, s["ref_file"]),
                            encoding="utf-8").read()
        else:
            ref_text = s["ref"]
        cases.append({**s, "audio": audio, "dur": dur, "ref_text": ref_text})
    if not cases:
        print("没有可用样本")
        return 1

    backends = []

    def score(hyp_text, ref_text, lang):
        if lang == "zh":
            r, h = chars(ref_text), chars(hyp_text)
        else:
            r, h = words(ref_text), words(hyp_text)
        return edit_dist(h, r) / max(1, len(r)), len(h)

    results = {}

    # ── faster-whisper 基线 ────────────────────────────────
    for name in [x.strip() for x in a.whisper.split(",") if x.strip()]:
        print(f"\n=== faster-whisper {name} (cpu/int8) ===")
        model = P.load_asr(name)
        res = {}
        for c in cases:
            t0 = time.time()
            segs = list(model.transcribe(
                c["audio"], language=c["lang"], beam_size=5,
                vad_filter=True, word_timestamps=True,
                condition_on_previous_text=True)[0])
            dt = time.time() - t0
            text = " ".join(x.text for x in segs).strip()
            err, ntok = score(text, c["ref_text"], c["lang"])
            res[c["id"]] = {"err": round(err, 4), "time_s": round(dt, 2),
                            "text": text}
            print(f"  {c['id']}: {'CER' if c['lang']=='zh' else 'WER'} "
                  f"{err:.3f}  {dt:.1f}s  | {text[:70]}")
        results[f"whisper-{name}"] = res
        del model

    # ── Qwen3-ASR-0.6B ─────────────────────────────────────
    if not a.no_qwen:
        if not os.path.isdir(QWEN_DIR):
            print("\n缺模型目录:", QWEN_DIR)
        else:
            print("\n=== Qwen3-ASR-0.6B (cpu/fp32) ===")
            import torch
            from qwen_asr import Qwen3ASRModel
            t0 = time.time()
            qmodel = Qwen3ASRModel.from_pretrained(
                QWEN_DIR, dtype=torch.float32, device_map="cpu",
                max_inference_batch_size=1, max_new_tokens=256)
            print(f"  模型加载 {time.time()-t0:.1f}s")
            res = {}
            for c in cases:
                t0 = time.time()
                out = qmodel.transcribe(
                    audio=(c["audio"], 16000),
                    language=QWEN_LANG[c["lang"]])[0]
                dt = time.time() - t0
                text = (out.text or "").strip()
                err, ntok = score(text, c["ref_text"], c["lang"])
                res[c["id"]] = {"err": round(err, 4), "time_s": round(dt, 2),
                                "text": text, "detected_lang": out.language}
                print(f"  {c['id']}: {'CER' if c['lang']=='zh' else 'WER'} "
                      f"{err:.3f}  {dt:.1f}s  lang={out.language}"
                      f"  | {text[:70]}")
            results["qwen3-asr-0.6b"] = res
            del qmodel

    out_path = os.path.join(HERE, "work", "asr_eval_qwen3.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    json.dump(results, open(out_path, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\n结果已写入", out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
