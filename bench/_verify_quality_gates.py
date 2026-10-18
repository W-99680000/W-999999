# -*- coding: utf-8 -*-
"""bench/_verify_quality_gates.py —— 质量/实时性改动的纯函数回归。

覆盖（不联网、不加载模型）：
  ① srt_hold_for：SRT 结束时间按真实语音时长估算
  ② batch_flush_reason：静音句尾提前发批判定
  ③ SrtWriter：写入的 end-start 与真实时长一致
  ④ Qwen-MT translation_options：messages 只放文本、术语/语言进选项、
     服务端拒绝时降级回 prompt 风格
  ⑤ is_hallucination 仍拦复读、不误杀正常句
"""
from __future__ import annotations

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import live_subtitle_v2 as M            # noqa: E402
import llm_translate as LT             # noqa: E402

n_ok = n_bad = 0


def chk(name, ok, detail=""):
    global n_ok, n_bad
    if ok:
        n_ok += 1
        print(f"  ok   {name}")
    else:
        n_bad += 1
        print(f"  FAIL {name}  {detail}")


print("\n一、SRT 真实时长")
chk("无时长信息 → 退回上限 6s", M.srt_hold_for(None) == 6.0)
chk("2.0s 语音 → 2.65s（*1.15+0.35）",
    abs(M.srt_hold_for(2.0) - 2.65) < 1e-6, M.srt_hold_for(2.0))
chk("0.2s 碎片 → 下限 1.0s", M.srt_hold_for(0.2) == 1.0)
chk("10s 长句 → 上限 6.0s", M.srt_hold_for(10.0) == 6.0)

with tempfile.TemporaryDirectory() as td:
    p = os.path.join(td, "x.srt")
    w = M.SrtWriter(p, hold=6.0)
    w.add("hello", "你好", dur=2.0)
    txt = open(p, encoding="utf-8").read()
    import re
    m = re.search(r"(\d\d:\d\d:\d\d,\d\d\d) --> (\d\d:\d\d:\d\d,\d\d\d)", txt)
    chk("SrtWriter 写出了时间轴", bool(m), txt[:80])
    if m:
        def sec(t):
            h, mm, rest = t.split(":")
            s, ms = rest.split(",")
            return int(h) * 3600 + int(mm) * 60 + int(s) + int(ms) / 1000
        span = sec(m.group(2)) - sec(m.group(1))
        chk("结束时间=真实时长外扩（≈2.65s）", 2.0 < span <= 3.2, span)

print("\n二、静音句尾提前发批")
chk("够 max_batch 优先", M.batch_flush_reason(
    2.0, 0.1, False, 2.0, 1.2, 0.6) == "max_batch")
chk("等够 max_wait", M.batch_flush_reason(
    0.5, 1.3, False, 2.0, 1.2, 0.6) == "max_wait")
chk("静音句尾 + 达 flush_min → natural", M.batch_flush_reason(
    0.8, 0.2, True, 2.0, 1.2, 0.6) == "natural")
chk("静音句尾但太短 → 不发", M.batch_flush_reason(
    0.3, 0.2, True, 2.0, 1.2, 0.6) is None)
chk("flush_min=0 关闭提前发", M.batch_flush_reason(
    0.8, 0.2, True, 2.0, 1.2, 0) is None)
chk("空缓冲不发", M.batch_flush_reason(
    0.0, 9.9, True, 2.0, 1.2, 0.6) is None)

print("\n三、Qwen-MT 官方 translation_options")
tr = LT.LLMTranslator("https://example.com", "qwen-mt-flash",
                      "k", "openai", no_system=True)
tr.set_terms([("Bilibili", "哔哩哔哩"), ("up主", "创作者")])
body = tr._payload("I watched it on Bilibili.", "Simplified Chinese",
                   source="English")
opts = body.get("translation_options") or {}
chk("请求体带 translation_options", bool(opts), body)
chk("source_lang 归一成 English", opts.get("source_lang") == "English",
    opts.get("source_lang"))
chk("target_lang 归一成 Chinese", opts.get("target_lang") == "Chinese",
    opts.get("target_lang"))
chk("术语表进 terms 数组",
    {"source": "Bilibili", "target": "哔哩哔哩"} in (opts.get("terms") or []),
    opts.get("terms"))
chk("messages 只有一条 user 且只放原文",
    body.get("messages") == [{"role": "user",
                              "content": "I watched it on Bilibili."}],
    body.get("messages"))
# 降级路径：服务端不认 translation_options 时回到原 prompt 风格
tr.no_options = True
fbody = tr._payload("I watched it on Bilibili.", "Simplified Chinese")
chk("降级后不带 translation_options", "translation_options" not in fbody)
chk("降级后回到翻译指令 prompt",
    "Translate the following" in fbody["messages"][-1]["content"])
# 普通 OpenAI 端点不受影响
otr = LT.LLMTranslator("https://example.com", "deepseek-chat", "k",
                       "openai")
chk("非 qwen-mt 不带 options",
    "translation_options" not in otr._payload("hi", "Simplified Chinese"))

print("\n三b、CPS 间隙延长（切句不降 CPS）")
import pretranslate as PT
_LONG = "this is a very long subtitle line that definitely exceeds twenty chars per second"
c1 = PT.Cue(0.0, 2.0, _LONG)
c2 = PT.Cue(4.0, 6.0, "next")
cps_before = len(re.sub(r"\s+", "", c1.text)) / 2.0
PT.fit_cues_cps([c1, c2], max_cps=20.0)
cps_after = len(re.sub(r"\s+", "", c1.text)) / (c1.end - c1.start)
chk("有静音间隙时延长到 CPS 达标", cps_after <= 20.01, (cps_before, cps_after))
chk("不越过下一条开始时间", c1.end <= 4.0, c1.end)
d1 = PT.Cue(0.0, 2.0, _LONG)
d2 = PT.Cue(2.0, 4.0, "next")
PT.fit_cues_cps([d1, d2], max_cps=20.0)
chk("无间隙（密集语音）时无法降低，保持原样（不丢文本）",
    abs((d1.end - d1.start) - 2.0) < 1e-6)
chk("合规短句不动", (lambda x: x.end == 2.0)(
    PT.fit_cues_cps([PT.Cue(0.0, 2.0, "短句。")], max_cps=20.0)[0]))
chk("max_cps=0 关闭", (lambda x: x.end == 2.0)(
    PT.fit_cues_cps([PT.Cue(0.0, 2.0, _LONG)], max_cps=0)[0]))

print("\n四、幻觉过滤")
for s_, want in (("不能理解,怎麼理解,怎麼理解,怎麼理解,怎麼理解,怎麼理解。", True),
                 ("왕코, 왕코, 왕코, 왕코, 왕코, 왕코.", True),
                 ("广宁省警方刚刚以谋杀罪起诉被告段忠勇。", False),
                 ("对对对", False),
                 ("哈哈哈哈", False)):
    chk(f"is_hallucination={want}: {s_[:18]}",
        M.is_hallucination(s_) is want)

print(f"\n合计 {n_ok} 通过 / {n_bad} 失败")
raise SystemExit(0 if n_bad == 0 else 1)
