# -*- coding: utf-8 -*-
"""tools/test_overlay_control.py —— 浮窗控制命令路由回归（不碰真实浮窗）。

背景：视频翻译的 watch 浮窗用 watch_cmd.json；旧版后台只写
control_cmd.json → 面板所有调控失效。这里用临时文件 + 本地 HTTP 验证：
  ① POST /api/overlay 同时写两套命令文件（协议 _ts/_changes 正确）
  ② watch 在线时 GET /api/overlay 返回 target="watch" 且是 watch 设置
  ③ 面板校验仍工作（坏字段 400）
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import urllib.request
from http.server import ThreadingHTTPServer

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


def post(url, obj):
    req = urllib.request.Request(
        url, data=json.dumps(obj).encode(), method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.loads(r.read().decode())


def main() -> int:
    print("=" * 56)
    td = tempfile.mkdtemp(prefix="babel-ov-")
    try:
        paths = {
            "OVERLAY_SETTINGS": "overlay_qt.json",
            "OVERLAY_STATE": "overlay_state.json",
            "OVERLAY_CMD": "control_cmd.json",
            "WATCH_SETTINGS": "watch_overlay.json",
            "WATCH_STATE": "watch_overlay_state.json",
            "WATCH_CMD": "watch_cmd.json",
        }
        for attr, name in paths.items():
            setattr(S, attr, os.path.join(td, name))
        # watch 在线（_app_ts 新鲜），loopback 不在线
        now = __import__("time").time()
        json.dump({"font_size": 17, "_app_ts": now},
                  open(S.WATCH_SETTINGS, "w"), ensure_ascii=False)
        json.dump({"ts": now, "kind": "subtitle"},
                  open(S.WATCH_STATE, "w"), ensure_ascii=False)
        json.dump({"font_size": 24, "_app_ts": now - 600},
                  open(S.OVERLAY_SETTINGS, "w"), ensure_ascii=False)

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), S.Handler)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{port}"

        st, j = post(base + "/api/overlay",
                     {"changes": {"font_size": 33, "opacity": 0.8}})
        chk("POST 成功", st == 200 and j.get("ok") is True, (st, j))
        chk("targets 同时含直播+watch",
            j.get("targets") == ["loopback", "watch"], j.get("targets"))

        for p, label in ((S.OVERLAY_CMD, "control_cmd"),
                         (S.WATCH_CMD, "watch_cmd")):
            cur = json.load(open(p, encoding="utf-8"))
            chk(f"{label}: 有 _ts", isinstance(cur.get("_ts"), (int, float)), cur)
            chk(f"{label}: _changes 正确",
                cur.get("_changes") == {"font_size": 33, "opacity": 0.8},
                cur.get("_changes"))

        ov = get(base + "/api/overlay")
        chk("watch 在线时 GET target=watch", ov.get("target") == "watch", ov)
        chk("GET 返回 watch 的设置（不是直播的 24）",
            ov.get("settings", {}).get("font_size") == 17, ov.get("settings"))
        chk("GET online=True", ov.get("online") is True, ov.get("online"))

        st, j = post(base + "/api/overlay", {"changes": {"bad_key": 1}})
        chk("非法字段仍被拒（400）", st == 400 and j.get("ok") is False, (st, j))

        httpd.shutdown()
    finally:
        import shutil
        shutil.rmtree(td, ignore_errors=True)

    print(f"\n合计 {n_ok} 通过 / {n_bad} 失败")
    return 0 if n_bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
