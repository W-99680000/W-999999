# -*- coding: utf-8 -*-
"""tools/quality_report.py —— 读 work/metrics_live.jsonl，出稳定性/实时性基线。

用法:
    python tools/quality_report.py
    python tools/quality_report.py --file work/metrics_live.jsonl --json

指标口径:
  · asr_s     每批识别耗时（P50/P90/max）—— 实时性主因
  · audio_s   每批音频时长；audio_s/asr_s 即识别吞吐倍率
  · ready_s   启动到模型就绪的秒数
  · shown     每批上屏段数（翻译并行的产出）
  · rejected  噪声门拒掉的段数（稳定性/门限是否过紧）
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT = os.path.join(HERE, "work", "metrics_live.jsonl")


def pct(xs, q):
    if not xs:
        return None
    ys = sorted(xs)
    k = max(0, min(len(ys) - 1, int(round((len(ys) - 1) * q))))
    return ys[k]


def load(path):
    rows = []
    if not os.path.isfile(path):
        return rows
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=DEFAULT)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--limit", type=int, default=0,
                    help="只看最近 N 批（0=全部）")
    a = ap.parse_args()

    rows = load(a.file)
    batches = [r for r in rows if r.get("kind") == "batch"]
    startups = [r for r in rows if r.get("kind") == "startup"]
    if a.limit:
        batches = batches[-a.limit:]
    asr = [float(r["asr_s"]) for r in batches if r.get("asr_s") is not None]
    aud = [float(r["audio_s"]) for r in batches if r.get("audio_s")]
    shown = [int(r.get("shown") or 0) for r in batches]
    rej = [int(r.get("rejected") or 0) for r in batches]
    total_audio = sum(aud)
    total_asr = sum(asr)
    out = {
        "file": a.file,
        "batches": len(batches),
        "startups": [{"mode": s.get("mode"), "model": s.get("model"),
                      "ready_s": s.get("ready_s")} for s in startups[-5:]],
        "asr_s": {"p50": pct(asr, .5), "p90": pct(asr, .9),
                  "max": max(asr) if asr else None},
        "audio_s": {"total": round(total_audio, 1),
                    "per_batch_p50": pct(aud, .5)},
        "throughput_x": (round(total_audio / total_asr, 2)
                         if total_asr else None),
        "shown_total": sum(shown),
        "rejected_total": sum(rej),
    }
    # 发批原因分布 + 三类拒段（累计值取最后一条，不求和）
    flush = {}
    for r in batches:
        k = r.get("flush") or "-"
        flush[k] = flush.get(k, 0) + 1
    last = batches[-1] if batches else {}
    out["flush"] = flush
    out["rejected"] = {
        "hallucination": last.get("rejected_hallu"),
        "low_quality": last.get("rejected_lowq"),
        "remote_empty": last.get("rejected_remote"),
    }
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    print(f"文件: {a.file}")
    if not batches:
        print("还没有 batch 指标 —— 先跑一次直播翻译（可播 samples/loopback_test.wav）")
        return 0
    print(f"批次: {len(batches)}　音频 {total_audio:.1f}s　"
          f"识别总耗时 {total_asr:.1f}s　吞吐 {out['throughput_x']}x")
    print(f"识别耗时: P50 {out['asr_s']['p50']:.3f}s　"
          f"P90 {out['asr_s']['p90']:.3f}s　max {out['asr_s']['max']:.3f}s")
    print(f"上屏段数: 本批合计 {out['shown_total']}　"
          f"噪声门拒段 {out['rejected_total']}")
    print("发批原因: " + "　".join(f"{k}={v}" for k, v in
                                   sorted(out["flush"].items())))
    rj = out["rejected"]
    print(f"累计拒段: 幻觉 {rj['hallucination']}　"
          f"低质量 {rj['low_quality']}　远端空 {rj['remote_empty']}")
    for s in out["startups"]:
        print(f"启动: mode={s['mode']} model={s.get('model')} "
              f"就绪 {s.get('ready_s')}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
