#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/doctor.py —— 一条命令体检"现在到底能不能跑、哪里坏了、怎么修"

为什么需要
==========
这个项目依赖的东西分散在四个层面，任何一层漂移都会表现为"没字幕/没浮窗"，
而现象几乎一样、很难区分：

  1. **解释器**：机器上有多个 Python，装的东西互补（见 babel_admin/dlls.py）
  2. **CUDA DLL**：cuBLAS/cuDNN 不在 site-packages 里就是在别处；缺了
     `WhisperModel(device="cuda")` **构造会成功、transcribe 才炸**（最误导）
  3. **模型缓存**：HuggingFace 缓存被清/换用户 → 启动时联网卡住
  4. **运行期产物**：端口、状态 JSON、字幕文件

实测事故（本项目自己踩过的，README 也记了）：
  · DSH 运行时升级后依赖被换掉 → 同一份代码昨天 GPU、今天 CPU（本文件就是
    为这种"环境漂移"准备的）。
  · `dlls.py` 只检查 5 个模块，`winsdk`/`yt_dlp` 缺失照样"通过"，
    到 watch/网页视频路径才炸。

用法
----
    python tools/doctor.py            # 人或 CI 看
    python tools/doctor.py --json     # 机器读
    python tools/doctor.py --out work/doctor.txt

退出码：0 = 没有 FAIL（WARN 不算失败）；1 = 至少一个 FAIL。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# 运行时真正会用到的第三方模块（按功能分组，便于报错说清"哪个功能没了"）
GROUPS = {
    "核心(引擎)": ["numpy", "faster_whisper", "ctranslate2",
                   "PyQt5", "pyaudiowpatch"],
    "翻译": ["argostranslate"],
    "watch/网页视频": ["winsdk", "yt_dlp", "av"],
    "桌面壳(app.py)": ["webview", "pystray", "PIL"],
}
NEED_DLLS = ("cublas64_12.dll", "cudnn64_9.dll")

PROBE = r'''
import glob, importlib.util as iu, json, os, sys
groups = json.loads(%r)
dlls = json.loads(%r)
miss = {}
for g, mods in groups.items():
    miss[g] = [m for m in mods if iu.find_spec(m) is None]
sp = os.path.join(sys.prefix, "Lib", "site-packages")
found = {}
for d in dlls:
    hits = glob.glob(os.path.join(sp, "nvidia", "*", "bin", d))
    hits += glob.glob(os.path.join(sp, "nvidia", "*", d))
    found[d] = hits[0] if hits else ""
print(json.dumps({"exe": sys.executable, "py": sys.version.split()[0],
                  "miss": miss, "dll": found, "sp": sp}))
'''


def candidates() -> list[str]:
    try:
        sys.path.insert(0, os.path.join(ROOT, "babel_admin"))
        import dlls  # type: ignore
        return dlls.candidates()
    except Exception:
        home = os.path.expanduser("~")
        return [
            os.environ.get("BABEL_PYTHON", ""),
            os.path.join(home, ".dsh", "dsh-runtimes", "dsh-primary-runtime",
                         "dependencies", "python", "python.exe"),
            os.path.join(home, "AppData", "Local", "Programs", "Python",
                         "Python312", "python.exe"),
            sys.executable,
        ]


def probe(py: str, timeout: float = 90.0) -> dict:
    info = {"exe": py, "ok": False, "miss": {}, "dll": {}, "error": ""}
    if not py or not os.path.isfile(py):
        info["error"] = "不存在"
        return info
    script = PROBE % (json.dumps(GROUPS, ensure_ascii=False),
                      json.dumps(list(NEED_DLLS)))
    try:
        r = subprocess.run([py, "-X", "utf8", "-c", script],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           timeout=timeout)
        if r.returncode != 0:
            info["error"] = (r.stderr or "").strip()[-300:]
            return info
        d = json.loads((r.stdout or "").strip().splitlines()[-1])
        info.update(d)
        core_ok = not d["miss"].get("核心(引擎)")
        info["ok"] = core_ok and all(d["dll"].values())
        info["core_ok"] = core_ok
    except subprocess.TimeoutExpired:
        info["error"] = f"探测超时（>{timeout:.0f}s）"
    except Exception as e:
        info["error"] = f"{type(e).__name__}: {e}"
    return info


def check_host() -> dict:
    out = {"nvidia_smi": None, "hf_models": [], "hf_mb": 0.0,
           "port_8770": None, "free_gb": None, "registry_ok": None,
           "json_bad": []}
    # GPU 硬件
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15)
        line = (r.stdout or "").strip().splitlines()
        if line:
            out["nvidia_smi"] = [x.strip() for x in line[0].split(",")]
    except Exception:
        pass
    # HF faster-whisper 模型缓存
    hub = os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub")
    if os.path.isdir(hub):
        size = 0
        for d in os.listdir(hub):
            if "faster-whisper" not in d.lower():
                continue
            out["hf_models"].append(d.replace("models--", "")
                                    .replace("--", "/"))
            for root, _ds, fs in os.walk(os.path.join(hub, d)):
                for f in fs:
                    try:
                        size += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
        out["hf_models"].sort()
        out["hf_mb"] = round(size / 1e6, 1)
    # 端口（管理端/桌面壳）
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.4)
    try:
        out["port_8770"] = (s.connect_ex(("127.0.0.1", 8770)) == 0)
    finally:
        s.close()
    # 磁盘
    try:
        import shutil
        out["free_gb"] = round(shutil.disk_usage(ROOT).free / 1e9, 1)
    except Exception:
        pass
    # 关键 JSON 可解析
    for name in ("model_registry.json", "overlay_qt.json",
                 "device_pref.json", "control_cmd.json",
                 "overlay_state.json", "watch_overlay.json"):
        p = os.path.join(ROOT, name)
        if not os.path.exists(p):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                json.load(f)
        except Exception as e:
            out["json_bad"].append(f"{name}: {type(e).__name__}")
    try:
        p = os.path.join(ROOT, "model_registry.json")
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        out["registry_ok"] = bool(d.get("defaults") and d.get("modes"))
    except Exception:
        out["registry_ok"] = False
    return out


def fmt(info: dict) -> list[str]:
    lines = []
    exe = info.get("exe", "?")
    tag = "OK   " if info.get("ok") else ("CORE " if info.get("core_ok")
                                          else "FAIL ")
    lines.append(f"  {tag}{exe}")
    lines.append(f"        python {info.get('py', '?')}   "
                 f"site-packages {info.get('sp', '?')}")
    if info.get("error"):
        lines.append(f"        错误: {info['error']}")
    for g, mods in (info.get("miss") or {}).items():
        if mods:
            lines.append(f"        缺[{g}]: {', '.join(mods)}")
    for d, p in (info.get("dll") or {}).items():
        lines.append(f"        {d}: {'有 → ' + p if p else '**缺**'}")
    return lines


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    t0 = time.time()
    infos = [probe(p) for p in candidates()]
    host = check_host()

    # 选解释器：先"核心齐 + DLL 齐"，再"核心齐"，最后当前解释器
    def rank(i):
        return (0 if i.get("ok") else (1 if i.get("core_ok") else 2))
    best = sorted(infos, key=rank)[0] if infos else None

    fails, warns = [], []
    if not best or not best.get("ok"):
        fails.append("没有找到『核心模块 + CUDA DLL』都齐的解释器："
                     "GPU 链路不可用（会退回 CPU/远端）")
    if best and best.get("core_ok"):
        missing = [m for g, ms in best.get("miss", {}).items()
                   if g in ("watch/网页视频", "桌面壳(app.py)") for m in ms]
        if missing:
            warns.append("当前最佳解释器缺功能模块: " + ", ".join(missing) +
                         " → watch/网页视频/桌面壳会降级")
    if not host["nvidia_smi"]:
        warns.append("nvidia-smi 不可用（无 NVIDIA 驱动/显卡）")
    if not host["hf_models"]:
        warns.append("HuggingFace 里没有 faster-whisper 模型缓存 → "
                     "首次启动会联网下载（可能很慢）")
    if host["port_8770"] is False:
        warns.append("管理端端口 8770 未监听（正常：只有 app.py/admin 启动后才有）")
    if host["json_bad"]:
        fails.append("关键 JSON 损坏: " + "; ".join(host["json_bad"]))
    if not host["registry_ok"]:
        fails.append("model_registry.json 缺失或结构不对（defaults/modes）")
    if host["free_gb"] is not None and host["free_gb"] < 5:
        warns.append(f"磁盘余量仅 {host['free_gb']} GB（模型缓存/预翻译需要空间）")

    repair = []
    if fails:
        repair += [
            "GPU 链路（如需要）:",
            f"  \"{best['exe'] if best else '<python>'}\" -m pip install "
            "nvidia-cublas-cu12 nvidia-cudnn-cu12",
            "  （装完 DLL 在 site-packages/nvidia/*/bin，"
            "gpu_setup.enable_cuda_dlls() 会前置 PATH）",
            "watch/网页视频功能:",
            f"  \"{best['exe'] if best else '<python>'}\" -m pip install "
            "winsdk yt-dlp",
            "",
        ]

    if a.json:
        print(json.dumps({"interpreter": best, "all": infos, "host": host,
                          "fails": fails, "warns": warns,
                          "repair": repair, "seconds": round(time.time() - t0, 1)},
                         ensure_ascii=False, indent=2))
    else:
        print("=" * 72)
        print("live-subtitle doctor")
        print("=" * 72)
        print("\n[解释器候选]")
        for i in infos:
            print("\n".join(fmt(i)))
        print("\n[主机]")
        if host["nvidia_smi"]:
            print(f"  GPU: {host['nvidia_smi'][0]}  "
                  f"{host['nvidia_smi'][1]} MiB  driver {host['nvidia_smi'][2]}")
        else:
            print("  GPU: nvidia-smi 不可用")
        print(f"  faster-whisper 缓存: {len(host['hf_models'])} 个模型, "
              f"{host['hf_mb']} MB  {host['hf_models']}")
        print(f"  端口 8770: {host['port_8770']}   "
              f"磁盘剩余 {host['free_gb']} GB")
        print(f"  model_registry.json: {'OK' if host['registry_ok'] else 'BAD'}")
        print("\n[判定]")
        for w in warns:
            print(f"  WARN {w}")
        for f in fails:
            print(f"  FAIL {f}")
        if not fails and not warns:
            print("  PASS 环境完整")
        if repair:
            print("\n[修复命令]")
            print("\n".join(repair))
        print(f"\n耗时 {time.time() - t0:.1f}s")
        print("退出码:", 1 if fails else 0)

    if a.out:
        try:
            os.makedirs(os.path.dirname(os.path.join(ROOT, a.out)), exist_ok=True)
        except Exception:
            pass
        # 把可读结果落盘（work/ 已在 .gitignore）
        with open(os.path.join(ROOT, a.out), "w", encoding="utf-8") as f:
            f.write(json.dumps({"interpreter": best, "host": host,
                                "fails": fails, "warns": warns,
                                "repair": repair}, ensure_ascii=False, indent=2))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
