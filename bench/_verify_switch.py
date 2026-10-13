# -*- coding: utf-8 -*-
"""_verify_switch.py —— 验证语言切换/提示词/缓存三项改动的轻量检查。

不发真实网络请求（LLM 调用全部拦下来看 payload），几秒跑完。
"""
import io
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import live_subtitle_v2 as M
import llm_translate as L

ok = fail = 0


def chk(name, cond):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ok   {name}")
    else:
        fail += 1
        print(f"  FAIL {name}")


print("一、Translator 构建成本（auto 必须跳过 argos）")
t0 = time.time()
t_auto = M.Translator("auto", "zh")
t_auto_cost = time.time() - t0
print(f"  auto 构建 {t_auto_cost:.2f}s mode={t_auto.mode}")
chk("auto 构建 < 3s（argos 已惰性跳过）", t_auto_cost < 3.0)
chk("auto 译器 mode 合理", t_auto.mode in
    ("llm", "local-llm", "google-free", "mymemory"))

print("\n二、源语言提示（准确性）")
chk("无 hint → _source_name 为 None", t_auto._source_name() is None)
t_auto.src_hint = "vi"
chk("hint=vi → Vietnamese", t_auto._source_name() == "Vietnamese")
t_auto.src_hint = "ja"
chk("hint=ja → Japanese", t_auto._source_name() == "Japanese")
t_auto.src_hint = "xx"
chk("未知码 → None（不乱猜）", t_auto._source_name() is None)

print("\n三、LLM payload 源语言注入")
llm = L.LLMTranslator("http://127.0.0.1:9", "m", None, "openai")
p = llm._payload("xin chào", "Simplified Chinese", source="Vietnamese")
body = p["messages"][-1]["content"]
chk("payload 含 Vietnamese 提示", "Vietnamese text into" in body)
p2 = llm._payload("xin chào", "Simplified Chinese")
chk("无 source 时保持原样", "following text into" in p2["messages"][-1]["content"])

print("\n四、每线程连接（threading.local）")
import threading as _th
chk("连接容器是 threading.local", isinstance(llm._tls, _th.local))
_seen = []

def _grab():
    try:
        _seen.append(llm._conn())
    except Exception:
        _seen.append(None)

ths = [_th.Thread(target=_grab) for _ in range(2)]
[t.start() for t in ths]
[t.join() for t in ths]
chk("两个线程拿到不同连接对象",
    _seen[0] is not None and _seen[1] is not None
    and _seen[0] is not _seen[1])

print("\n五、短句表按 src_hint 兜底（auto 模式不再浪费 PHRASEBOOK）")
t2 = M.Translator("auto", "zh")
chk("auto 译器本表为空", len(t2.book) == 0)
t2.src_hint = "vi"
# 直接调 __call__，vietnamese 短句应命中短句表、零网络
got = t2("cảm ơn")
chk(f"vi 短句命中短句表（{got!r}）", got == "谢谢")
t2.src_hint = "en"
got2 = t2("thank you")
chk(f"en 短句命中短句表（{got2!r}）", got2 == "谢谢")
t2.src_hint = None

print("\n六、LLM 单例与 warmup 存在")
chk("warmup 方法存在", hasattr(L.LLMTranslator, "warmup"))
chk("默认超时已降到 8s", L.LLMTranslator("http://x", "m").timeout == 8.0)

print(f"\n结果: {ok} ok / {fail} fail")
sys.exit(1 if fail else 0)
