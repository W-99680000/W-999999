# -*- coding: utf-8 -*-
"""tools/test_model_search.py —— 模型搜索范围（别名/中文/厂商/用途）。"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import model_manager as M  # noqa: E402

n_ok = n_bad = 0


def chk(name, cond, detail=""):
    global n_ok, n_bad
    if cond:
        n_ok += 1
        print("  ok   " + name)
    else:
        n_bad += 1
        print("  FAIL " + name + "  " + str(detail))


def ids(q):
    return {m["id"] for m in M.search(q)}


def main():
    print("=" * 56)
    print("模型搜索：中文/厂商/用途/档位 别名")
    ck = {
        "腾讯（厂商）": ("local:hh-translate-base" in ids("腾讯")),
        "混元（系列）": ("local:hh-translate-large" in ids("混元")),
        "7B（档位）": ("local:hh-translate-large" in ids("7B")),
        "语音识别（用途）": ("local:faster-whisper-base" in ids("语音识别")),
        "阿里云（厂商）": ("remote:qwen-mt-flash" in ids("阿里云")),
        "在线（部署）": ("remote:qwen3-asr-flash" in ids("在线")),
        "离线（部署）": ("local:hh-translate-base" in ids("离线")),
        "英文（语种）": ("local:faster-whisper-base" in ids("英文")),
    }
    for name, ok in ck.items():
        chk(name, ok, sorted(ids(name.split("（")[0]))[:3])
    # 空搜索=全量；分组过滤仍生效
    alln = len(M.search(""))
    chk("空搜索返回全部目录", alln >= 12, alln)
    chk("分组过滤 asr", all(
        m["group"] == "asr" for m in M.search("", group="asr")))
    print(f"\n合计 {n_ok} 通过 / {n_bad} 失败")
    return 0 if n_bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
