# -*- coding: utf-8 -*-
"""tools/check_qwen_terms.py —— 实测 Qwen-MT 术语注入是否生效（A/B）。

用法：python tools/check_qwen_terms.py
需要 DASHSCOPE_API_KEY / .dsh 凭据；没有就跳过（退出码 0）。
只发 2~4 条短句，费用可忽略。
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from llm_translate import from_env_or_args  # noqa: E402

TERMS = [("Bilibili", "哔哩哔哩"), ("up主", "创作者")]
CASES = [
    ("I watched the video on Bilibili yesterday.", "Bilibili"),
    ("The up主 uploaded a new video.", "up主"),
]


def main() -> int:
    tr = from_env_or_args()
    if tr is None:
        print("跳过：没有可用的 LLM 端点（DASHSCOPE_API_KEY / LLM_*）")
        return 0
    if not tr._is_qwen_mt():
        print(f"跳过：当前端点不是 qwen-mt（{tr.model}）")
        return 0
    print(f"端点：{tr.model}")
    print("\n--- 无术语 ---")
    base = {}
    for text, _term in CASES:
        zh = tr(text, target="Simplified Chinese", source="English")
        base[text] = zh
        print(f"  原: {text}\n  译: {zh or '（失败）'}")
    print(f"\n--- 带术语 {TERMS} ---")
    tr.set_terms(TERMS)
    ok_terms = 0
    for text, term in CASES:
        zh = tr(text, target="Simplified Chinese", source="English")
        print(f"  原: {text}\n  译: {zh or '（失败）'}")
        if term == "Bilibili" and ("哔哩哔哩" in zh or "B站" in zh):
            ok_terms += 1
        if term == "up主" and "创作者" in zh:
            ok_terms += 1
    print(f"\nstatus: {tr.status()}")
    if tr.option_fails:
        print("！服务端拒绝了 translation_options，已自动降级 —— 检查字段名")
        return 1
    print(f"术语命中 {ok_terms}/{len(CASES)}；"
          f"translation_options 被接受 {tr.options_ok} 次")
    return 0 if ok_terms else 2


if __name__ == "__main__":
    raise SystemExit(main())
