# -*- coding: utf-8 -*-
"""翻译上下文（前缀切片法）的纯逻辑单测。
跑法：python bench/_verify_context.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_translate import translate_batch, BATCH_SEP   # noqa: E402
import pretranslate as P                                # noqa: E402

_ok = _fail = 0


def chk(name, cond):
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"  ok  {name}")
    else:
        _fail += 1
        print(f" FAIL {name}")


class FakeTr:
    """模拟 qwen-mt：把 prompt 正文按 SEP 拆开，每段加个 ZH: 前缀。"""

    def __init__(self, corrupt=False):
        self.corrupt = corrupt
        self.batch_fallbacks = 0
        self.last_prompt = None

    def __call__(self, text, target=None):
        self.last_prompt = text
        # 抠出正文（BATCH_PROMPT 里 body 在空行之后）；逐条调用没有 prompt 包装
        body = text.split("\n\n", 1)[1] if "\n\n" in text else text
        parts = [p.strip() for p in body.split(BATCH_SEP)]
        if self.corrupt:
            return "只有一段没有分隔符"
        return (" " + BATCH_SEP + " ").join(f"ZH:{p}" for p in parts)


print("== 上下文前缀切片 ==")
tr = FakeTr()
out = translate_batch(tr, ["それは何？", "大丈夫です。"],
                      context=["前の文です。", "その前の文。"])
chk("返回长度=本批长度（上下文被切掉）", len(out) == 2)
chk("本批内容正确", out == ["ZH:それは何？", "ZH:大丈夫です。"])
chk("上下文确实进了 prompt", "前の文です。" in tr.last_prompt)

print("== 无上下文回归 ==")
tr2 = FakeTr()
out2 = translate_batch(tr2, ["hello", "world"])
chk("无 context 照常工作", out2 == ["ZH:hello", "ZH:world"])

print("== 上下文导致段数错乱 → 退回逐条 ==")
tr3 = FakeTr(corrupt=True)
out3 = translate_batch(tr3, ["A", "B"], context=["C"])
chk("段数对不上退回逐条（不抛异常）", len(out3) == 2)
chk("记了 batch_fallbacks", tr3.batch_fallbacks == 1)

print("== translate_cues 上下文选取 ==")
# 构造 5 条 cue，前 3 条已译；翻第 4~5 条时应带第 2、3 条原文做上下文
cues = [P.Cue(float(i), float(i) + 1, f"文{i}") for i in range(5)]
done = {0: "译0", 1: "译1", 2: "译2"}
seen_ctx = {}


class CtxProbe:
    def __call__(self, text, target=None):
        body = text.split("\n\n", 1)[1]
        parts = [p.strip() for p in body.split(BATCH_SEP)]
        seen_ctx["n_ctx"] = len(parts) - 2      # 本批 2 条，多出来的就是上下文
        return (" " + BATCH_SEP + " ").join(f"译:{p}" for p in parts)


P.translate_cues(CtxProbe(), cues[3:], "zh", batch_size=2,
                 index_base=3, done=done, head_cues=cues[:3])
chk("带上了 2 条上下文", seen_ctx.get("n_ctx") == 2)
chk("本批两条都译出", done.get(3) == "译:文3" and done.get(4) == "译:文4")

print(f"\n合计 {_ok} 通过 / {_fail} 失败")
sys.exit(1 if _fail else 0)
