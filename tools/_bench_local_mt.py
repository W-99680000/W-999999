# -*- coding: utf-8 -*-
"""量本地 HY-MT 翻译模型：延迟、显存、质量，和远端 qwen-mt 对比。

背景：F 盘的 HY-MT（腾讯混元翻译模型）是标准 llama.cpp GGUF，
而同一个软件包里**带了现成的 llama.cpp Windows Vulkan 构建** ——
这就是我之前"llama-cpp-python 编译失败"的解法（不需要 CUDA Toolkit）。

已确认能跑（llama-cli）：
    输入 "I never thought I would see you again."
    输出 "我从没想过会再次见到你。"
    Prompt 787.6 t/s　Generation 150.9 t/s　显存 1075MB

但 llama-cli 是交互式的，**嵌进程序要走 llama-server 的 HTTP 接口**
（那才有稳定的请求/响应边界）。本脚本测 server 模式的延迟。

server 启动参数（照抄 llama-cli 验证过的那套）：
    --model <gguf> -ngl 99 -c 512 --temp 0.7 --top-k 20 --top-p 0.8
"""
import io
import json
import os
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

LL = r"D:\NEW WORK\work\llamacpp"
SERVER = os.path.join(LL, "llama-server.exe")
BASE = "http://127.0.0.1:8137"
MODELS = {
    "base": r"F:\hh_si_pro\hh-translate-model\hh-translate-base.gguf",
    "small": r"F:\hh_si_pro\hh-translate-model\hh-translate-small.gguf",
}
KEY = ""
for line in open(os.path.join(os.path.expanduser("~"), ".dsh", ".dashscope"),
                 encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#"):
        KEY = line
        break


def wait_ready(proc, timeout=180):
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


def ask(prompt, timeout=60, n_predict=80):
    body = json.dumps({
        "prompt": prompt,
        "n_predict": n_predict,
        "temperature": 0.7, "top_k": 20, "top_p": 0.8,
        "stop": ["\n"], "stream": False,
    }, ensure_ascii=False).encode()
    req = urllib.request.Request(BASE + "/completion", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        j = json.loads(r.read().decode("utf-8", "replace"))
    return j.get("content", "").strip(), time.time() - t0


SENTS = [
    ("英文短", "I never thought I would see you again."),
    ("英文长", "Once the operator locks the target correctly, the guidance "
               "system becomes immune to jamming."),
    ("越南语", "tình quảng ninh đưa ra xét sử bị cáo doãn chung dũng"),
    ("俄语", "Я только на это рассчитываю на самом деле, здесь женщина."),
]
PROMPT = "把下面这段文本翻译成简体中文，只输出译文：\n{src}"

for tag, path in MODELS.items():
    if not os.path.exists(path):
        print(f"  {tag}: 文件不存在")
        continue
    print("=" * 86)
    print(f"本地 HY-MT（{tag}）  {os.path.getsize(path)/1048576:.0f} MB")
    print("=" * 86)
    args = [SERVER, "--model", path, "-ngl", "99", "-c", "512",
            "--temp", "0.7", "--top-k", "20", "--top-p", "0.8",
            "--port", "8137", "--host", "127.0.0.1"]
    t0 = time.time()
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, cwd=LL)
    if not wait_ready(proc):
        print("  启动失败")
        proc.kill()
        continue
    print(f"  启动 {time.time()-t0:.1f}s")
    print()
    print(f"  {'类型':<8} {'耗时':>7}  译文")
    print("  " + "-" * 72)
    lat = []
    for name, s in SENTS:
        try:
            out, dt = ask(PROMPT.format(src=s))
            lat.append(dt)
            print(f"  {name:<8} {dt:>6.2f}s  {out[:56]}")
        except Exception as e:
            print(f"  {name:<8}   失败  {type(e).__name__}: {str(e)[:50]}")
    if lat:
        lat.sort()
        print(f"\n  → 中位 {statistics.median(lat):.2f}s　最小 {min(lat):.2f}s　最大 {max(lat):.2f}s")
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()
    print()

# 对照：远端
print("=" * 86)
print("对照：远端 qwen-mt-flash")
print("=" * 86)
if KEY:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from llm_translate import LLMTranslator, DASHSCOPE_BASE
    t = LLMTranslator(DASHSCOPE_BASE, "qwen-mt-flash", KEY, "openai",
                      no_system=True)
    lat = []
    for name, s in SENTS:
        t0 = time.time()
        out = t(f"把下面这段文本翻译成简体中文，只输出译文：\n{s}",
                target="Simplified Chinese")
        dt = time.time() - t0
        lat.append(dt)
        print(f"  {name:<8} {dt:>6.2f}s  {out[:56]}")
    lat.sort()
    print(f"\n  → 中位 {statistics.median(lat):.2f}s")
else:
    print("  没有 key")
