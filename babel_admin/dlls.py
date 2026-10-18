# -*- coding: utf-8 -*-
"""babel_admin/dlls.py —— 挑出"真正能跑 GPU"的那个 Python

为什么需要单独一层
==================
这台机器上有**两个 Python 3.12**，依赖装得不完整且互补：

    C:\\Users\\w\\AppData\\Local\\Programs\\Python\\Python312      ← PATH 里的 python
        faster_whisper / ctranslate2 / PyQt5 / pyaudiowpatch 都在
        **但没有 nvidia 包** → 没有 cublas64_12.dll
    %USERPROFILE%\\.dsh\\dsh-runtimes\\dsh-primary-runtime\\...\\python.exe
        **有 nvidia/cublas/bin/cublas64_12.dll（98MB）**

后果实测（2026-08）：
  · 用系统 Python 起链路 → `gpu_available()` 报
    `Library cublas64_12.dll is not found or cannot be loaded`，
    然后联网重试又没有超时 → **启动卡 35 秒以上 / 永久卡住**，
    而浮窗是在这之后才创建的 → 现象是"启动后什么都没有、也没有报错"。
  · 换成 DSH 运行时那个 Python → `gpu_available()` **5.9 秒返回 True**。

所以"用哪个 python"不是口味问题，是**能不能出字幕**的问题。
这个判断必须由**代码**做（而不是只写在 .bat 里）：管理端是从界面
点按钮起引擎的，那条路径根本不经过 .bat。

判据不是"哪个 python 存在"，而是"**哪个 python 真的能加载 cuBLAS**" ——
我在 .bat 里第一版就是按存在性选的，结果选到了缺库的那个。
"""
from __future__ import annotations

import glob
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

#: 需要 CUDA 运行库的模块（缺 cublas 时 ctranslate2 能 import 但前向会炸，
#: 所以这里只列"必须存在"的模块，CUDA 库另行检查）
NEED_MODULES = ("faster_whisper", "ctranslate2", "PyQt5", "pyaudiowpatch",
                "numpy")
#: 前向真正要加载的 DLL（名字与 nvidia-cublas-cu12 / -cudnn-cu12 一致）
NEED_DLLS = ("cublas64_12.dll",)

#: 探测结果的进程内缓存（避免每点一次按钮就 subprocess 一遍）
_CACHE: dict[str, dict] = {}


def _check_script() -> str:
    """在候选解释器里跑的一小段自检（只读，不改任何东西）。"""
    mods = ", ".join(repr(m) for m in NEED_MODULES)
    dlls = ", ".join(repr(d) for d in NEED_DLLS)
    return (
        "import glob, importlib.util as iu, json, os, sys\n"
        f"mods = [{mods}]\n"
        f"dlls = [{dlls}]\n"
        "miss_m = [m for m in mods if iu.find_spec(m) is None]\n"
        "sp = os.path.join(sys.prefix, 'Lib', 'site-packages')\n"
        "found = {}\n"
        "for d in dlls:\n"
        "    hits = glob.glob(os.path.join(sp, 'nvidia', '*', 'bin', d))\n"
        "    hits += glob.glob(os.path.join(sp, 'nvidia', '*', d))\n"
        "    found[d] = hits[0] if hits else ''\n"
        "print(json.dumps({'exe': sys.executable, 'py': sys.version.split()[0],\n"
        "                  'miss': miss_m, 'dll': found}))\n")


def candidates() -> list[str]:
    """候选解释器，按"最可能对"排序。"""
    out: list[str] = []
    env = os.environ.get("BABEL_PYTHON")
    if env:
        out.append(env)
    home = os.path.expanduser("~")
    out += [
        os.path.join(home, ".dsh", "dsh-runtimes", "dsh-primary-runtime",
                     "dependencies", "python", "python.exe"),
        os.path.join(home, "AppData", "Local", "Programs", "Python", "Python312",
                     "python.exe"),
        sys.executable,
    ]
    seen, uniq = set(), []
    for p in out:
        if p and p not in seen and os.path.isfile(p):
            seen.add(p)
            uniq.append(p)
    return uniq


def describe(py: str, use_cache: bool = True) -> dict:
    """这个解释器有没有跑 GPU 链路要的东西。"""
    if use_cache and py in _CACHE:
        return _CACHE[py]
    info: dict = {"exe": py, "ok": False, "miss": [], "dll": {}, "error": ""}
    try:
        r = subprocess.run([py, "-c", _check_script()], capture_output=True,
                           text=True, timeout=60,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode != 0:
            info["error"] = (r.stderr or "").strip()[-200:]
        else:
            import json
            d = json.loads((r.stdout or "").strip().splitlines()[-1])
            info.update(d)
            info["ok"] = (not d.get("miss")) and all(d.get("dll", {}).values())
    except Exception as e:
        info["error"] = f"{type(e).__name__}: {e}"
    if use_cache:
        _CACHE[py] = info
    return info


def pick(use_cache: bool = True) -> dict:
    """挑一个能用的。返回 describe() 的 dict，外加 `reason`。

    排序规则（**先看 CUDA 库，再看模块**）：
      ① 两个都齐 → 直接用它
      ② 只有模块齐、缺 CUDA → 记为备选（能跑，但识别会退 CPU/远端）
      ③ 都不行 → 回退 sys.executable 并在 reason 里说明
    绝不在"没有可用解释器"时抛异常 —— 那样连界面都起不来。
    """
    fallback = None
    for py in candidates():
        info = describe(py, use_cache)
        if info["ok"]:
            info = dict(info)
            info["reason"] = "模块与 CUDA 库都齐"
            return info
        if not info.get("miss") and fallback is None:
            fallback = dict(info)
            fallback["reason"] = ("模块齐但缺 CUDA 库（%s）→ 识别会退 CPU/远端；"
                                  "补：pip install nvidia-cublas-cu12 "
                                  "nvidia-cudnn-cu12"
                                  % ", ".join(k for k, v in info["dll"].items()
                                              if not v))
    if fallback:
        return fallback
    info = dict(describe(sys.executable, use_cache))
    info["reason"] = "没有找到含全部依赖的解释器，回退当前进程的解释器"
    return info


def env_for_engine() -> dict:
    """起引擎时的环境变量。

    这些值原本只写在 .bat 里（start.bat / panel.bat），而管理端是从
    界面按钮起进程的 —— **不走 .bat**，所以必须在代码里给全。
    缺 HF_HUB_OFFLINE 的后果实测过：模型明明在缓存里，却先去问
    huggingface.co（本机不通），启动能被拖住几十秒。
    """
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("HF_HUB_OFFLINE", "1")
    env.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    env.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    return env


if __name__ == "__main__":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    print("候选解释器：")
    for py in candidates():
        i = describe(py, use_cache=False)
        flag = "OK  " if i["ok"] else "×   "
        print(f"  {flag}{py}")
        print(f"        python {i.get('py')}  缺模块 {i.get('miss') or '无'}")
        for d, p in (i.get("dll") or {}).items():
            print(f"        {d}: {'有 → ' + p if p else '**缺**'}")
        if i.get("error"):
            print(f"        错误: {i['error'][:160]}")
    p = pick(use_cache=False)
    print(f"\n选中的：{p['exe']}（{p['reason']}）")
