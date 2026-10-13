# -*- coding: utf-8 -*-
"""验证：预翻译是否"边翻边落盘"（分块流水）。

做法：--force 跑一次 pretranslate，同时每 0.4s 看一次 srt 文件：
  · 是否**在进程结束前**就出现（说明不是等全部跑完才写）
  · 条数是否**持续增长**（说明一块一块往前推）
同时把 pretranslate 的 stdout 打上时间戳，看"已出字幕到 X 分钟"的节奏。
"""
import os
import re
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)

SRC = (sys.argv[1] if len(sys.argv) > 1
       else "pre_cache/url_cache/BV1mQ8EzsEpR.m4a")
SRT = os.path.splitext(os.path.abspath(SRC))[0] + ".zh.srt"
PY = sys.executable

for p in (SRT, SRT + ".tmp", SRT + ".meta.json"):
    if os.path.exists(p):
        os.remove(p)

env = dict(os.environ, PYTHONIOENCODING="utf-8", HF_HUB_OFFLINE="1")
t0 = time.time()
proc = subprocess.Popen(
    [PY, "-u", "pretranslate.py", SRC, "--force"],
    cwd=HERE, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    text=True, encoding="utf-8", errors="replace", bufsize=1)


def reader():
    for raw in proc.stdout:
        for ln in (raw or "").replace("\r", "\n").split("\n"):
            ln = ln.strip()
            if ln:
                print(f"[{time.time()-t0:6.1f}s] {ln}", flush=True)


threading.Thread(target=reader, daemon=True).start()

timeline = []
last = None
while proc.poll() is None:
    ex = os.path.isfile(SRT)
    sz = os.path.getsize(SRT) if ex else 0
    if (ex, sz) != last:
        last = (ex, sz)
        n = 0
        if ex and sz:
            try:
                n = len(re.findall(
                    r"-->", open(SRT, encoding="utf-8-sig",
                                 errors="replace").read()))
            except Exception:
                n = 0
        timeline.append((round(time.time() - t0, 1), ex, sz, n))
        print(f"[{time.time()-t0:6.1f}s] >>> SRT 存在={ex} 字节={sz} 条数={n}",
              flush=True)
    time.sleep(0.4)
proc.wait()
print(f"\n进程结束，用时 {time.time()-t0:.1f}s，returncode={proc.returncode}")
print("首条 SRT（在进程结束前出现的时刻）:", timeline[0] if timeline else None)
