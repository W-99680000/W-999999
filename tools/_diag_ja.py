# -*- coding: utf-8 -*-
"""日文"差"到底差在哪：把链路每一段分开量。

已确认（_bench_ja.py）：
  · 日文**翻译**很好（敬语/被动/口语/片假名都对）
  · 英文走 argos，反而有硬伤（"東京都知事" → "东京都督"）
  · 同一模型下日英质量相同
所以"日文差"不在翻译。剩下的可能在：
  ① 识别（whisper 的 japanese 本来就弱）
  ② 语言判错（日文被当成中文，因为汉字多）
  ③ 显示（CJK 断行）

本脚本验证 ②③（不需要日文音频）：
  · 把日文文本送进语言路由，看它被判成什么
  · 看显示层怎么处理日文
① 需要日文音频，本机没有 TTS 音色，无法合成 —— 如实说明。
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from live_subtitle_v2 import _pick_offline_source, clean_translation

print("=" * 80)
print("② 语言判错的风险：日文汉字多，会不会被当成中文？")
print("=" * 80)
print("  clean_translation 会做「目标语言校验」：译文必须含 CJK，否则丢弃。")
print("  日文原文**本身全是 CJK**，所以如果识别结果直接漏过去，")
print("  会被当成有效中文显示出来 —— 这是日文特有的风险（英文不会）。")
print()
ja_samples = [
    "昨日の会議で新しい方針が決まりました。",
    "お忙しいところ恐れ入りますが、ご確認いただけますでしょうか。",
    "このシステムのインターフェースをアップデートしました。",
]
for s in ja_samples:
    out = clean_translation(s)
    print(f"  日文原文: {s[:40]}")
    print(f"    clean_translation 返回: {out[:40]!r}")
    print(f"    → {'**原样通过（会被当成中文）**' if out else '被拦掉'}")
    print()

print("=" * 80)
print("③ 路由判据：日文会不会被送去 argos（只有 en 该去）")
print("=" * 80)
for lang, prob in (("ja", 0.99), ("ja", 0.60), ("zh", 0.95), ("en", 0.97)):
    r = _pick_offline_source(lang, prob)
    print(f"  lang={lang:<4} prob={prob:.2f} → 走 argos: {r}")
print()
print("  英文高置信才走 argos，日文一律走 qwen-mt —— 路由本身没错。")
print()
print("=" * 80)
print("① 识别：本机无法验证（如实说明）")
print("=" * 80)
print("  本机只有两个 TTS 音色：")
print("    Microsoft Huihui Desktop [zh-CN]")
print("    Microsoft Zira Desktop  [en-US]")
print("  **没有日文音色**，合成不出日文语音，所以测不了 whisper 的日文识别。")
print()
print("  能查到的已知情况（公开常识，非本机实测）：")
print("    · whisper 官方 WER 基准里，japanese 明显差于 english")
print("      （Common Voice / Fleurs 上日语 CER 通常比英语 WER 高一档）")
print("    · 日语识别的难点：同音词极多、无空格分词、汉字读音多解")
print("  所以日文链路里**最可能弱的一环是识别**，不是翻译。")
