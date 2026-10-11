# -*- coding: utf-8 -*-
"""把 HY-MT 的**接口规范**完整挖出来 —— 这才能改它、修它。

要能诊断"为什么这句译错"，必须先确定它期望的**确切输入格式**：
  · chat_template 全文（我只看到前 300 字符，被截断了）
  · 特殊 token 的真实内容（名字被 llama.cpp 缩写成了 <｜hy_place▁holder▁no▁3｜>，
    真实的 Unicode 字符是什么？分隔符到底长什么样？）
  · bos/eos/pad/sep 在词表里对应什么字符串
  · 用上模板 vs 不用模板，输出差多少

判据：哪条路径能**稳定给出中文译文**。
"""
import io
import json
import os
import re
import struct
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

MODEL = r"F:\hh_si_pro\hh-translate-model\hh-translate-base.gguf"

T_STR = 8
T_ARR = 9


def read_val(f, t):
    if t == T_STR:
        n = struct.unpack("<Q", f.read(8))[0]
        return f.read(n).decode("utf-8", "replace")
    if t == T_ARR:
        et = struct.unpack("<I", f.read(4))[0]
        n = struct.unpack("<Q", f.read(8))[0]
        if et == T_STR:
            out = []
            for _ in range(n):
                ln = struct.unpack("<Q", f.read(8))[0]
                out.append(f.read(ln).decode("utf-8", "replace"))
            return out
        f.seek(n * {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1,
                    10: 8, 11: 8, 12: 8}.get(et, 0), 1)
        return f"<{n} 元素>"
    sizes = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}
    fmts = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f",
            7: "<B", 10: "<Q", 11: "<q", 12: "<d"}
    return struct.unpack(fmts[t], f.read(sizes[t]))[0]


print("=" * 92)
print("一、chat_template 全文")
print("=" * 92)
with open(MODEL, "rb") as f:
    f.read(4)
    struct.unpack("<I", f.read(4))[0]
    n_tensors = struct.unpack("<Q", f.read(8))[0]
    n_kv = struct.unpack("<Q", f.read(8))[0]
    kv = {}
    for _ in range(n_kv):
        klen = struct.unpack("<Q", f.read(8))[0]
        key = f.read(klen).decode("utf-8", "replace")
        vt = struct.unpack("<I", f.read(4))[0]
        kv[key] = read_val(f, vt)

tpl = kv.get("tokenizer.chat_template", "")
print(tpl)
print()

print("=" * 92)
print("二、特殊 token 的真实内容（名字被 llama.cpp 缩写了，看实际字符）")
print("=" * 92)
tokens = kv.get("tokenizer.ggml.tokens")
if isinstance(tokens, list):
    print(f"  词表共 {len(tokens)} 项")
    for tid in (120000, 120002, 120007, 120020):
        if tid < len(tokens):
            t = tokens[tid]
            print(f"    id {tid:>6} = {t!r}")
            print(f"             码点: {' '.join('U+%04X' % ord(c) for c in t)}")
    # 找所有 hy_ 开头的特殊 token
    print()
    print("  hy_ 相关特殊 token:")
    for i, t in enumerate(tokens):
        if "hy_" in t or "place" in t.lower():
            print(f"    id {i:>6} = {t!r}")
else:
    print("  词表未读到")
print()

print("=" * 92)
print("三、模板里用到的那些占位符是什么")
print("=" * 92)
for m in set(re.findall(r"<[^>]{0,60}>", tpl)):
    print(f"  {m!r}")
