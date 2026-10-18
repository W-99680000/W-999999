# -*- coding: utf-8 -*-
"""tools/test_engine_stop.py —— 三个回归点：

① 管理端拉起的引擎是 pythonw.exe，且没有 conhost 子进程（无黑色命令行窗口）
② 主动停止后进程真的退出
③ 看门狗开启时，停止后不会被自动重启（之前 exit 1 被当崩溃 → "停止失败"）
"""
from __future__ import annotations

import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "babel_admin"))

import serve as S  # noqa: E402

n_ok = n_bad = 0


def chk(name, ok, detail=""):
    global n_ok, n_bad
    if ok:
        n_ok += 1
        print(f"  ok   {name}")
    else:
        n_bad += 1
        print(f"  FAIL {name}  {detail}")


def conhost_children(pid):
    try:
        import psutil
        p = psutil.Process(pid)
        return [c.pid for c in p.children()
                if (c.name() or "").lower() == "conhost.exe"]
    except Exception:
        return []


def main() -> int:
    print("=" * 56)
    print("管理端引擎：无黑窗 + 停止不被看门狗拉起")
    S.start_background(prewarm_on_open=False)   # 开看门狗
    time.sleep(0.3)
    ok, msg = S.ENGINE.start("loopback")
    chk("启动成功", ok, msg)
    chk("启动解释器是 pythonw.exe",
        os.path.basename((S.ENGINE.argv or [""])[0]).lower() == "pythonw.exe",
        (S.ENGINE.argv or [""])[0])
    ready = S.ENGINE.wait_ready(90)
    chk("引擎 ready", ready.get("state") == "ready", ready)
    pid = S.ENGINE.proc.pid if S.ENGINE.proc else None
    chk("拿到 pid", bool(pid), pid)
    try:
        import psutil
        name = psutil.Process(pid).name().lower()
    except Exception:
        name = ""
    chk("引擎进程是 pythonw", name == "pythonw.exe", name)
    chk("没有 conhost 子进程（无黑色命令行窗口）",
        not conhost_children(pid), conhost_children(pid))

    res = S.ENGINE.stop(wait=20)
    chk("主动停止成功", res[0] and not S.ENGINE.alive(), res)
    # 看门狗 3s 一轮；等 8s 看它会不会把"停止"误当崩溃重启
    time.sleep(8)
    st = S.ENGINE.status()
    chk("停止后 8s 没有被看门狗自动重启",
        st.get("running") is False, st)

    print(f"\n合计 {n_ok} 通过 / {n_bad} 失败")
    return 0 if n_bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
