# -*- coding: utf-8 -*-
"""验证官方文档里的三件事（用真实越南语素材，不猜）：

① 官方提示词模板 vs 我自造的
② 官方推荐推理参数（top_p 0.6 + repetition_penalty 1.05）vs 我用的
③ **术语干预** —— 能不能修掉实测到的专有名词错误
     · "số phét" 本地译"各种证据"、远端译"指纹"（正确）
     · 人名 doãn chung dũng 本地译"邓忠义"，远端译"段忠勇"

来源（官方，非推测）：
  https://github.com/Tencent-Hunyuan/HY-MT  README
  中↔XX 模板：将以下文本翻译为{target_language}，注意只需要输出翻译后的结果，不要额外解释：
  术语干预：  参考下面的翻译：\n{source_term} 翻译成 {target_term}\n\n<上面的模板>
  推理参数：  top_k 20 / top_p 0.6 / repetition_penalty 1.05 / temperature 0.7
"""
import io
import json
import os
import statistics
import subprocess
import sys
import time
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

LL = r"D:\NEW WORK\work\llamacpp"
MODEL = r"F:\hh_si_pro\hh-translate-model\hh-translate-base.gguf"
PORT = 8147
BASE = f"http://127.0.0.1:{PORT}"

BOS = "<\uff5chy_begin\u2581of\u2581sentence\uff5c>"
USER = "<\uff5chy_User\uff5c>"
ASST = "<\uff5chy_Assistant\uff5c>"

# 官方模板
TPL_ZH = ("将以下文本翻译为{target_language}，注意只需要输出翻译后的结果，"
          "不要额外解释：\n\n{source_text}")
TPL_XX = ("Translate the following segment into {target_language}, "
          "without additional explanation.\n\n{source_text}")
TPL_TERM = ("参考下面的翻译：\n{src_term} 翻译成 {tgt_term}\n\n" + TPL_ZH)

# 我原来用的（非官方）
TPL_MINE = "把下面的文本翻译成中文，只输出译文：\n{src}"

# 真实越南语段（本地 whisper GPU 识别出来的，见 _bench_vi_local_vs_remote）
SEGS = [
    "Sáng hôm sau, Xuân mang xe đạp đi bán, rồi mua vé tàu về quê.",
    "nghiệm thận trọng, tỷ mị, thu lượm từng số phét tại hiện trường.",
    "tình quảng ninh đưa ra xét sử bị cáo doãn chung dũng",
    "Không có đàn ông thì chắc là cũng không có mia trong ăn cơm",
]

# 术语表：从"远端译对、本地译错"的对照里提取的
TERMS = [
    ("doãn chung dũng", "段忠勇"),
    ("số phét", "指纹"),
    ("quảng ninh", "广宁省"),
]


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


def ask(prompt, params, n=140, timeout=60):
    body = {"prompt": BOS + USER + prompt + ASST, "n_predict": n,
            "stream": False}
    body.update(params)
    req = urllib.request.Request(
        BASE + "/completion",
        data=json.dumps(body, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        j = json.loads(r.read().decode("utf-8", "replace"))
    return (j.get("content") or "").strip().split("\n")[0], time.time() - t0


args = [os.path.join(LL, "llama-server.exe"), "--model", MODEL,
        "-ngl", "99", "-c", "1024", "--port", str(PORT), "--host", "127.0.0.1"]
proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, cwd=LL)
if not wait_ready(proc):
    print("  server 启动失败")
    sys.exit(1)
print("  server 就绪")

MINE = {"temperature": 0.0, "top_k": 1, "top_p": 1.0}
OFFICIAL = {"temperature": 0.7, "top_k": 20, "top_p": 0.6,
            "repeat_penalty": 1.05}

print()
print("=" * 96)
print("① + ② 模板与参数对比")
print("=" * 96)
for label, tpl, params in (
    ("我的模板 + 我的参数", TPL_MINE, MINE),
    ("官方模板 + 我的参数", TPL_ZH, MINE),
    ("官方模板 + 官方参数", TPL_ZH, OFFICIAL),
    ("我的模板 + 官方参数", TPL_MINE, OFFICIAL),
):
    print(f"\n【{label}】")
    for s in SEGS:
        try:
            out, dt = ask(tpl.format(target_language="中文", source_text=s), params)
        except Exception as e:
            print(f"    失败 {type(e).__name__}: {str(e)[:40]}")
            continue
        print(f"    {dt:.2f}s  原: {s[:52]}")
        print(f"            → {out[:60]}")

print()
print("=" * 96)
print("③ 术语干预：能不能修掉实测到的专有名词错误")
print("=" * 96)
for s in SEGS:
    print(f"\n  原: {s}")
    base_p = TPL_ZH.format(target_language="中文", source_text=s)
    out0, _ = ask(base_p, OFFICIAL)
    print(f"    无术语: {out0[:66]}")
    for src_t, tgt_t in TERMS:
        if src_t.lower() not in s.lower():
            continue
        p = TPL_TERM.format(src_term=src_t, tgt_term=tgt_t,
                            target_language="中文", source_text=s)
        out1, _ = ask(p, OFFICIAL)
        ok = tgt_t in out1
        print(f"    注入「{src_t} → {tgt_t}」: {out1[:60]}  "
              f"{'✓ 采纳' if ok else '✗ 未采纳'}")

proc.terminate()
try:
    proc.wait(timeout=10)
except Exception:
    proc.kill()
