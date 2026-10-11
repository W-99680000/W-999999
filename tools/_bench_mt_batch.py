# -*- coding: utf-8 -*-
"""合并翻译的提示词：找出**能可靠切分**的格式。

背景（_bench_mt_cost.py 实测）：
  qwen-mt 每次调用有 0.57s 固定开销，而真实翻译计算几乎为 0
  （133 字符只要 0.66s，5 字符也要 0.55s）。
  所以把多段合并成一次调用能省掉重复的固定开销：
      4 段合并 0.686s vs 逐条 2.382s → 省 1.70s

**但我上一个提示词失败了**：4 段合并只返回了第一段。
这说明"能合并"和"能可靠切分"是两件事。

本脚本对比几种格式，判据是**可解析性**（不是看起来像不像）：
  ① 编号行    1. xxx
  ② 分隔符    |||
  ③ XML 标记  <t id="1">...</t>
  ④ JSON 数组
每种都直接跑真实句子，并按格式解析，看**能否 100% 还原段数**。
"""
import io
import json
import os
import re
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from llm_translate import (LLMTranslator, DASHSCOPE_BASE,
                           _read_dashscope_key as read_key)

KEY = read_key()
t = LLMTranslator(DASHSCOPE_BASE, "qwen-mt-flash", KEY, "openai", no_system=True)

SEGS = [
    "Okay.",
    "I never thought I would see you again.",
    "Get out of here before I call the police.",
    "The truth is I have been lying to you since the day we met.",
]

FORMATS = {
    "编号行": (
        "把下面每一行翻译成简体中文。\n"
        "输出格式：每行以「序号. 」开头，序号与输入一致，只输出译文。\n\n"
        "{body}",
        lambda src: "\n".join(f"{i+1}. {s}" for i, s in enumerate(src)),
        lambda out, n: re.findall(r"^\s*(\d+)[.、)]\s*(.+?)\s*$", out, re.M),
    ),
    "分隔符": (
        "把下面每一段翻译成简体中文。段与段之间用 ||| 分隔。\n"
        "输出同样用 ||| 分隔，只输出译文，不要序号。\n\n{body}",
        lambda src: " ||| ".join(src),
        lambda out, n: [p.strip() for p in out.split("|||") if p.strip()],
    ),
    "XML标记": (
        "把下面每个 <t> 标签内的文本翻译成简体中文。\n"
        "保持标签结构，只输出译文，不要解释。\n\n{body}",
        lambda src: "\n".join(f"<t>{s}</t>" for s in src),
        lambda out, n: re.findall(r"<t>(.*?)</t>", out, re.S),
    ),
    "JSON数组": (
        "把下面 JSON 数组里每个字符串翻译成简体中文。\n"
        "只输出 JSON 数组，元素个数与输入相同，不要代码块标记。\n\n{body}",
        lambda src: json.dumps(src, ensure_ascii=False),
        lambda out, n: json.loads(re.sub(r"^```(?:json)?|```$", "",
                                         out.strip(), flags=re.M).strip()),
    ),
}

print("=" * 86)
print("合并翻译：哪种格式能可靠切分")
print("=" * 86)
print(f"  输入 {len(SEGS)} 段")
print()
for name, (tmpl, build, parse) in FORMATS.items():
    body = build(SEGS)
    prompt = tmpl.replace("{body}", body)
    ts, outs, parsed = [], [], None
    for _ in range(3):
        t0 = time.time()
        out = t(prompt, target="Simplified Chinese")
        ts.append(time.time() - t0)
        outs.append(out)
    ts.sort()
    try:
        parsed = parse(outs[0], len(SEGS))
        n_ok = len(parsed)
    except Exception as e:
        parsed, n_ok = None, f"解析异常 {type(e).__name__}"
    print(f"  【{name}】{ts[1]:.3f}s　解析出 {n_ok} / {len(SEGS)} 段")
    print(f"    原始输出: {outs[0][:150]!r}")
    if isinstance(n_ok, int) and n_ok == len(SEGS):
        print("    ✓ 可切分")
    else:
        print("    ✗ 不可切分")
    print()

print("=" * 86)
print("对照：逐条分开翻译")
print("=" * 86)
t0 = time.time()
seps = [t(s, target="Simplified Chinese") for s in SEGS]
dt = time.time() - t0
print(f"  {dt:.3f}s（{len(SEGS)} 次调用）")
for s, z in zip(SEGS, seps):
    print(f"    {z[:46]}")
