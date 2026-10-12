# -*- coding: utf-8 -*-
"""_test_keepalive_terms.py —— 验证长连接与 qwen-mt 术语注入

回答两个问题：
    ① keep-alive 后续调用是否比首呼快（省掉 TCP+TLS 握手）
    ② translation_options 的 terms 格式是否被 qwen-mt 接受、且真生效

用法：python tools/_test_keepalive_terms.py（需要 ~/.dsh/.dashscope）
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from llm_translate import (LLMTranslator, DASHSCOPE_BASE, DASHSCOPE_MODEL,
                           _read_dashscope_key)

key = os.environ.get("DASHSCOPE_API_KEY") or _read_dashscope_key()
if not key:
    print("没有 key，跳过")
    sys.exit(0)

tr = LLMTranslator(DASHSCOPE_BASE, DASHSCOPE_MODEL, key, "openai",
                   no_system=True,
                   terms=[("doãn chung dũng", "段忠勇"),
                          ("quảng ninh", "广宁省")])

SENTS = [
    "Cảnh sát tỉnh Quảng Ninh vừa khởi tố bị can Doãn Chung Dũng.",
    "Thủ tướng phát biểu tại cuộc họp báo sáng nay.",
    "Giá vàng hôm nay tiếp tục tăng mạnh trên thị trường.",
    "Bị can Doãn Chung Dũng khai nhận toàn bộ hành vi phạm tội.",
]

print("── 延迟（首呼含握手，后续应更快）──")
for i, s in enumerate(SENTS):
    out = tr(s)
    print(f"  [{i}] {tr.t_last:.2f}s  {s[:40]}\n       → {out[:60]}")

print("\n── 术语验证 ──")
print(f"  terms_used = {tr.terms_used}（4 句里 2 句含 doãn chung dũng，预期 2）")
print(f"  重连次数 = {tr._reconnects}")
