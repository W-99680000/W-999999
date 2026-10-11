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
            capture_output=True, text=True, timeout=10)
        line = (r.stdout or "").strip().split("\n")[0]
        if not line:
            return None
        name, total, free, cc = [x.strip() for x in line.split(",")]
        return {"name": name, "total_mib": int(total),
                "free_mib": int(free), "compute_cap": cc}
    except Exception:
        return None


def gpu_available():
    """GPU 是否**真的可用于推理**（不只是存在）。

    注意：这里必须真的跑一次前向，不能只看设备列表 ——
    缺 cuBLAS 时设备在、模型能构造，只有前向才报错。
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
    # 真跑一次极短前向，确认 DLL 真能找到
    try:
        from faster_whisper import WhisperModel
        import numpy as np
        m = WhisperModel("tiny", device="cuda", compute_type="float16")
        segs, _ = m.transcribe(np.zeros(16000, dtype=np.float32),
                               beam_size=1, vad_filter=False,
                               condition_on_previous_text=False)
        list(segs)
        del m
        return True
    except Exception:
        return False


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
