# -*- coding: utf-8 -*-
"""check_model_home.py —— 冒烟检查：模型是否都从 D 盘安装/解析。

用法：
    python tools/check_model_home.py            # 只查路径与识别
    python tools/check_model_home.py --load-tiny  # 额外真实加载 tiny（CPU，秒级）

退出码 0 = 全部通过；非 0 = 有断言失败（打印原因）。
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import model_home  # noqa: E402,F401

FAILS: list[str] = []


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {label}" + (f"  — {detail}" if detail else ""))
    if not cond:
        FAILS.append(label)


def main() -> int:
    home = model_home.model_home()
    hub = model_home.hf_hub_dir()
    ok("model_home 在 D 盘", home.lower().startswith("d:"), home)
    ok("HF hub 在 D 盘", hub.lower().startswith("d:"), hub)
    ok("HF hub 目录存在", os.path.isdir(hub), hub)

    # C 盘入口必须是 junction，真实落在 D 盘
    for name in ("huggingface", "whisper"):
        link = pathlib.Path(os.path.expanduser(f"~/.cache/{name}"))
        real = pathlib.Path(os.path.realpath(link))
        ok(f"C:\\...\\{name} 实际落在 D 盘", str(real).lower().startswith("d:"), str(real))

    # faster-whisper 五个档位都能被项目识别，且路径在 D 盘
    from model_manager import whisper_installed  # noqa: E402

    for size in ("tiny", "base", "small", "medium", "large-v3"):
        p = whisper_installed(size)
        ok(f"whisper {size} 已识别且在 D 盘", bool(p) and p.lower().startswith("d:"), str(p))

    # 模型大小读数（设备放置/显存预算用）应接近实际缓存
    from get_models import model_dir_mb  # noqa: E402

    expect = {"tiny": (60, 120), "base": (120, 220), "small": (400, 600),
              "medium": (1300, 1700), "large-v3": (2700, 3300)}
    for size, (lo, hi) in expect.items():
        mb = model_dir_mb(size)
        ok(f"model_dir_mb({size}) 在 {lo}~{hi}MB", lo <= mb <= hi, f"{mb:.1f}MB")

    # 项目缓存判断函数（live/pretranslate 各一份）必须认 D 盘缓存
    import pretranslate  # noqa: E402
    import live_subtitle_v2  # noqa: E402

    for mod in (pretranslate, live_subtitle_v2):
        fn = getattr(mod, "_hf_model_cached", None)
        if fn is None:
            ok(f"{mod.__name__}._hf_model_cached 可调用", False, "函数不存在")
            continue
        ok(f"{mod.__name__}._hf_model_cached('medium')", bool(fn("medium")))

    if args.load_tiny:
        from faster_whisper import WhisperModel  # noqa: E402

        m = WhisperModel("tiny", device="cpu", compute_type="int8", local_files_only=True)
        ok("tiny 模型可离线加载", type(m).__name__ == "WhisperModel", type(m).__name__)

    print()
    if FAILS:
        print(f"失败 {len(FAILS)} 项：" + "；".join(FAILS))
        return 1
    print("OK: 模型全部从 D 盘解析")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--load-tiny", action="store_true", help="额外真实加载 tiny 模型")
    args = ap.parse_args()
    sys.exit(main())
