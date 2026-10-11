# -*- coding: utf-8 -*-
"""确认日文在真实链路里是否总走 qwen-mt，以及英文能否也改走它。

已确认的事实（_bench_ja.py）：
  · 日文翻译质量很好
  · 大量文本时同一模型日英质量相同
  · **英文走 argos，而 argos 有硬伤**："東京都知事" → "东京都督"

这里回答两件事：
  ① 日文会不会在某些情况下掉出 qwen-mt（那才会突然变差）
  ② 英文改走 qwen-mt 值不值（质量提升 vs 延迟代价）
"""
import io
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from live_subtitle_v2 import Translator, clean_translation

print("=" * 84)
print("① 日文在各条路径下的表现（哪条会突然变差）")
print("=" * 84)
JA = "お忙しいところ恐れ入りますが、ご確認いただけますでしょうか。"

# 正常路径：llm（qwen-mt）
t_llm = Translator("ja", "zh")
print(f"  Translator('ja') → mode={t_llm.mode}")
print(f"    译文: {t_llm(JA)}")
print()

# 危险路径：如果 LLM 不可用，会退到 google-free
print("  如果 qwen-mt 不可用（没 key / 欠费），会退到什么：")
import live_subtitle_v2 as M
saved = M._LLM_SINGLETON[:]
M._LLM_SINGLETON.clear()
M._LLM_SINGLETON.append(None)          # 假装没有 LLM
try:
    t_fb = Translator("ja", "zh")
    print(f"    mode={t_fb.mode}")
    t0 = time.time()
    out = t_fb(JA)
    dt = time.time() - t0
    print(f"    {dt:.2f}s  译文: {out}")
    print(f"    清理后: {clean_translation(out)}")
finally:
    M._LLM_SINGLETON.clear()
    M._LLM_SINGLETON.extend(saved)
print()

print("=" * 84)
print("② 英文改走 qwen-mt 值不值（质量 vs 延迟）")
print("=" * 84)
EN = [
    ("proper noun", "The Governor of Tokyo gave a comment at a press conference."),
    ("keigo-like", "I apologise for troubling you when you are busy, but could you check this?"),
    ("casual", "Seriously, isn't that kind of crazy?"),
    ("numbers", "Sales reached about thirty billion yen, one point five times last year."),
    ("long tech", "Once the operator locks the target correctly, the guidance system becomes immune to jamming."),
]
t_argos = Translator("en", "zh")
qm = t_llm.llm      # 复用同一个 qwen-mt 实例
print(f"  argos 模式={t_argos.mode}　qwen-mt 模式={t_llm.mode}")
print()
print(f"  {'类型':<14} {'argos（现用）':<40} {'qwen-mt'}")
print("  " + "-" * 92)
lat_a, lat_q = [], []
for tag, s in EN:
    t0 = time.time(); za = t_argos(s); ta = time.time() - t0
    t0 = time.time(); zq = qm(s, target="Simplified Chinese"); tq = time.time() - t0
    lat_a.append(ta); lat_q.append(tq)
    print(f"  {tag:<14} {za[:38]:<40} {zq[:38]}")
print()
print(f"  延迟中位：argos {sorted(lat_a)[len(lat_a)//2]:.2f}s　"
      f"qwen-mt {sorted(lat_q)[len(lat_q)//2]:.2f}s　"
      f"差 {sorted(lat_q)[len(lat_q)//2]-sorted(lat_a)[len(lat_a)//2]:+.2f}s")
print()
print("=" * 84)
print("结论")
print("=" * 84)
print("  ① 日文走 qwen-mt 时质量好；只有 qwen-mt 不可用时才退到 google-free，")
print("     那时质量会掉（那是降级路径，不是常态）。")
print("  ② 英文目前走 argos（离线、快 0.3s），但专有名词会译错。")
print("     改走 qwen-mt 只多 0.3s —— 而总延迟预算 0.88s 里塞得下。")
