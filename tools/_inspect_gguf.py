# -*- coding: utf-8 -*-
"""把 F 盘的 HY-MT 翻译模型**读透**（只看文件，不跑推理）。

之前只读了 GGUF 头的 6 个字段（架构/名字/规模），那是远远不够的 ——
要真用它，至少得知道：
  · 词表大小与分词器类型（决定怎么把文本喂进去）
  · 有没有内嵌 chat template（决定提示词长什么样）
  · 上下文长度、层数、头数（决定显存与速度）
  · 量化类型（决定精度损失）
  · 有没有对齐/衬垫 token 的特殊约定

本脚本只解析文件，不加载模型、不推理。
"""
import io
import os
import struct
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

D = r"F:\hh_si_pro\hh-translate-model"

# GGUF 值类型
T_U8, T_I8, T_U16, T_I16, T_U32, T_I32, T_F32, T_BOOL, T_STR, T_ARR, T_U64, T_I64, T_F64 = range(13)
SIZES = {T_U8: 1, T_I8: 1, T_U16: 2, T_I16: 2, T_U32: 4, T_I32: 4,
         T_F32: 4, T_BOOL: 1, T_U64: 8, T_I64: 8, T_F64: 8}
FMT = {T_U8: "<B", T_I8: "<b", T_U16: "<H", T_I16: "<h", T_U32: "<I",
       T_I32: "<i", T_F32: "<f", T_BOOL: "<B", T_U64: "<Q", T_I64: "<q",
       T_F64: "<d"}


def read_val(f, t):
    if t == T_STR:
        n = struct.unpack("<Q", f.read(8))[0]
        return f.read(n).decode("utf-8", "replace")
    if t == T_ARR:
        et = struct.unpack("<I", f.read(4))[0]
        n = struct.unpack("<Q", f.read(8))[0]
        if n > 100000:
            # 超大数组（如词表）只读长度，不读内容
            if et == T_STR:
                for _ in range(n):
                    ln = struct.unpack("<Q", f.read(8))[0]
                    f.seek(ln, 1)
                return f"<{n} 个字符串>"
            f.seek(n * SIZES.get(et, 0), 1)
            return f"<{n} 个元素, 类型 {et}>"
        return [read_val(f, et) for _ in range(n)]
    return struct.unpack(FMT[t], f.read(SIZES[t]))[0]


def dump(path, name):
    print("=" * 84)
    print(f"{name}   ({os.path.getsize(path)/1048576:.0f} MB)")
    print("=" * 84)
    with open(path, "rb") as f:
        magic = f.read(4)
        if magic != b"GGUF":
            print("  不是 GGUF")
            return
        ver = struct.unpack("<I", f.read(4))[0]
        n_tensors = struct.unpack("<Q", f.read(8))[0]
        n_kv = struct.unpack("<Q", f.read(8))[0]
        print(f"  GGUF v{ver}　张量 {n_tensors}　元数据 {n_kv} 项")
        print()
        kv = {}
        for _ in range(n_kv):
            klen = struct.unpack("<Q", f.read(8))[0]
            key = f.read(klen).decode("utf-8", "replace")
            vt = struct.unpack("<I", f.read(4))[0]
            try:
                kv[key] = read_val(f, vt)
            except Exception as e:
                kv[key] = f"<读取失败 {e}>"
                break
        # 分组打印
        for key in sorted(kv):
            v = kv[key]
            if isinstance(v, str) and len(v) > 300:
                v = v[:300] + "…"
            print(f"    {key} = {v!r}")
        # 张量名抽样（看结构）
        print()
        print("  张量名抽样（看是不是标准 transformer）:")
        names = []
        for _ in range(min(n_tensors, 400)):
            ln = struct.unpack("<Q", f.read(8))[0]
            tn = f.read(ln).decode("utf-8", "replace")
            nd = struct.unpack("<I", f.read(4))[0]
            dims = [struct.unpack("<Q", f.read(8))[0] for _ in range(nd)]
            qt = struct.unpack("<I", f.read(4))[0]
            off = struct.unpack("<Q", f.read(8))[0]
            names.append((tn, dims, qt))
        for tn, dims, qt in names[:18]:
            print(f"    {tn:<46} {dims}  quant={qt}")
        print(f"    …（共 {n_tensors} 个张量）")
    print()


for fn in sorted(os.listdir(D)):
    if fn.endswith(".gguf"):
        try:
            dump(os.path.join(D, fn), fn)
        except Exception as e:
            print(f"  {fn} 解析失败: {type(e).__name__}: {e}")
