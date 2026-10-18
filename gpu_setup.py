# -*- coding: utf-8 -*-
"""gpu_setup.py —— CUDA 运行库加载与 GPU 探测

为什么需要这个文件
==================
faster-whisper 用 ctranslate2 推理。即使机器上有 NVIDIA 显卡、
ctranslate2 也编译了 CUDA 支持，**它仍然需要 cuBLAS / cuDNN 的 DLL**
（Windows 上叫 cublas64_12.dll、cublasLt64_12.dll 等）。

没有这些 DLL 时的报错是：

    RuntimeError: Library cublas64_12.dll is not found or cannot be loaded

**这个报错发生在真正前向计算时，不在加载模型时** —— 所以
`WhisperModel('small', device='cuda')` 能成功构造，一 transcribe 才炸。
这一点很有误导性：容易以为"GPU 能用"，或者反过来以为"没有 GPU"。

本机实测的教训（值得写下来）
---------------------------
我早先遇到这个报错，直接推断"本机没有可用 GPU"，于是把识别搬到远端，
并把"本地 CPU 有 2 秒固定成本"当成架构前提写进了 README。
**实际是 GPU 存在（RTX 4070 Laptop，8188 MiB，CC 8.9），只缺 DLL。**

正确做法：查 `nvidia-smi`，而不是从一次失败推断硬件不存在。
补齐 DLL 后 GPU 上 4 秒音频只要 0.15~0.19 秒，比 CPU 快 11 倍。

怎么补（不需要装完整 CUDA Toolkit，也不需要管理员权限）
------------------------------------------------------
    pip install nvidia-cublas-cu12 nvidia-cudnn-cu12

装完 DLL 落在 site-packages/nvidia/*/bin/。**但仅安装还不够** ——
ctranslate2 的原生代码走 LoadLibrary 搜索路径，
`os.add_dll_directory` 对它**无效**（实测），必须把目录前置到 PATH。
本模块的 enable_cuda_dlls() 做的就是这件事。
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
import threading

_CUDA_DIRS: list = []


def cuda_lib_dirs():
    """找出 site-packages/nvidia/*/bin 里的 CUDA 库目录。"""
    global _CUDA_DIRS
    if _CUDA_DIRS:
        return _CUDA_DIRS
    dirs = []
    try:
        import site
        roots = list(site.getsitepackages()) + [site.getusersitepackages()]
    except Exception:
        roots = []
    here = os.path.dirname(os.path.abspath(__file__))
    roots.append(os.path.join(os.path.dirname(here), "Lib", "site-packages"))
    for sp in roots:
        if not sp or not os.path.isdir(sp):
            continue
        for d in glob.glob(os.path.join(sp, "nvidia", "*", "bin")):
            if os.path.isdir(d) and d not in dirs:
                dirs.append(d)
    _CUDA_DIRS = dirs
    return dirs


def enable_cuda_dlls():
    """把 CUDA 库目录加进 DLL 搜索路径。返回加进去的目录列表。

    两条路都走，因为不同加载方式行为不同：
      · PATH 前置 —— ctranslate2 的原生 LoadLibrary 靠这个（实测有效）
      · os.add_dll_directory —— Python 3.8+ 的官方机制，对某些库有用
    """
    dirs = cuda_lib_dirs()
    if not dirs:
        return []
    # PATH 前置（关键）
    cur = os.environ.get("PATH", "")
    parts = cur.split(os.pathsep) if cur else []
    add = [d for d in dirs if d not in parts]
    if add:
        os.environ["PATH"] = os.pathsep.join(add + parts)
    # 官方机制（辅助）
    for d in dirs:
        try:
            os.add_dll_directory(d)
        except Exception:
            pass
    return dirs


def nvidia_smi_info():
    """读 nvidia-smi。返回 dict 或 None。"""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        r = subprocess.run(
            [exe, "--query-gpu=name,memory.total,memory.free,compute_cap",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        line = (r.stdout or "").strip().split("\n")[0]
        if not line:
            return None
        name, total, free, cc = [x.strip() for x in line.split(",")]
        return {"name": name, "total_mib": int(total),
                "free_mib": int(free), "compute_cap": cc}
    except Exception:
        return None


def _probe_once(compute_type="float16"):
    """真跑一次极短前向，确认 DLL 真能找到。抛异常=不可用。"""
    from faster_whisper import WhisperModel
    import numpy as np
    m = WhisperModel("tiny", device="cuda", compute_type=compute_type)
    segs, _ = m.transcribe(np.zeros(16000, dtype=np.float32),
                           beam_size=1, vad_filter=False,
                           condition_on_previous_text=False)
    list(segs)
    del m


def _probe_offline_ready():
    """把"别问 hub"这件事**在 import 之前**钉死。

    为什么必须在 import 前：`huggingface_hub.constants` 在**导入时**就把
    HF_HUB_OFFLINE 读进模块常量，之后再 os.environ 改它**不生效**。
    gpu_setup 的旧代码只在第一次探测失败后才设它 —— 于是第一次那个
    联网请求该挂多久还是挂多久。
    """
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


def _run_with_deadline(fn, timeout=25.0):
    """跑 fn()，超过 timeout 就放弃（**探测不许把整个程序挂住**）。

    返回值 (ok, err)。卡住时 ok=None（未知），不是 False ——
    "探不出来"和"探出来是坏的"结论不同，不该混成同一个。
    """
    box: list = []

    def _worker():
        try:
            fn()
            box.append((True, ""))
        except BaseException as e:                # noqa: BLE001
            box.append((False, f"{type(e).__name__}: {e}"))

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout)
    if not box:
        return None, f"探测超过 {timeout:.0f} 秒未返回（判定为卡死，跳过）"
    return box[0][0], box[0][1]


def gpu_available(timeout=25.0, online_timeout=8.0, verbose=False):
    """GPU 是否**真的可用于推理**（不只是存在）。

    注意：这里必须真的跑一次前向，不能只看设备列表 ——
    缺 cuBLAS 时设备在、模型能构造，只有前向才报错。

    **但探测本身绝不能把启动挂住**（2026-08 实测踩到）：本机
    huggingface.co 连不通（代理 502 那类），`WhisperModel("tiny")` 的
    联网请求没有超时，于是 `gpu_available()` **永久卡住** ——
    而它是在建浮窗之前调的，所以现象是"启动后什么都没有、也没有报错"，
    操作者完全看不出卡在哪。见 README「踩过的坑」。

    现在的顺序，**总耗时上限 ≈ timeout + online_timeout**：
      ① 先按**离线**探一次（模型已在缓存里就该走这条）
      ② 离线不成再试一次联网（网络请求可能没有超时，所以死线更短：
         8 秒。拔网线/代理挂掉时不该等 25 秒才发现连不上）
      ③ 两次都失败/超时 → False，并打印原因
    """
    info = nvidia_smi_info()
    if info is None:
        return False
    enable_cuda_dlls()
    try:
        import ctranslate2
        types = ctranslate2.get_supported_compute_types("cuda")
        if not types:
            return False
    except Exception:
        return False

    prev = {k: os.environ.get(k) for k in ("HF_HUB_OFFLINE",)}
    _probe_offline_ready()                       # 必须在 import 之前
    try:
        ok, err = _run_with_deadline(_probe_once, timeout)
        if ok:
            return True
        if verbose and err:
            print(f"    · 离线探测未通过：{err[:160]}")
        # 离线不成 → 放行联网再试（仍带死线）。有些机器没缓存 tiny，
        # 但能正常联网，这种不该被误判成 GPU 坏。
        if prev.get("HF_HUB_OFFLINE") is None:
            os.environ.pop("HF_HUB_OFFLINE", None)
        os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "5")
        os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "5")
        ok2, err2 = _run_with_deadline(_probe_once, online_timeout)
        if ok2:
            return True
        if verbose:
            print(f"    · 联网探测未通过：{(err2 or err)[:160]}")
        return False
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def describe():
    """给人看的一行状态。"""
    info = nvidia_smi_info()
    if info is None:
        return "GPU: 没有 nvidia-smi"
    dirs = cuda_lib_dirs()
    ok = gpu_available()
    return (f"GPU: {info['name']}　{info['total_mib']} MiB"
            f"（空闲 {info['free_mib']}）　CC {info['compute_cap']}"
            f"　CUDA 库 {len(dirs)} 个目录"
            f"　推理可用: {'是' if ok else '否'}")


if __name__ == "__main__":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    print(f"  {describe()}")
    for d in cuda_lib_dirs():
        print(f"    {d}")
    if not cuda_lib_dirs():
        print("    没找到 CUDA 库。装：pip install nvidia-cublas-cu12 "
              "nvidia-cudnn-cu12")
