# -*- coding: utf-8 -*-
"""tools/test_admin_api.py —— 后台管理端 API 回归

测的是**写路径**，因为写错会直接改动 model_registry.json —— 那是两个
翻译模块共用的默认模型来源。所以每次测试都在**临时目录的注册表副本**
上做（monkeypatch model_registry.PATH / WEB_PATH），绝不碰真注册表。

覆盖：
  ① GET /                    界面返回 HTML
  ② GET /api/models          快照字段齐全（界面渲染的输入）
  ③ GET /api/state           两个模式都能解析
  ④ POST /api/select         改 defaults.mt → 落盘 → 两个模式都跟着变
  ⑤ POST /api/select         白名单外的字段被拒（403/400）
  ⑥ POST /api/select         不存在的模型 id 被拒
  ⑦ POST /api/select         本机不可用的模型被拒；force=true 放行
  ⑧ POST /api/set-mode       只覆盖一个模式，另一个不受影响 ← 核心语义
  ⑨ 界面改完 → cli_args() 立刻反映（证明"界面改的就是程序读的"）

用法
====
    python tools/test_admin_api.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import model_registry as R            # noqa: E402
sys.path.insert(0, os.path.join(ROOT, "babel_admin"))
import serve as S                     # noqa: E402

ok = bad = 0


def chk(name: str, cond: bool, extra: str = "") -> None:
    global ok, bad
    if cond:
        ok += 1
        print(f"  ok   {name}")
    else:
        bad += 1
        print(f"  FAIL {name}   {extra}")


def call(base: str, path: str, payload: dict | None = None):
    # 路径里可能有中文（测未知路径时故意用中文）—— http.client 只接受
    # ASCII 请求行，不转义会在 putrequest 里抛 UnicodeEncodeError。
    url = base + urllib.parse.quote(path, safe="/?=&")
    if payload is None:
        req = urllib.request.Request(url)
    else:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(body) if body.strip().startswith(("{", "["))
                              else body)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, body


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="babel_api_")
    # **把注册表整体搬到临时目录** —— 测试不许动真文件
    shutil.copy(os.path.join(ROOT, "model_registry.json"),
                os.path.join(tmp, "model_registry.json"))
    R.PATH = os.path.join(tmp, "model_registry.json")
    R.WEB_PATH = os.path.join(tmp, "model_registry_web.json")
    R._cache = None
    try:
        base_ready = threading.Event()
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), S.Handler)
        port = httpd.server_address[1]
        base = f"http://127.0.0.1:{port}"
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        base_ready.wait(0.2)
        time.sleep(0.2)

        print("一、读路径")
        code, body = call(base, "/")
        chk("GET / 返回 HTML", code == 200 and "<html" in str(body).lower(),
            f"{code}")
        code, snap = call(base, "/api/models")
        chk("GET /api/models = 200", code == 200, str(code))
        need = ("asr", "mt", "modes", "defaults", "router", "patchable",
                "pending_hardcoded", "means")
        miss = [k for k in need if k not in snap]
        chk("快照字段齐全（界面渲染输入）", not miss, str(miss))
        chk("快照含实况可用性", all("available" in x for x in snap["asr"]))
        chk("快照含归属 used_by", all("used_by" in x for x in snap["mt"]))
        code, st = call(base, "/api/state")
        chk("GET /api/state 两个模式都能解析",
            code == 200 and set(st["resolved"]) == {"loopback", "cache"})
        chk("state 带上未接管的硬编码清单",
            isinstance(st.get("pending_hardcoded"), list)
            and len(st["pending_hardcoded"]) >= 3)

        print("二、写路径 · 白名单")
        code, r = call(base, "/api/select",
                       {"target": "asr", "value": "local:faster-whisper-small"})
        chk("不在白名单的字段被拒", code == 400 and not r.get("ok"), f"{code} {r}")
        code, r = call(base, "/api/select",
                       {"target": "defaults.asr", "value": "local:不存在"})
        chk("不存在的模型 id 被拒", code == 400, f"{code} {r}")
        chk("拒绝时给出可选清单", "可选" in str(r.get("error", "")), str(r)[:120])

        print("三、写路径 · 不可用模型")
        un = [x["id"] for x in R.probe()["mt"] if x.get("available") is False]
        if un:
            code, r = call(base, "/api/select",
                           {"target": "defaults.mt", "value": un[0]})
            chk("不可用模型默认被拒", code == 400, f"{code} {r}")
            code, r2 = call(base, "/api/select",
                            {"target": "defaults.mt", "value": un[0], "force": True})
            chk("force=true 放行", code == 200 and r2.get("ok"), f"{code} {r2}")
            # 放行后要还原，别把测试的痕迹留进后续断言
            call(base, "/api/select", {"target": "defaults.mt",
                                       "value": "remote:qwen-mt-flash"})
        else:
            chk("本机没有不可用模型（跳过这两项）", True)
            chk("本机没有不可用模型（跳过这两项）", True)

        print("四、核心语义：改 defaults 只带动跟随的模式")
        before = R.all_resolved()
        code, r = call(base, "/api/select",
                       {"target": "defaults.asr", "value": "local:faster-whisper-small"})
        chk("改 defaults.asr 成功", code == 200 and r.get("ok"), f"{code} {r}")
        after = R.all_resolved()
        chk("loopback（跟随）变了 → small",
            after["loopback"]["asr"] == "local:faster-whisper-small",
            after["loopback"]["asr"])
        chk("cache（显式覆盖）没变 → large-v3",
            after["cache"]["asr"] == "local:faster-whisper-large-v3",
            after["cache"]["asr"])
        chk("响应里回带最新快照",
            isinstance(r.get("snapshot"), dict) and "asr" in r["snapshot"])

        print("五、界面改的就是程序读的（cli_args 直连）")
        cl = R.cli_args("loopback")
        chk("实时模块下次启动会用 small", cl.get("model") == "small", str(cl.get("model")))
        cc = R.cli_args("cache")
        chk("缓存模块下次启动仍是 large-v3", cc.get("model") == "large-v3",
            str(cc.get("model")))

        print("六、set-mode：只动一个模式")
        code, r = call(base, "/api/set-mode",
                       {"mode": "cache", "mt": "local:hh-translate-base",
                        "params": {"mt_batch": 12}})
        chk("set-mode 成功", code == 200 and r.get("ok"), f"{code} {r}")
        rs = R.all_resolved()
        chk("cache.mt 改了", rs["cache"]["mt"] == "local:hh-translate-base",
            rs["cache"]["mt"])
        chk("loopback.mt 没被牵连",
            rs["loopback"]["mt"] == "remote:qwen-mt-flash", rs["loopback"]["mt"])
        chk("params 生效", R.cli_args("cache").get("mt_batch") == 12,
            str(R.cli_args("cache")))
        code, r = call(base, "/api/set-mode", {"mode": "cache", "params": {"野键": 1}})
        chk("params 不接受新增野键", code == 400, f"{code} {r}")
        code, r = call(base, "/api/set-mode", {"mode": "cache", "params": {"mt_batch": "8"}})
        chk("params 类型不符被拒（字符串冒充整数）", code == 400, f"{code} {r}")
        code, r = call(base, "/api/set-mode", {"mode": "cache", "params": {"mt_batch": 8.0}})
        chk("params 整数型接受 8.0 并归一成 int",
            code == 200 and isinstance(R.load(R.PATH)["modes"]["cache"]["params"]["mt_batch"], int),
            f"{code} {r.get('error') if isinstance(r, dict) else r}")
        code, r = call(base, "/api/set-mode", {"mode": "不存在", "asr": "x"})
        chk("未知模式被拒", code == 400, f"{code} {r}")

        print("七、null = 回到跟随默认")
        code, r = call(base, "/api/select", {"target": "modes.cache.asr", "value": None})
        chk("mode.asr 置 null 成功", code == 200 and r.get("ok"), f"{code} {r}")
        chk("置 null 后 cache 跟着 defaults（small）",
            R.all_resolved()["cache"]["asr"] == "local:faster-whisper-small",
            R.all_resolved()["cache"]["asr"])

        print("八、落盘与快照")
        saved = json.load(open(R.PATH, encoding="utf-8"))
        chk("改动落进注册表文件",
            saved["defaults"]["asr"] == "local:faster-whisper-small",
            json.dumps(saved["defaults"], ensure_ascii=False))
        chk("快照文件已重新导出", os.path.isfile(R.WEB_PATH))
        chk("快照 != 注册表（一个是派生的）", R.WEB_PATH != R.PATH)

        print("九、越界与容错")
        code, r = call(base, "/api/select", {"value": "x"})
        chk("缺 target → 400", code == 400, f"{code}")
        req = urllib.request.Request(base + "/api/select", data=b"{bad json",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as rr:
                code = rr.status
        except urllib.error.HTTPError as e:
            code = e.code
        chk("坏 JSON → 400（不是崩掉）", code == 400, str(code))
        code, r = call(base, "/不存在")
        chk("未知路径 → 404", code == 404, f"{code}")

        httpd.shutdown()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n后台 API 回归：{ok} 项通过，{bad} 项失败")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
