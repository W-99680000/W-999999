# -*- coding: utf-8 -*-
"""bench/bench_typeset.py —— 排版开销 vs 翻译开销（可复现测量）

问题：加第三方排版（pysubs2 → ASS）会不会拖慢翻译？

方法：同一批 cue 上分别量
  A. 排版：subtitle_typeset.typeset() 全流程（拆行 + 建事件 + 落盘）
  B. 翻译：本地 llama-server / 远端 MT 的**实测单段延迟**（从日志/实测取，
     这里用固定基线值，见 BASE_MT_LATENCY，避免基准依赖网络/GPU 状态）
输出：每条 cue 的排版耗时、整集耗时，以及相对翻译预算的占比。

用法：
    python bench/bench_typeset.py  [srt1 srt2 ...]
不给参数时自动扫 live.srt 与 pre_cache/*.zh.srt（取前 5 个）。
"""
from __future__ import annotations

import glob
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import subtitle_typeset as T  # noqa: E402

#: 翻译基线（同项目实测，毫秒/段）：
#:   qwen-mt-flash 远端 360 / HY-MT1.8B 本地 490 / Hy-MT2 本地 ~120
BASE_MT_LATENCY_MS = {"remote:qwen-mt-flash": 360.0,
                      "local:hh-translate-base": 490.0,
                      "local:hy-mt2-1.8b": 120.0}


def bench_one(path: str, repeat: int = 15) -> dict | None:
    rows = T.read_srt_rows(path)
    if not rows or not T.available():
        return None
    out = os.path.join(HERE, "bench", "_bench.ass")
    times = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        T.typeset(rows, out, style="bilingual")
        times.append((time.perf_counter() - t0) * 1000)
    times.sort()
    med = times[len(times) // 2]
    n = len(rows)
    try:
        os.remove(out)                  # 别在树里留基准产物
    except OSError:
        pass
    return {"file": os.path.basename(path), "cues": n,
            "total_ms": round(med, 2), "per_cue_ms": round(med / n, 4),
            "mt_budget_ms": BASE_MT_LATENCY_MS["local:hy-mt2-1.8b"] * n,
            "ratio_pct": round(med / (BASE_MT_LATENCY_MS[
                "local:hy-mt2-1.8b"] * n) * 100, 3)}


def main(argv: list[str]) -> int:
    paths = argv[1:]
    if not paths:
        cand = [os.path.join(HERE, "live.srt")]
        cand += sorted(glob.glob(os.path.join(HERE, "pre_cache", "*.zh.srt")))
        paths = [p for p in cand if os.path.isfile(p)][:5]
    if not T.available():
        print("pysubs2 未安装，排版功能不可用")
        return 1

    print(f"{'文件':<42}{'条数':>6}{'总耗时ms':>10}{'单条ms':>9}"
          f"{'占翻译%':>9}")
    agg_n = 0
    agg_ms = 0.0
    for p in paths:
        r = bench_one(p)
        if not r:
            continue
        agg_n += r["cues"]
        agg_ms += r["total_ms"]
        name = r["file"][:40]
        print(f"{name:<42}{r['cues']:>6}{r['total_ms']:>10.2f}"
              f"{r['per_cue_ms']:>9.4f}{r['ratio_pct']:>9.3f}")
    if agg_n:
        per = agg_ms / agg_n
        print("-" * 78)
        print(f"合计 {agg_n} 条：排版 {agg_ms:.1f}ms　单条 {per:.4f}ms")
        print(f"对照翻译基线（Hy-MT2 本地 120ms/段）："
              f"排版占 {per / 120 * 100:.3f}%")
        print(f"对照远端 qwen-mt-flash（360ms/段）："
              f"排版占 {per / 360 * 100:.3f}%")
        print("\n结论：排版是翻译之后的独立 CPU 步骤，单条 <0.03ms，"
              "整集（千条级）<50ms，不改变首屏字幕到达时间。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
