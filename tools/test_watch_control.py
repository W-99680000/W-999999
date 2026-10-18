# -*- coding: utf-8 -*-
"""tools/test_watch_control.py —— 视频翻译（cache）模式必须拉起 watch 模块。

回归点：
  ① ENGINE.start("cache") 的 argv 是 watch_player.py --auto（不是 live 引擎）
  ② 进程活着并能等到 ready（watch 浮窗/常驻日志）
  ③ 停止哨兵能让 watch 干净退出（exit 0/1 都接受，但不能残留进程）
  ④ 不留下 pretranslate 子进程
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


def pretranslate_procs():
    try:
        import psutil
    except Exception:
        return []
    out = []
    for p in psutil.process_iter(["pid", "cmdline"]):
        cmd = " ".join(p.info.get("cmdline") or [])
        if "pretranslate.py" in cmd:
            out.append(p.info["pid"])
    return out


def main() -> int:
    print("=" * 56)
    print("视频翻译（cache）模式 → watch_player --auto")
    S.start_background(prewarm_on_open=False)
    ok, msg = S.ENGINE.start("cache")
    chk("启动成功", ok, msg)
    argv = " ".join(S.ENGINE.argv or [])
    chk("拉的是 watch_player.py", "watch_player.py" in argv, argv)
    chk("带 --auto", "--auto" in (S.ENGINE.argv or []), argv)
    chk("不是 live 引擎", "live_subtitle_v2.py" not in argv, argv)

    ready = S.ENGINE.wait_ready(45)
    chk("watch 报到 ready", ready.get("state") == "ready", ready)
    time.sleep(2)
    log = "\n".join(S.ENGINE.lines)
    chk("日志有 watch 常驻标记",
        ("watch --auto 启动" in log) or ("常驻字幕已启动" in log), log[-200:])
    chk("进程活着", S.ENGINE.alive(), "")

    t0 = time.time()
    res = S.ENGINE.stop(wait=20)
    chk("停止哨兵生效、进程退出",
        res[0] and not S.ENGINE.alive(),
        f"{res} in {time.time()-t0:.1f}s")
    time.sleep(1.0)
    left = pretranslate_procs()
    chk("不残留 pretranslate 子进程", not left, left)

    print(f"\n合计 {n_ok} 通过 / {n_bad} 失败")
    return 0 if n_bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
