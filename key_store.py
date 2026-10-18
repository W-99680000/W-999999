# -*- coding: utf-8 -*-
"""key_store.py —— 本机 API 密钥集中管理（不写进仓库）。

用途：
  · DashScope（阿里云百炼）：远端识别 qwen3-asr-flash / 远端翻译 qwen-mt
  · HuggingFace Access Token：下载 gated / 受限模型（普通模型不需要）

存放位置：%LOCALAPPDATA%\\BabelPlan\\key_store.json
（只在当前 Windows 用户目录，不进 Git；后台设置页可填写/清除）

为什么需要它：原来 key 只能手写 ~/.dsh/.dashscope 或设环境变量，
普通用户在界面上没地方填 —— 选远端模型只能看到"没有 API key"。
"""
from __future__ import annotations

import json
import os
import threading

_DIR = os.path.join(os.environ.get("LOCALAPPDATA")
                    or os.path.expanduser("~"), "BabelPlan")
PATH = os.path.join(_DIR, "key_store.json")
_lock = threading.RLock()

DEFAULTS = {"dashscope": "", "hf_token": ""}


def load() -> dict:
    try:
        with open(PATH, encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict):
            d = {}
    except Exception:
        d = {}
    out = dict(DEFAULTS)
    out.update({k: str(v or "") for k, v in d.items()
                if k in DEFAULTS})
    return out


def get(name: str) -> str:
    return load().get(name, "")


def update(**kw) -> dict:
    """只改传入的字段；空字符串=清除。"""
    with _lock:
        d = load()
        for k, v in kw.items():
            if k in DEFAULTS:
                d[k] = str(v or "").strip()
        try:
            os.makedirs(_DIR, exist_ok=True)
            tmp = PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=1)
            os.replace(tmp, PATH)
        except OSError:
            pass
        apply_env(d)
        return d


def apply_env(d: dict | None = None) -> None:
    """把密钥同步到当前进程环境（子进程/引擎会继承这些变量）。"""
    d = d or load()
    ds = (d.get("dashscope") or "").strip()
    hf = (d.get("hf_token") or "").strip()
    if ds:
        os.environ["DASHSCOPE_API_KEY"] = ds
    if hf:
        os.environ["HF_TOKEN"] = hf
        os.environ["HUGGING_FACE_HUB_TOKEN"] = hf


def mask(v: str) -> str:
    v = (v or "").strip()
    if not v:
        return ""
    if len(v) <= 8:
        return "已配置"
    return f"{v[:4]}…{v[-4:]}"


LEGACY_DASHSCOPE = os.path.join(os.path.expanduser("~"), ".dsh",
                                ".dashscope")


def legacy_dashscope() -> str:
    """老位置 ~/.dsh/.dashscope 的第一条非注释行（兼容已配置用户）。"""
    try:
        with open(LEGACY_DASHSCOPE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    return line
    except Exception:
        pass
    return ""


def dashscope_available() -> bool:
    return bool((load()["dashscope"] or "").strip() or legacy_dashscope())


def status() -> dict:
    d = load()
    app_ds = (d["dashscope"] or "").strip()
    legacy = legacy_dashscope()
    ds = app_ds or legacy
    return {
        "dashscope_set": bool(ds),
        "dashscope_hint": mask(ds),
        "dashscope_source": ("app" if app_ds
                             else ("legacy" if legacy else "")),
        "hf_token_set": bool(d["hf_token"]),
        "hf_token_hint": mask(d["hf_token"]),
        "path": PATH,
        "legacy_path": LEGACY_DASHSCOPE,
    }


# import 时同步一次，保证后续直接用 env 的模块（huggingface_hub 等）能拿到
apply_env()
