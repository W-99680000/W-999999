# -*- coding: utf-8 -*-
"""本地 HY-MT vs 远端 qwen-mt：用**真实越南语音频**逐句对照质量。

为什么必须用真实音频：
  前面所有对比都只用了我手写的 4 个句子，而且发现本地模型
  在越南语上"流畅但意思错"（tình → 情郎）。
  一句样本不足以下结论，必须用**成段的真实素材**逐句对照。

流程（复现真实链路，只是把翻译换成两边）：
  ① 本地 whisper GPU 识别越南语音频 → 原文
  ② 按 4 秒切段（和主程序一致）
  ③ 同一批原文分别给：本地 HY-MT（模板格式）/ 远端 qwen-mt-flash
  ④ 逐句打印对照，人眼判断 + 客观统计

判据（不只看中文占比，那已经证明会骗人）：
  · 中文占比（过滤"输出原文/英文"这类硬失败）
  · 长度比（译文/原文，检测"漏译"与"胡编"两个极端）
  · 与远端译文的字符重合度（作为参考锚点，不是标准答案）
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
MODEL = r"F:\hh_si_pro\hh-translate-model\hh-translate-base.gguf"
PORT = 8143
BASE = f"http://127.0.0.1:{PORT}"

BOS = "<\uff5chy_begin\u2581of\u2581sentence\uff5c>"
USER = "<\uff5chy_User\uff5c>"
ASST = "<\uff5chy_Assistant\uff5c>"
INSTR = "把下面的文本翻译成中文，只输出译文：\n{src}"

CJK = re.compile(r"[\u4e00-\u9fff]")


def cjk_ratio(s):
    return len(CJK.findall(s)) / max(1, len(s.strip())) if s else 0.0


# ── ① 本地 GPU 识别越南语素材 ──────────────────────────────
import numpy as np
from live_subtitle_v2 import to_mono16k, TARGET_RATE

print("=" * 96)
print("① 用本地 GPU whisper 识别真实越南语音频")
print("=" * 96)
from faster_whisper import WhisperModel
from gpu_setup import enable_cuda_dlls
enable_cuda_dlls()
m = WhisperModel("medium", device="cuda", compute_type="float16")

SEGS_SRC = []
for name, rel in (("vi_sample", "samples/vi_sample.wav"),
                  ("vi_long", "samples/vi_long.wav")):
    p = os.path.join(HERE, rel)
    if not os.path.exists(p):
        continue
    w = wave.open(p, "rb")
    raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    a = to_mono16k(raw, w.getframerate(), w.getnchannels())
    print(f"\n  【{name}】{len(a)/TARGET_RATE:.1f} 秒")
    step = int(4 * TARGET_RATE)
    for i in range(0, len(a) - step + 1, step):
        segs, info = m.transcribe(a[i:i+step], beam_size=5, vad_filter=True,
                                  condition_on_previous_text=False,
                                  temperature=0.0)
        txt = " ".join(s.text.strip() for s in segs).strip()
        if txt:
            SEGS_SRC.append(txt)
            print(f"    {i/TARGET_RATE:5.1f}s [{info.language}] {txt[:70]}")
del m
print(f"\n  共 {len(SEGS_SRC)} 段原文")

# ── ② 本地 HY-MT（模板格式）──────────────────────────────
print()
print("=" * 96)
print("② 本地 HY-MT（正确模板格式）")
print("=" * 96)
args = [os.path.join(LL, "llama-server.exe"), "--model", MODEL,
        "-ngl", "99", "-c", "1024", "--port", str(PORT), "--host", "127.0.0.1"]
proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, cwd=LL)
t0 = time.time()
ok = False
while time.time() - t0 < 120:
    if proc.poll() is not None:
        break
    try:
        with urllib.request.urlopen(BASE + "/health", timeout=2) as r:
            if r.status == 200:
                ok = True
                break
    except Exception:
        pass
    time.sleep(0.4)
if not ok:
    print("  server 启动失败")
    proc.kill()
    sys.exit(1)


def local_mt(src):
    p = BOS + USER + INSTR.format(src=src) + ASST
    body = json.dumps({"prompt": p, "n_predict": 120, "temperature": 0.0,
                       "top_k": 1, "top_p": 1.0, "stream": False},
                      ensure_ascii=False).encode()
    req = urllib.request.Request(BASE + "/completion", data=body,
                                headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=60) as r:
        j = json.loads(r.read().decode("utf-8", "replace"))
    return (j.get("content") or "").strip(), time.time() - t0


local_out, local_lat = [], []
for i, s in enumerate(SEGS_SRC, 1):
    out, dt = local_mt(s)
    local_out.append(out)
    local_lat.append(dt)
    print(f"\n  [{i}] {dt:.2f}s　原: {s[:66]}")
    print(f"      本地: {out[:66]}")
proc.terminate()
try:
    proc.wait(timeout=10)
except Exception:
    proc.kill()

# ── ③ 远端 qwen-mt ───────────────────────────────────────
print()
print("=" * 96)
print("③ 远端 qwen-mt-flash")
print("=" * 96)
from llm_translate import (LLMTranslator, DASHSCOPE_BASE,
                           _read_dashscope_key as read_key)
t = LLMTranslator(DASHSCOPE_BASE, "qwen-mt-flash", read_key(), "openai",
                  no_system=True)
remote_out, remote_lat = [], []
for i, s in enumerate(SEGS_SRC, 1):
    t0 = time.time()
    out = t(INSTR.format(src=s), target="Simplified Chinese")
    remote_out.append(out)
    remote_lat.append(time.time() - t0)
    print(f"\n  [{i}] 原: {s[:66]}")
    print(f"      远端: {out[:66]}")

# ── ④ 统计 ───────────────────────────────────────────────
print()
print("=" * 96)
print("④ 客观统计")
print("=" * 96)
print(f"  {'段':>3} {'本地中文%':>9} {'远端中文%':>9} {'本地/原长度':>11} "
      f"{'远端/原长度':>11} {'本地s':>7} {'远端s':>7}")
for i, src in enumerate(SEGS_SRC):
    lo, ro = local_out[i], remote_out[i]
    print(f"  {i+1:>3} {cjk_ratio(lo):>8.0%} {cjk_ratio(ro):>8.0%} "
          f"{len(lo)/max(1,len(src)):>11.2f} {len(ro)/max(1,len(src)):>11.2f} "
          f"{local_lat[i]:>7.2f} {remote_lat[i]:>7.2f}")
print()
if local_lat and remote_lat:
    print(f"  延迟：本地中位 {statistics.median(local_lat):.2f}s　"
          f"远端中位 {statistics.median(remote_lat):.2f}s")
    bad = sum(1 for o in local_out if cjk_ratio(o) < 0.3)
    print(f"  本地硬失败（中文占比<30%）: {bad}/{len(local_out)} 段")
