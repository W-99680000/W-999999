# -*- coding: utf-8 -*-
"""tools/check_registry_wiring.py —— 注册表 ↔ 命令行参数 接线校验

为什么需要它
============
`model_registry.cli_args("cache")` 吐出来的键，最终是喂给
`pretranslate.build_parser()` 里那些 `add_argument` 的默认值的。
**键名对不上不会报错** —— argparse 的 `set_defaults(**kw)` 对未知键
静默接受，于是"注册表里改了模型，程序还是跑老模型"这种问题
可以潜伏很久，而且现象是"感觉没变化"，最难查。

所以这里做**静态**校验（用 ast 解析源码，不 import 那两个大模块）：

  ① 注册表给这个模式的每个键，在对应 build_parser() 里都有 add_argument
  ② 值的类型与 argparse 的 type/default 相容
     （int 型参数拿到 "2.0" 这种字符串就是接线错了）
  ③ 注册表里声明的模型 id 都在 asr/mt 池里存在
  ④ 两个模块**共用**的参数（如 model）不会被注册表给出互相矛盾的值
  ⑤ 报告"哪些键被静默丢弃"（本工具最大的价值：把静默变成显式）

用法
====
    python tools/check_registry_wiring.py          # 校验，返回码非 0 = 接线错
    python tools/check_registry_wiring.py -v       # 连通过项一起打印
"""
from __future__ import annotations

import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import model_registry as R  # noqa: E402

#: 模式 → (源码文件, build_parser 函数名)
TARGETS = {
    "loopback": ("live_subtitle_v2.py", "build_parser"),
    "cache": ("pretranslate.py", "build_parser"),
}


def _const(node):
    """从 ast 节点取字面量；取不到返回 None。"""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.List):
        return [_const(e) for e in node.elts]
    return None


def _type_name(node):
    """add_argument(type=...) 的取值 → 类型名。"""
    if isinstance(node, ast.Name):
        return node.id                      # int / float / str
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def read_parser_args(path: str, func: str) -> dict[str, dict]:
    """静态读出 build_parser() 里声明的参数。

    返回 {dest: {"flags":[...], "type":..., "default":..., "action":...}}

    **只读源码、不 import** —— live_subtitle_v2 有 3200 行且顶层就 import
    PyAudio/Qt，为了查表把它跑起来既慢又可能起界面。
    """
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src, filename=path)
    fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func:
            fn = node
            break
    if fn is None:
        raise SystemExit(f"{path} 里找不到 {func}()")
    out: dict[str, dict] = {}
    for node in ast.walk(fn):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"):
            continue
        flags = [_const(a) for a in node.args if isinstance(a, ast.Constant)]
        flags = [f for f in flags if isinstance(f, str)]
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        dest = _const(kw["dest"]) if "dest" in kw else None
        if not dest:
            longs = [f for f in flags if f.startswith("--")]
            dest = (longs[0][2:].replace("-", "_") if longs
                    else (flags[0] if flags else None))
        if not dest:
            continue
        out[dest] = {
            "flags": flags,
            "type": _type_name(kw["type"]) if "type" in kw else None,
            "default": _const(kw["default"]) if "default" in kw else None,
            "action": _const(kw["action"]) if "action" in kw else None,
            "has_default": "default" in kw,
        }
    return out


def _compatible(value, spec: dict) -> tuple[bool, str]:
    """注册表的值能不能喂给这个 argparse 参数。"""
    t = spec.get("type")
    d = spec.get("default")
    if spec.get("action") == "store_const":
        return True, "store_const（由 action 决定，值被忽略）"
    if isinstance(d, bool):
        return isinstance(value, bool), f"期望 bool，得到 {type(value).__name__}"
    if t == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            return False, f"期望 int，得到 {value!r}（{type(value).__name__}）"
        return True, "int"
    if t == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False, f"期望 float，得到 {value!r}"
        return True, "float"
    if t == "str":
        return isinstance(value, str), f"期望 str，得到 {value!r}"
    if t is None and isinstance(d, (int, float, str)) and not isinstance(d, bool):
        same = isinstance(value, type(d))
        return same, f"无 type 声明，默认值 {d!r}（{type(d).__name__}）"
    return True, "未声明类型，跳过类型校验"


def check(verbose: bool = False) -> int:
    ok = bad = 0
    warn: list[str] = []

    def good(msg: str) -> None:
        nonlocal ok
        ok += 1
        if verbose:
            print(f"  ✓ {msg}")

    def fail(msg: str) -> None:
        nonlocal bad
        bad += 1
        print(f"  ✗ {msg}")

    d = R.load()

    # ③ 模型 id 都存在
    for mid, r in R.all_resolved(d).items():
        for slot in ("asr", "mt", "asr_stream"):
            v = r.get(slot)
            if v and R.get(v, d) is None:
                fail(f"{mid}.{slot} 指向不存在的模型 {v!r}")
            else:
                good(f"{mid}.{slot} = {v}")

    for mode, (fname, func) in TARGETS.items():
        path = os.path.join(ROOT, fname)
        args = read_parser_args(path, func)
        kw = R.cli_args(mode, d)
        applied, dropped = {}, {}
        for k, v in kw.items():
            if k in args:
                applied[k] = v
            else:
                dropped[k] = v
        print(f"[{mode}] {fname}::{func}()  声明参数 {len(args)} 个，"
              f"注册表给 {len(kw)} 个 → 生效 {len(applied)}，丢弃 {len(dropped)}")
        if dropped:
            # 丢弃本身不一定是错（各模块参数集不同是设计的一部分），
            # 但**必须看得见** —— 所以列出来，且不当作失败。
            warn.append(f"{mode} 丢弃: {sorted(dropped)} "
                        f"（{fname} 没有这些参数，属正常）")
        for k, v in applied.items():
            fine, why = _compatible(v, args[k])
            if fine:
                good(f"{mode}.{k} = {v!r}  [{why}]  flags={args[k]['flags']}")
            else:
                fail(f"{mode}.{k} = {v!r} 与 {args[k]['flags']} 不相容：{why}")

    # ④ 两个模块共用的 --model 不能互相矛盾
    a, b = R.cli_args("loopback", d), R.cli_args("cache", d)
    if "model" in a and "model" in b:
        if a["model"] == b["model"]:
            print(f"  ! 两个模式的识别模型相同（{a['model']}）—— "
                  f"如果是有意的（都改走同一个）就忽略这条提示；"
                  f"如果不是，缓存模式本应更准")
            warn.append(f"loopback/cache 的 model 相同：{a['model']}")
        else:
            good(f"两模式识别模型不同（实时 {a['model']} / 缓存 {b['model']}）")

    print(f"\n接线校验：{ok} 项通过，{bad} 项失败")
    for w in warn:
        print("  ! ", w)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(check("-v" in sys.argv or "--verbose" in sys.argv))
