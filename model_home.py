# -*- coding: utf-8 -*-
"""model_home.py —— 统一的模型安装目录（不再往 C 盘写模型）

为什么有这个文件
================
faster-whisper / huggingface_hub 默认把模型缓存到 ``~/.cache/huggingface``
（即 C 盘用户目录）。本机 C 盘空间紧张，模型必须统一落到项目所在的 D 盘。

本模块把目录规则收在一处：

    <项目>/models/
        ├── hf_home/            ← HF_HOME（faster-whisper 的 hub 缓存）
        │   └── hub/
        ├── whisper_cache/       ← 旧版 openai-whisper 的 .pt（历史遗留，不再使用）
        ├── Qwen3-ASR-*/         ← Qwen3-ASR 本地权重
        └── *.gguf               ← HY-MT / 翻译 GGUF

导入本模块会**设置环境变量**（只在未显式设置时）：
    HF_HOME、HUGGINGFACE_HUB_CACHE、HF_HUB_CACHE

注意：huggingface_hub 在 import 时读取这些变量，所以本模块必须在
``import faster_whisper`` / ``import huggingface_hub`` 之前导入。
项目里由 model_manager.py、live_subtitle_v2.py、pretranslate.py 等入口
在文件顶部 ``import model_home``，启动脚本（*.bat）也会设同样的变量；
直接 ``python xxx.py`` 时靠前者生效。

可用 ``LIVE_SUBTITLE_MODEL_HOME`` 覆盖整个模型根目录（默认 <项目>/models）。
"""
from __future__ import annotations

import os

HERE = os.path.dirname(os.path.abspath(__file__))

#: 模型根目录（可用环境变量覆盖，默认项目 models/）
DEFAULT_MODEL_HOME = os.path.join(HERE, "models")

#: 旧版 openai-whisper 的 .pt 归档目录（本项目已不用，仅保留/迁移用）
WHISPER_CACHE = os.path.join(DEFAULT_MODEL_HOME, "whisper_cache")


def model_home() -> str:
    """模型根目录：LIVE_SUBTITLE_MODEL_HOME 优先，否则 <项目>/models。"""
    env = os.environ.get("LIVE_SUBTITLE_MODEL_HOME")
    if env:
        return os.path.abspath(env)
    return DEFAULT_MODEL_HOME


def hf_home() -> str:
    """HF_HOME：显式环境变量优先，否则 <模型根>/hf_home。"""
    env = os.environ.get("HF_HOME")
    if env:
        return os.path.abspath(env)
    return os.path.join(model_home(), "hf_home")


def hf_hub_dir() -> str:
    """HF hub 缓存目录：HUGGINGFACE_HUB_CACHE/HF_HUB_CACHE 优先，否则 <HF_HOME>/hub。"""
    for key in ("HUGGINGFACE_HUB_CACHE", "HF_HUB_CACHE"):
        env = os.environ.get(key)
        if env:
            return os.path.abspath(env)
    return os.path.join(hf_home(), "hub")


def apply() -> dict:
    """把默认目录写进环境变量（不覆盖用户显式设置），返回生效值。

    必须在 import faster_whisper / huggingface_hub 之前调用。
    """
    home = model_home()
    hub_home = hf_home()
    hub = os.path.join(hub_home, "hub")
    os.makedirs(hub_home, exist_ok=True)
    os.environ.setdefault("HF_HOME", hub_home)
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", hub)
    os.environ.setdefault("HF_HUB_CACHE", hub)
    # 不再设 TRANSFORMERS_CACHE：transformers 会为此打弃用警告，HF_HOME 已覆盖。
    os.makedirs(WHISPER_CACHE, exist_ok=True)
    return {
        "model_home": home,
        "hf_home": os.environ["HF_HOME"],
        "hf_hub": os.environ["HUGGINGFACE_HUB_CACHE"],
        "whisper_cache": WHISPER_CACHE,
    }


#: 导入即生效：所有 ``import model_home`` 的入口都会先拿到 D 盘路径。
APPLIED = apply()

if __name__ == "__main__":
    for key, value in APPLIED.items():
        print(f"{key:14s} {value}")
