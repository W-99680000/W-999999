# -*- coding: utf-8 -*-
"""用 HY-MT 的**正确接口**重测：模板格式 vs 裸提示词。

挖出来的完整接口（从 GGUF 的 chat_template 读的，不是猜的）：

    {% if system %}<｜hy_begin▁of▁sentence｜>{{ sys }}<｜hy_place▁holder▁no▁3｜>
    {% else %}    <｜hy_begin▁of▁sentence｜>
    {% endif %}
    user      → <｜hy_User｜>{{ content }}
    assistant → <｜hy_Assistant｜>{{ content }}<｜hy_place▁holder▁no▁2｜>
    生成时追加 <｜hy_Assistant｜>

实际 token（词表 id）：
    120000 <｜hy_begin▁of▁sentence｜>   bos
    120007 <｜hy_Assistant｜>            生成起点
    120020 <｜hy_place▁holder▁no▁2｜>   assistant 段结束
    120002 <｜hy_▁pad▁｜>                pad
    sep    <｜hy_place▁holder▁no▁8｜>

**关键判断**：之前我直接发 "把下面的文本翻译成中文：xxx"，
没有 <｜hy_User｜>/<｜hy_Assistant｜>/句首标记 —— 那是**训练分布外的输入**。
这能解释为什么它对格式极度敏感、越南语会编内容。

本脚本对比：裸提示词 vs 正确模板，三种源语言。
判据仍然是**中文占比**（客观），外加人眼看内容对不对。
"""
import io
import json
import os
import re
import statistics
import subprocess
import sys
import time
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

LL = r"D:\NEW WORK\work\llamacpp"
MODEL = r"F:\hh_si_pro\hh-translate-model\hh-translate-base.gguf"
PORT = 8141
BASE = f"http://127.0.0.1:{PORT}"

BOS = "<\uff5chy_begin\u2581of\u2581sentence\uff5c>"
USER = "<\uff5chy_User\uff5c>"
ASST = "<\uff5chy_Assistant\uff5c>"
EOM = "<\uff5chy_place\u2581holder\u2581no\u25812\uff5c>"

CJK = re.compile(r"[\u4e00-\u9fff]")


def cjk_ratio(s):
    return len(CJK.findall(s)) / max(1, len(s.strip())) if s else 0.0


CASES = [
    ("英文", "I never thought I would see you again."),
    ("越南语", "tình quảng ninh đưa ra xét sử bị cáo doãn chung dũng"),
    ("俄语", "Я только на это рассчитываю на самом деле, здесь женщина."),
    ("波兰语", "To to 100 tysięcy miesięcy, i żona milionera USA."),
]

INSTR = "把下面的文本翻译成中文，只输出译文：\n{src}"


def wait_ready(proc, timeout=120):
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
        time.sleep(0.4)
    return False


def ask(prompt, n=90, timeout=60):
    body = json.dumps({"prompt": prompt, "n_predict": n,
                       "temperature": 0.0, "top_k": 1, "top_p": 1.0,
                       "stream": False}, ensure_ascii=False).encode()
    req = urllib.request.Request(BASE + "/completion", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        j = json.loads(r.read().decode("utf-8", "replace"))
    return (j.get("content") or "").strip(), time.time() - t0


args = [os.path.join(LL, "llama-server.exe"), "--model", MODEL,
        "-ngl", "99", "-c", "512", "--port", str(PORT), "--host", "127.0.0.1"]
proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, cwd=LL)
if not wait_ready(proc):
    print("  server 启动失败")
    sys.exit(1)
print("  server 就绪")
print()

VARIANTS = {
    "裸提示词": lambda src: INSTR.format(src=src),
    "模板(仅 Assistant)": lambda src: BOS + INSTR.format(src=src) + ASST,
    "模板(User/Assistant)":
        lambda src: BOS + USER + INSTR.format(src=src) + ASST,
    "模板+assistant收尾":
        lambda src: BOS + USER + INSTR.format(src=src) + EOM + ASST,
}

print("=" * 96)
print("正确接口 vs 裸提示词")
print("=" * 96)
results = {}
for name, build in VARIANTS.items():
    print(f"\n【{name}】")
    rs = []
    for label, src in CASES:
        p = build(src)
        try:
            out, dt = ask(p)
        except Exception as e:
            print(f"    {label:<6} 失败 {type(e).__name__}: {str(e)[:40]}")
            rs.append(0.0)
            continue
        r = cjk_ratio(out)
        rs.append(r)
        flag = "✓" if r > 0.3 else ("半" if r > 0.05 else "✗")
        print(f"    {label:<6} {dt:5.2f}s [{flag} {r:>3.0%}] {out[:60]}")
    results[name] = rs

print()
print("=" * 96)
print("汇总（中文占比，越高越说明格式对了）")
print("=" * 96)
print(f"  {'方案':<24} " + " ".join(f"{l:>7}" for l, _ in CASES) + "   平均")
for name, rs in sorted(results.items(), key=lambda kv: -statistics.mean(kv[1])):
    print(f"  {name:<24} " + " ".join(f"{r:>6.0%} " for r in rs)
          + f"  {statistics.mean(rs):>5.0%}")

proc.terminate()
try:
    proc.wait(timeout=10)
except Exception:
    proc.kill()
