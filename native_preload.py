# -*- coding: utf-8 -*-
"""native_preload.py —— 在加载 Qt 之前把 torch 的原生 DLL 先载入进程。

为什么需要它（本机实测，2026-10-18）
====================================
torch 2.13.0+cpu 的 `torch/lib/c10.dll` 如果**在 PyQt5 之后**才加载，
会抛：

    OSError: [WinError 1114] 动态链接库(DLL)初始化例程失败。
    Error loading ".../torch/lib/c10.dll" or one of its dependencies.

而先 import torch 再 import PyQt5 就没事。更麻烦的是：faster-whisper 的
依赖链 ctranslate2 → ctranslate2.specs.model_spec 在 **import 时**
`import torch`，所以这不只影响 device_placement 的设备枚举，而是整条
识别链起不来。

已验证的修法：在第一次 `import PyQt5.*` **之前**，用 ctypes 预载
`torch/lib/c10.dll`（`os.add_dll_directory` 同时加上 torch/lib）；
之后 Qt 再加载、torch 再 import 都能成功。没有 torch 时本函数是 no-op。
"""
from __future__ import annotations

import os
import sys

_DONE = False


def preload_torch_dlls() -> bool:
    """预载 torch 的 c10.dll。返回是否真的预载了。幂等、绝不抛异常。"""
    global _DONE
    if _DONE:
        return False
    _DONE = True
    if os.name != "nt":
        return False
    try:
        import importlib.util
        spec = importlib.util.find_spec("torch")
        if spec is None or not spec.origin:
            return False
        libdir = os.path.join(os.path.dirname(spec.origin), "lib")
        c10 = os.path.join(libdir, "c10.dll")
        if not os.path.isfile(c10):
            return False
        if hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(libdir)
            except OSError:
                pass
        import ctypes
        ctypes.WinDLL(c10)          # 成功即说明依赖链都能初始化
        return True
    except Exception:
        # 预载失败就回到"没做"状态：device_placement 里还有 try/except 兜底
        return False
