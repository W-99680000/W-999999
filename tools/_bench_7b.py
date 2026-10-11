# -*- coding: utf-8 -*-
"""测 7B（HY-MT1.5）vs 1.8B：质量、速度、显存。

官方报告说 7B 在所有任务上更好（WMT25 上 +16%）。但它大 4 倍，
在 8GB 的 4070 上要和 whisper medium（1.6GB）共存 —— 显存够不够、
速度能不能用，都得实测。

用真实越南语素材（本地 whisper GPU 识别出来的原文），逐句对照。
同时用官方模板 + 官方参数 + 术语干预（术语两条路都加，公平比较）。
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
import wave

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

LL = r"D:\NEW WORK\work\llamacpp"
MODELS = [
    ("1.8B", r"F:\hh_si_pro\hh-translate-model\hh-translate-base.gguf"),
    ("7B", r"F:\hh_si_pro\hh-translate-model\hh-translate-large.gguf"),
]
PORT = 8149
BASE = f"http://127.0.0.1:{PORT}"

BOS = "<\uff5chy_begin\u2581of\u2581sentence\uff5c>"
USER = "<\uff5chy_User\uff5c>"
ASST = "<\uff5chy_Assistant\uff5c>"
TPL_ZH = ("将以下文本翻译为{target}，注意只需要输出翻译后的结果，"
          "不要额外解释：\n\n{src}")
PARAMS = {"temperature": 0.7, "top_k": 20, "top_p": 0.6,
          "repeat_penalty": 1.05}
TERMS = [("doãn chung dũng", "段忠勇"), ("số phét", "指纹"),
         ("quảng ninh", "广宁省")]

CJK = re.compile(r"[\u4e00-\u9fff]")


def cjk_ratio(s):
    return len(CJK.findall(s)) / max(1, len(s.strip())) if s else 0.0


def vram_used():
    r = subprocess.run(["nvidia-smi", "--query-gpu=memory.used",
                        "--format=csv,noheader,nounits"],
                       capture_output=True, text=True)
    try:
        return int((r.stdout or "0").strip().split("\n")[0])
    except Exception:
        return -1


# 真实越南语原文（本地 GPU 识别的，见 _bench_vi_local_vs_remote.py）
SEGS = [
    "Sáng hôm sau, Xuân mang xe đạp đi bán, rồi mua vé tàu về quê.",
    "nghiệm thận trọng, tỷ mị, thu lượm từng số phét tại hiện trường.",
    "tình quảng ninh đưa ra xét sử bị cáo doãn chung dũng",
    "trong khi bộ phận khám nghiệm.",
    "Bây giờ, phải có một nông viên thì phải một nông viên chỉ có một người",
    "Không có đàn ông thì chắc là cũng không có mia trong ăn cơm",
]


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


def ask(prompt, n=160, timeout=90):
    body = {"prompt": BOS + USER + prompt + ASST, "n_predict": n,
            "stream": False}
    body.update(PARAMS)
    req = urllib.request.Request(
        BASE + "/completion",
        data=json.dumps(body, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        j = json.loads(r.read().decode("utf-8", "replace"))
    return (j.get("content") or "").strip().split("\n")[0], time.time() - t0


print(f"  baseline VRAM: {vram_used()} MiB")
all_out = {}
for tag, path in MODELS:
    if not os.path.exists(path):
        print(f"  {tag}: 文件不存在")
        continue
    print()
    print("=" * 92)
    print(f"【{tag}】{os.path.basename(path)}  {os.path.getsize(path)/1048576:.0f} MB")
    print("=" * 92)
    v0 = vram_used()
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
    load = time.time() - t0
    v1 = vram_used()
    print(f"  启动 {load:.1f}s　显存 +{v1-v0} MiB（总 {v1} / "
          f"{subprocess.run(['nvidia-smi','--query-gpu=memory.total','--format=csv,noheader,nounits'],capture_output=True,text=True).stdout.strip()}）")
    print()
    print(f"  {'#':>2} {'耗时':>7} {'中文%':>6}  译文")
    print("  " + "-" * 84)
    outs, lats = [], []
    for i, s in enumerate(SEGS, 1):
        hits = [(a, b) for a, b in TERMS if a.lower() in s.lower()]
        if hits:
            p = "\n".join(f"参考下面的翻译：\n{a} 翻译成 {b}\n\n" +
                          TPL_ZH.format(target="中文", src=s)
                          for a, b in hits)
        else:
            p = TPL_ZH.format(target="中文", src=s)
        try:
            out, dt = ask(p)
        except Exception as e:
            print(f"  {i:>2}   失败  {type(e).__name__}: {str(e)[:44]}")
            continue
        outs.append(out)
        lats.append(dt)
        print(f"  {i:>2} {dt:>6.2f}s {cjk_ratio(out):>5.0%}  {out[:62]}")
    if lats:
        lats.sort()
        print(f"\n  → 延迟 中位 {statistics.median(lats):.2f}s　"
              f"最小 {min(lats):.2f}s　最大 {max(lats):.2f}s　"
              f"硬失败 {sum(1 for o in outs if cjk_ratio(o)<0.3)}/{len(outs)}")
    all_out[tag] = outs
    proc.terminate()
    try:
        proc.wait(timeout=15)
    except Exception:
        proc.kill()
    time.sleep(2)
    print(f"  释放后显存 {vram_used()} MiB")

print()
print("=" * 92)
print("逐句对照")
print("=" * 92)
for i, s in enumerate(SEGS):
    print(f"\n[{i+1}] {s}")
    for tag in all_out:
        o = all_out[tag][i] if i < len(all_out[tag]) else "(缺)"
        print(f"    {tag:<6} {o[:70]}")
