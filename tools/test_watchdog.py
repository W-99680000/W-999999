# -*- coding: utf-8 -*-
"""tools/test_watchdog.py —— 验证引擎看门狗：强杀后自动重启并恢复到 ready。

不依赖管理端 HTTP；直接驱动 babel_admin.serve.ENGINE + 看门狗线程。
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


def main() -> int:
    print("=" * 56)
    print("看门狗回归：强杀引擎 → 自动重启 → ready")
    S.start_background(prewarm_on_open=False)
    time.sleep(0.3)
    ok, msg = S.ENGINE.start("loopback")
    chk("引擎启动", ok, msg)
    ready = S.ENGINE.wait_ready(90)
    chk("启动后 ready", ready.get("state") == "ready", ready)
    p1 = S.ENGINE.proc.pid if S.ENGINE.proc else None
    chk("拿到 pid", bool(p1), p1)

    print(f"  强杀 pid={p1} …")
    try:
        S.ENGINE.proc.kill()
    except Exception as e:
        chk("强杀成功", False, e)
        return 1

    # 看门狗 3s 一轮；给到 40s（含模型重新加载）
    t0 = time.time()
    p2 = p1
    while time.time() - t0 < 40:
        time.sleep(1.0)
        if S.ENGINE.alive() and S.ENGINE.proc and S.ENGINE.proc.pid != p1:
            p2 = S.ENGINE.proc.pid
            break
    chk("看门狗拉起新进程（pid 变化）", p2 != p1, f"{p1} -> {p2}")
    ready = S.ENGINE.wait_ready(90)
    chk("重启后 ready", ready.get("state") == "ready", ready)
    # start() 会清空日志缓冲，看门狗提示在重启瞬间已被清掉 ——
    # 以「pid 变化 + 重新 ready」为重启证据，另查看门狗开关是否开启。
    chk("看门狗开关开启", bool(S._admin_cfg().get("watchdog", True)))

    t_stop = time.time()
    stopped = S.ENGINE.stop(wait=15)
    chk("主动停止干净退出", stopped[0] and not S.ENGINE.alive(),
        f"{stopped} in {time.time()-t_stop:.1f}s")

    print(f"\n合计 {n_ok} 通过 / {n_bad} 失败")
    return 0 if n_bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
