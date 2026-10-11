# -*- coding: utf-8 -*-
"""qwen-mt 系列 vs DeepSeek：同一批真实字幕句，比延迟与质量。

素材取自本项目实际遇到的语种（越南语新闻、俄语/乌克兰语军事、
波兰语、英语长句），都是操作者真的在看的类型。

同时验 qwen-mt 的**翻译专用能力**（通用模型没有的）：
  · 术语定制（terms）—— 人名地名固定译法
  · 领域提示（domains）—— 新闻/军事/口语
"""
import io
import json
import os
import re
import ssl
import statistics
import sys
import time
import urllib.error
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

DS_KEY = os.environ.get("DS_KEY", "")
if not DS_KEY:
    print("  没有 DS_KEY 环境变量")
    sys.exit(1)

# DeepSeek（当前程序在用的）
CRED = os.path.join(os.path.expanduser("~"), ".dsh", ".credentials.yaml")
try:
    t = open(CRED, encoding="utf-8", errors="replace").read()
    DSP_KEY = re.search(r"DEEPSEEK_API_KEY:\s*(\S+)", t).group(1)
except Exception:
    DSP_KEY = ""

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

QWEN_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DSP_BASE = "https://api.deepseek.com"

SENTS = [
    ("越南语", "Sẵn hùng sau, xung mang xe đạp đi bán, rồi bị cáo Doãn Chung Dũng khai nhận hành vi."),
    ("越南语", "tình quảng ninh đưa ra xét sử bị cáo doãn chung dũng"),
    ("俄语", "Я только на это рассчитываю на самом деле, здесь женщина."),
    ("俄语", "Так, а что, расскажи, что там, что там за история с этим?"),
    ("俄语", "Его greatest advantage is that once the operator locks the target correctly, the guidance system becomes immune to jamming."),
    ("波兰语", "To to 100 tysięcy miesięcy, i żona milionera USA."),
    ("乌克兰语", "Управление идеально, дрон летит точно в цель."),
]


def call(base, key, model, text, extra=None, timeout=25, target="Chinese"):
    prompt = (f"Translate the following text into {target}. "
              f"Output ONLY the translation.\n\n{text}")
    payload = {"model": model, "temperature": 0.1, "max_tokens": 300,
               "messages": [{"role": "user", "content": prompt}]}
    if extra:
        payload.update(extra)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        base + "/chat/completions", data=body,
        headers={"Authorization": "Bearer " + key,
                 "Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        j = json.loads(r.read().decode("utf-8", "replace"))
    dt = time.time() - t0
    out = j["choices"][0]["message"]["content"].strip()
    out = out.split("\n")[0].strip().strip('"').strip("“”")
    return out, dt, j.get("usage", {})


MODELS = [
    ("qwen-mt-flash", QWEN_BASE, DS_KEY),
    ("qwen-mt-lite", QWEN_BASE, DS_KEY),
    ("qwen-mt-plus", QWEN_BASE, DS_KEY),
    ("deepseek-chat", DSP_BASE, DSP_KEY),
]

print("=" * 78)
print("一、延迟与译文（同一批句子，每个模型一次）")
print("=" * 78)

results = {}
for name, base, key in MODELS:
    if not key:
        print(f"\n【{name}】没有 key，跳过")
        continue
    print(f"\n【{name}】")
    lat = []
    for lang, s in SENTS:
        try:
            out, dt, usage = call(base, key, name, s)
            lat.append(dt)
            results.setdefault(name, []).append(out)
            print(f"  {dt:5.2f}s [{lang}] {out[:56]}")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:110]
            print(f"  HTTP {e.code} [{lang}] {body}")
            results.setdefault(name, []).append("")
        except Exception as e:
            print(f"  {type(e).__name__} [{lang}] {str(e)[:70]}")
            results.setdefault(name, []).append("")
    if lat:
        lat.sort()
        print(f"  → 中位 {statistics.median(lat):.2f}s　"
              f"最小 {min(lat):.2f}s　最大 {max(lat):.2f}s")

print("\n" + "=" * 78)
print("二、逐句对照（看质量差异）")
print("=" * 78)
names = [n for n, _, _ in MODELS if n in results]
for i, (lang, s) in enumerate(SENTS):
    print(f"\n[{lang}] {s[:66]}")
    for n in names:
        r = results[n][i] if i < len(results[n]) else ""
        print(f"    {n:<15} {r[:62]}")

print("\n" + "=" * 78)
print("三、qwen-mt 专用能力：术语定制 + 领域提示")
print("=" * 78)
# 术语定制（translation_options）
TERM_TEST = "tình quảng ninh đưa ra xét sử bị cáo doãn chung dũng"
print(f"\n  原句: {TERM_TEST}")
try:
    out, dt, _ = call(QWEN_BASE, DS_KEY, "qwen-mt-flash", TERM_TEST,
                      extra={"translation_options": {
                          "source_lang": "Vietnamese",
                          "target_lang": "Chinese",
                          "terms": [{"source": "Doãn Chung Dũng",
                                     "target": "尹忠勇"}]}})
    print(f"  指定术语（Doãn Chung Dũng → 尹忠勇）: {out}")
    print(f"  耗时 {dt:.2f}s")
except urllib.error.HTTPError as e:
    print(f"  术语定制 HTTP {e.code}: "
          f"{e.read().decode('utf-8','replace')[:130]}")
except Exception as e:
    print(f"  术语定制失败 {type(e).__name__}: {str(e)[:90]}")

# 领域提示
try:
    out, dt, _ = call(QWEN_BASE, DS_KEY, "qwen-mt-flash", SENTS[3][1],
                      extra={"translation_options": {
                          "source_lang": "Russian",
                          "target_lang": "Chinese",
                          "domains": "military news, colloquial speech"}})
    print(f"\n  领域提示（军事新闻+口语）: {out}")
    print(f"  耗时 {dt:.2f}s")
except urllib.error.HTTPError as e:
    print(f"\n  领域提示 HTTP {e.code}: "
          f"{e.read().decode('utf-8','replace')[:130]}")
except Exception as e:
    print(f"\n  领域提示失败 {type(e).__name__}: {str(e)[:90]}")
