# -*- coding: utf-8 -*-
"""日文翻译为什么比英文差：把两个环节分开量。

日文和英文走的路径本来就不同：
    en → argos 离线（en_zh 直连）
    ja → qwen-mt-flash（远端）
所以"日文差"可能是：
  ① 翻译环节差（qwen-mt 的日译中不行）
  ② 识别环节差（whisper/japanese 本来就不好，错在输入端）
  ③ 路由问题（明明有 en_ja 却没用上，或者被错判语言）

这个脚本分离验证 ①②：
  用**真实日文句子**直接打翻译，不经过识别 —— 排除识别影响。
  同一批句子给英文版，走 argos，做对照。

素材：取自日语新闻/访谈的常见句式（含专有名词、敬语、
    省略主语、片假名外来语 —— 这四类是日译中的难点）。
"""
import io
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from live_subtitle_v2 import Translator

# 日文测试句（覆盖四类难点）
JA = [
    ("普通叙述", "昨日の会議で新しい方針が決まりました。"),
    ("省略主语", "来週までに終わらせておきます。"),
    ("敬语", "お忙しいところ恐れ入りますが、ご確認いただけますでしょうか。"),
    ("片假名外来语", "このシステムのインターフェースをアップデートしました。"),
    ("专有名词", "東京都知事が記者会見でコメントを発表した。"),
    ("口语省略", "それって、まじでやばくない？"),
    ("被动+长句", "その計画は住民の反対によって中止に追い込まれたと報じられている。"),
    ("数字+单位", "売上は前年比で一・五倍、約三百億円に達した。"),
]
# 对应的英文（用来对照同一模型/路径下的表现）
EN = [
    ("plain", "A new policy was decided at yesterday's meeting."),
    ("dropped subject", "I will have it finished by next week."),
    ("keigo", "I apologise for troubling you when you are busy, but could you check this?"),
    ("katakana loanword", "We updated the interface of this system."),
    ("proper noun", "The Governor of Tokyo gave a comment at a press conference."),
    ("casual", "Seriously, isn't that kind of crazy?"),
    ("passive long", "It is reported that the plan was forced to a halt by residents' opposition."),
    ("numbers", "Sales reached about thirty billion yen, one point five times last year."),
]

print("=" * 84)
print("一、日文 → 中文（qwen-mt-flash，即当前实际路径）")
print("=" * 84)
tr_ja = Translator("ja", "zh")
print(f"  模式: {tr_ja.mode}")
print()
ja_out = []
for tag, s in JA:
    t0 = time.time()
    zh = tr_ja(s)
    dt = time.time() - t0
    ja_out.append(zh)
    print(f"  [{tag}] {dt:.2f}s")
    print(f"    日: {s}")
    print(f"    中: {zh}")
    print()

print("=" * 84)
print("二、英文 → 中文（argos 离线，即当前实际路径）")
print("=" * 84)
tr_en = Translator("en", "zh")
print(f"  模式: {tr_en.mode}")
print()
en_out = []
for tag, s in EN:
    t0 = time.time()
    zh = tr_en(s)
    dt = time.time() - t0
    en_out.append(zh)
    print(f"  [{tag}] {dt:.2f}s")
    print(f"    英: {s}")
    print(f"    中: {zh}")
    print()

print("=" * 84)
print("三、同一模型下的日/英对照（排除模型差异）")
print("=" * 84)
tr_qm = Translator("auto", "zh")
print(f"  模式: {tr_qm.mode}（日英都走它）")
print()
print(f"  {'tag':<14} {'英→中':<40} {'日→中'}")
print("  " + "-" * 80)
for (tag_e, se), (tag_j, sj) in zip(EN, JA):
    ze = tr_qm(se)
    zj = tr_qm(sj)
    print(f"  {tag_j:<14} {ze[:38]:<40} {zj[:38]}")
