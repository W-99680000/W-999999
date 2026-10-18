# -*- coding: utf-8 -*-
"""startup_stats.py —— 引擎启动耗时记录

干什么：在 live_subtitle_v2.run() 的关键阶段打点，退出/就绪时把
{总耗时, 各阶段耗时} 追加写进 work/startup_stats.json（保留最近 50 次）。
后台「启动」页读它，让用户看到"这次启动哪一步最慢"。

用法（引擎里）：
    import startup_stats as SS
    SS.mark("gpu_probe")     # 阶段结束就打一个点
    SS.flush_ready()         # 浮窗出现/开始转写时算"就绪"
"""
from __future__ import annotations

import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "work", "startup_stats.json")
KEEP = 50

_t0 = time.time()
_marks: list[tuple[str, float]] = []
_flushed = False


def mark(stage: str) -> None:
    _marks.append((stage, time.time() - _t0))


def flush_ready(extra: dict | None = None) -> None:
    """就绪时调用：本次启动记入历史。只写一次。"""
    global _flushed
    if _flushed:
        return
    _flushed = True
    stages: dict[str, float] = {}
    prev = 0.0
    for name, t in _marks:
        stages[name] = round(t - prev, 2)
        prev = t
    rec = {"ts": _t0, "total": round(prev, 2), "stages": stages}
    if extra:
        rec.update(extra)
    try:
        os.makedirs(os.path.dirname(PATH), exist_ok=True)
        try:
            with open(PATH, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
        hist = data.get("history", [])
        hist.append(rec)
        data = {"history": hist[-KEEP:], "last": rec}
        tmp = PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, PATH)
    except Exception:
        pass
