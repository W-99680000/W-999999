# -*- coding: utf-8 -*-
"""tools/soak_test.py —— 直播翻译稳定性 soak（默认 5 分钟，可调 8h）。

做法：隐藏启动引擎 → 循环播放样本音频 → 按 15s 采样：
  · 进程是否活着
  · RSS（内存增长是长跑泄漏的早期信号）
  · 日志里有没有 Traceback
  · metrics_live.jsonl 新增批次（确认真的在干活）
结束时写停止哨兵，验证优雅退出。

用法：
  python tools/soak_test.py                 # 300s
  python tools/soak_test.py --seconds 60    # 冒烟
  python tools/soak_test.py --seconds 28800 # 8h（配合 --json 出报告）
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

WORK = os.path.join(HERE, "work")
LOG = os.path.join(WORK, "soak.log")
SRT = os.path.join(WORK, "soak.srt")
STOP = os.path.join(WORK, "engine_stop.flag")
METRICS = os.path.join(WORK, "metrics_live.jsonl")
WAV = os.path.join(HERE, "samples", "loopback_test.wav")


def rss_mb(pid):
    try:
        import psutil
        return psutil.Process(pid).memory_info().rss / 1024 / 1024
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=300)
    ap.add_argument("--sample", default=WAV)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if not os.path.isfile(a.sample):
        print("缺样本:", a.sample)
        return 1
    os.makedirs(WORK, exist_ok=True)
    for p in (LOG, SRT, STOP):
        try:
            os.remove(p)
        except OSError:
            pass

    m0 = 0
    if os.path.isfile(METRICS):
        with open(METRICS, encoding="utf-8") as f:
            m0 = sum(1 for _ in f)

    logf = open(LOG, "wb")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.Popen(
        [sys.executable, "-u", os.path.join(HERE, "live_subtitle_v2.py"),
         "--no-overlay", "--srt", SRT],
        cwd=HERE, env=env, stdout=logf, stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    # 等 ready
    ready = False
    t0 = time.time()
    while time.time() - t0 < 60:
        try:
            txt = open(LOG, encoding="utf-8", errors="ignore").read()
        except OSError:
            txt = ""
        if "转写:" in txt:
            ready = True
            break
        if p.poll() is not None:
            break
        time.sleep(0.4)
    print(f"ready={ready} in {time.time()-t0:.1f}s pid={p.pid}")

    # 循环播放样本（系统声音 → WASAPI 环回）
    try:
        import winsound
        winsound.PlaySound(a.sample, winsound.SND_FILENAME
                           | winsound.SND_ASYNC | winsound.SND_LOOP)
    except Exception as e:
        print("播放失败（不影响引擎存活检查）:", e)

    samples = []
    tracebacks = 0
    alive_all = True
    start = time.time()
    try:
        while time.time() - start < a.seconds:
            time.sleep(15)
            elapsed = time.time() - start
            alive = p.poll() is None
            alive_all = alive_all and alive
            rss = rss_mb(p.pid) if alive else None
            if rss is not None:
                samples.append((round(elapsed, 1), round(rss, 1)))
            try:
                txt = open(LOG, encoding="utf-8", errors="ignore").read()
            except OSError:
                txt = ""
            tracebacks = txt.count("Traceback (most recent call last)")
            print(f"  t={elapsed:5.0f}s alive={alive} rss={rss}MB "
                  f"traceback={tracebacks}")
            if not alive:
                break
    finally:
        try:
            import winsound
            winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception:
            pass
        try:
            open(STOP, "w").write(str(time.time()))
        except OSError:
            pass
        try:
            rc = p.wait(timeout=25)
        except Exception:
            p.terminate()
            rc = -1
        logf.close()

    # 指标汇总
    new_batches = 0
    asr = []
    try:
        with open(METRICS, encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i < m0:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("kind") == "batch":
                    new_batches += 1
                    if r.get("asr_s") is not None:
                        asr.append(float(r["asr_s"]))
    except OSError:
        pass
    rss_start = samples[0][1] if samples else None
    rss_end = samples[-1][1] if samples else None
    grew = (rss_end - rss_start) if (rss_start and rss_end) else None

    out = {
        "seconds": a.seconds,
        "ready": ready,
        "alive_all": alive_all,
        "exit_code": rc,
        "batches": new_batches,
        "asr_s_p50": (sorted(asr)[len(asr) // 2] if asr else None),
        "rss_start_mb": rss_start,
        "rss_end_mb": rss_end,
        "rss_grew_mb": round(grew, 1) if grew is not None else None,
        "tracebacks": tracebacks,
        "log": LOG,
    }
    if a.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        print("\n== soak 结果 ==")
        for k, v in out.items():
            print(f"  {k}: {v}")

    ok = (ready and alive_all and new_batches > 0 and tracebacks == 0
          and rc not in (None,) and (grew is None or grew < 200))
    print("\n" + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
