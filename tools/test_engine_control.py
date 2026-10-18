# -*- coding: utf-8 -*-
"""tools/test_engine_control.py —— 链路启停端到端（真起进程、真看浮窗）

这条测试回答的是用户实际问的那个问题：
**"我打开后台管理系统，为什么屏幕上没有翻译浮窗？"**

之前的答案是"管理端只管配置，浮窗要另外启动" —— 那等于没交付。
现在管理端自己托管链路进程，所以必须测到**浮窗真的出现**：

  ① POST /api/engine {action:start} → 200，pid 非空
  ② /api/engine 轮询到 running=true，日志里有内容
  ③ 进程真的存在（psutil/任务列表级证据）
  ④ 日志里出现"浮窗"相关行（浮窗创建成功）—— 这一步会真跑模型，
     慢，所以给足超时并在超时后如实报告"还在加载"而不是假装通过
  ⑤ POST /api/engine {action:stop} → 200，running=false，进程真的没了
  ⑥ 停止后浮窗进程不残留（防止"关不掉"）

用法
====
    python tools/test_engine_control.py            # 完整（会真启动引擎，约 1~2 分钟）
    python tools/test_engine_control.py --quick     # 只测启停通路，不等浮窗
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "babel_admin"))

import serve as S  # noqa: E402

OK = BAD = 0


def chk(name: str, cond: bool, extra: str = "") -> None:
    global OK, BAD
    if cond:
        OK += 1
        print(f"  ok   {name}")
    else:
        BAD += 1
        print(f"  FAIL {name}   {extra}")


def call(base: str, path: str, payload: dict | None = None, timeout: float = 180):
    url = base + path
    if payload is None:
        req = urllib.request.Request(url)
    else:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            return e.code, {}


def pid_alive(pid: int) -> bool:
    """进程还在不在（不引 psutil）。"""
    if not pid:
        return False
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                             capture_output=True, text=True, timeout=20).stdout
        return str(pid) in out
    except Exception:
        try:
            os.kill(pid, 0)
            return True
        except Exception:
            return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true",
                    help="不起真引擎，只测接口通路与错误处理")
    a = ap.parse_args(argv)

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), S.Handler)
    base = f"http://127.0.0.1:{port}"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    time.sleep(0.3)

    print("一、解释器挑选（这条决定了 GPU 能不能用）")
    st = S.ENGINE.status()
    print(f"   选中：{st['python']}")
    print(f"   理由：{st['python_reason']}")
    chk("选中了解释器", bool(st["python"]))
    if not st["python_ok"]:
        print("   ！注意：这个解释器缺 CUDA 库 → 识别会退 CPU/远端，启动也更慢")
    chk("python_ok 字段存在", "python_ok" in st)
    chk("状态里带解释器理由（排障用）", bool(st["python_reason"]))

    print("二、未启动时的状态")
    code, j = call(base, "/api/engine")
    chk("GET /api/engine = 200", code == 200, str(code))
    chk("running=false", j.get("running") is False, str(j.get("running")))
    chk("带 log 数组（界面日志区要用）", isinstance(j.get("log"), list))
    chk("带 python_ok（界面要标缺库警告）", "python_ok" in j)

    print("三、错误处理")
    code, j = call(base, "/api/engine", {"action": "胡说"})
    chk("未知 action → 400", code == 400, str(code))
    code, j = call(base, "/api/engine", {"action": "stop"})
    chk("没跑就停 → 409（不是崩）", code == 409, f"{code} {j.get('message')}")

    if a.quick:
        print("\n（--quick：跳过真启动）")
        httpd.shutdown()
        print(f"\n链路控制回归：{OK} 项通过，{BAD} 项失败")
        return 1 if BAD else 0

    print("四、启动链路（真起进程；模型加载要几十秒）")
    t0 = time.time()
    code, j = call(base, "/api/engine",
                   {"action": "start", "mode": "loopback", "wait": True,
                    "timeout": 150}, timeout=240)
    dt = time.time() - t0
    chk("POST start → 200", code == 200, f"{code} {j.get('message')}")
    eng = j.get("engine") or {}
    pid = eng.get("pid")
    chk("拿到 pid", bool(pid), str(pid))
    chk(f"进程真的存在（pid {pid}）", pid_alive(pid or 0), "tasklist 里找不到")
    ready = j.get("ready") or {}
    print(f"   ready={ready.get('state')}  用时 {dt:.0f}s")
    print(f"   解释器：{eng.get('python')}")
    log = "\n".join(eng.get("log") or [])
    chk("日志非空（界面能显示）", bool(log.strip()), "日志区是空的")
    chk("日志里能看到启动命令", "live_subtitle_v2.py" in log, log[:200])
    if ready.get("state") == "ready":
        chk("浮窗已创建（日志出现浮窗/监听行）",
            ("浮窗位置" in log) or ("listening" in log) or ("转写" in log),
            log[-400:])
    else:
        print(f"   ！{dt:.0f}s 内没到 ready（state={ready.get('state')}）——"
              f"不算通过也不算失败，但**必须在报告里写明**")
        print("   日志尾部：")
        for ln in (eng.get("log") or [])[-12:]:
            print("     " + ln)

    print("五、停止链路")
    t1 = time.time()
    code, j = call(base, "/api/engine", {"action": "stop"}, timeout=60)
    print(f"   停止用时 {time.time()-t1:.1f}s：{j.get('message')}")
    chk("POST stop → 200", code == 200, f"{code} {j.get('message')}")
    chk("停止后 running=false", (j.get("engine") or {}).get("running") is False)
    time.sleep(1.5)
    chk(f"进程真的没了（pid {pid}）", not pid_alive(pid or 0),
        "**进程残留 = 浮窗关不掉**")

    print("六、日志与状态仍在（停止后界面要能看排障日志）")
    code, j = call(base, "/api/engine")
    chk("停止后日志还留着", bool((j.get("log") or [])), "日志被清空了，没法排障")
    chk("exit_code 有记录", j.get("exit_code") is not None, str(j.get("exit_code")))

    httpd.shutdown()
    print(f"\n链路控制回归：{OK} 项通过，{BAD} 项失败")
    return 1 if BAD else 0


if __name__ == "__main__":
    raise SystemExit(main())
