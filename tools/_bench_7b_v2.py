# -*- coding: utf-8 -*-
"""7B 的真实模板重测。

上一版结论"7B 比 1.8B 差"是**无效的** —— 我给 7B 发了 1.8B 的标记。

两个模型的 chat 模板完全不同（从各自 GGUF 的 tokenizer.chat_template 读的）：

1.8B（hy_ 系列）
    <|hy_begin_of_sentence|><|hy_User|>{user}<|hy_Assistant|>{asst}<|hy_placeholder_2|>
    bos=120000  eos=120020  pad=120002

7B（startoftext / extra_N 系列）
    user      → <|startoftext|>{content}<|extra_0|>
    system    → <|startoftext|>{content}<|extra_4|>
    assistant → {content}<|eos|>
    bos=127958  eos=127960  pad=127961

**同一个模型家族，两代用不同 token 体系。** 所以"7B 差"是发错 token 造成的。

本脚本用 7B 自己的模板重测，并与 1.8B（用它的模板）公平对照。
"""
import io
import json
import os
import re
import statistics
import struct
import subprocess
import sys
import time
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

LL = r"D:\NEW WORK\work\llamacpp"
PORT = 8151
BASE = f"http://127.0.0.1:{PORT}"

# 1.8B 的标记
BOS_18 = "<\uff5chy_begin\u2581of\u2581sentence\uff5c>"
USER_18 = "<\uff5chy_User\uff5c>"
ASST_18 = "<\uff5chy_Assistant\uff5c>"
# 7B 的标记
SOT_7 = "<|startoftext|>"
EX0_7 = "<|extra_0|>"

TPL = ("将以下文本翻译为{target}，注意只需要输出翻译后的结果，"
       "不要额外解释：\n\n{src}")
TPL_TERM = "参考下面的翻译：\n{a} 翻译成 {b}\n\n" + TPL
PARAMS = {"temperature": 0.7, "top_k": 20, "top_p": 0.6,
          "repeat_penalty": 1.05}
TERMS = [("doãn chung dũng", "段忠勇"), ("số phét", "指纹"),
         ("quảng ninh", "广宁省")]

CJK = re.compile(r"[\u4e00-\u9fff]")


def cjk_ratio(s):
    return len(CJK.findall(s)) / max(1, len(s.strip())) if s else 0.0


SEGS = [
    "Sáng hôm sau, Xuân mang xe đạp đi bán, rồi mua vé tàu về quê.",
    "nghiệm thận trọng, tỷ mị, thu lượm từng số phét tại hiện trường.",
    "tình quảng ninh đưa ra xét sử bị cáo doãn chung dũng",
    "trong khi bộ phận khám nghiệm.",
    "Bây giờ, phải có một nông viên thì phải một nông viên chỉ có một người",
    "Không có đàn ông thì chắc là cũng không có mia trong ăn cơm",
]


def build_prompt(src, style):
    hits = [(a, b) for a, b in TERMS if a.lower() in src.lower()]
    if hits:
        body = "\n".join(TPL_TERM.format(a=a, b=b, target="中文", src=src)
                         for a, b in hits)
    else:
        body = TPL.format(target="中文", src=src)
    if style == "18":
        return BOS_18 + USER_18 + body + ASST_18
    # 7B：user 轮 = <|startoftext|>{body}<|extra_0|>
    return SOT_7 + body + EX0_7


def wait_ready(proc, timeout=240):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(BASE + "/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def ask(prompt, n=200, timeout=90):
    body = {"prompt": prompt, "n_predict": n, "stream": False}
    body.update(PARAMS)
    req = urllib.request.Request(
        BASE + "/completion",
        data=json.dumps(body, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        j = json.loads(r.read().decode("utf-8", "replace"))
    return (j.get("content") or "").strip(), time.time() - t0


RUNS = [
    ("1.8B + 1.8B模板", "18",
     r"F:\hh_si_pro\hh-translate-model\hh-translate-base.gguf"),
    ("7B + 7B模板", "7",
     r"F:\hh_si_pro\hh-translate-model\hh-translate-large.gguf"),
]

results = {}
for label, style, path in RUNS:
    if not os.path.exists(path):
        print(f"  {label}: 文件不存在")
        continue
    print()
    print("=" * 92)
    print(f"【{label}】")
    print("=" * 92)
    args = [os.path.join(LL, "llama-server.exe"), "--model", path,
            "-ngl", "99", "-c", "1024", "--port", str(PORT),
            "--host", "127.0.0.1"]
    t0 = time.time()
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, cwd=LL)
    if not wait_ready(proc):
        print("  启动失败")
        proc.kill()
        continue
    print(f"  启动 {time.time()-t0:.1f}s")
    print()
    print(f"  {'#':>2} {'耗时':>7} {'中文%':>6}  译文")
    print("  " + "-" * 84)
    outs, lats = [], []
    for i, s in enumerate(SEGS, 1):
        try:
            out, dt = ask(build_prompt(s, style))
        except Exception as e:
            print(f"  {i:>2}   失败  {type(e).__name__}: {str(e)[:44]}")
            continue
        out1 = out.split("\n")[0]
        outs.append(out1)
        lats.append(dt)
        print(f"  {i:>2} {dt:>6.2f}s {cjk_ratio(out1):>5.0%}  {out1[:60]}")
    if lats:
        lats.sort()
        print(f"\n  → 中位 {statistics.median(lats):.2f}s　"
              f"硬失败 {sum(1 for o in outs if cjk_ratio(o)<0.3)}/{len(outs)}")
    results[label] = outs
    proc.terminate()
    try:
        proc.wait(timeout=15)
    except Exception:
        proc.kill()
    time.sleep(2)

print()
print("=" * 92)
print("逐句对照")
print("=" * 92)
keys = list(results)
for i, s in enumerate(SEGS):
    print(f"\n[{i+1}] {s}")
    for k in keys:
        o = results[k][i] if i < len(results[k]) else "(缺)"
        print(f"    {k:<18} {o[:66]}")
