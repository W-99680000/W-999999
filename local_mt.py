# -*- coding: utf-8 -*-
"""local_mt.py —— 本地翻译（HY-MT + llama.cpp，完全离线）

为什么能做
==========
F 盘那个同声传译软件包里，除了 GGUF 模型，**还带了现成的 llama.cpp
Windows Vulkan 构建**（llama-server.exe + ggml-vulkan.dll 等）。
这就是"llama-cpp-python 编译失败"的解法 —— 不需要 CUDA Toolkit，
Vulkan 后端在 RTX 4070 上直接可用。

    实测：模型 1075 MB 上显存，生成 151 token/s

模型：HY-MT（腾讯混元翻译专用）
    base  1.8B，1081 MB      实测 0.15s/段
    large HY-MT1.5，4410 MB  支持 36 语言

接口：**必须用它的 chat 模板**（从 GGUF 的 tokenizer.chat_template 读的）
=====================================================================
    {% if system %}<｜hy_begin▁of▁sentence｜>{{ sys }}<｜hy_place▁holder▁no▁3｜>
    {% else %}    <｜hy_begin▁of▁sentence｜>
    {% endif %}
    user      → <｜hy_User｜>{{ content }}
    assistant → <｜hy_Assistant｜>{{ content }}<｜hy_place▁holder▁no▁2｜>
    生成时追加 <｜hy_Assistant｜>

词表里的实际 token id：
    120000 <｜hy_begin▁of▁sentence｜>   句首
    120007 <｜hy_Assistant｜>             生成起点
    120020 <｜hy_place▁holder▁no▁2｜>    assistant 段结束
    120002 <｜hy_▁pad▁｜>                 pad

**这一步不能省。** 实测：裸提示词（不发这些标记）越南语输出 0% 中文
（`thep, thep, thep...` 这种胡言乱语）；发对标记后 100% 中文。
因为那是**训练分布外的畸形输入**。

质量（实测，真实越南语音频 6 段，对照远端 qwen-mt）
==================================================
    本地  中文占比与远端相当，通顺度略好，专有名词偶尔泛化
          延迟中位 0.15s（比远端快 4 倍），硬失败 0/6
    远端  专有名词更准（"số phét"→"指纹" vs 本地"各种证据"）
    结论  各有胜负，都可接受。本地完全离线、零成本。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

# llama.cpp 二进制默认位置（从 F 盘软件包里解出来的）
LLAMA_DIRS = [
    os.environ.get("LLAMA_DIR", ""),
    r"D:\NEW WORK\work\llamacpp",
    os.path.join(HERE, "llamacpp"),
]

# 模型默认位置（F 盘自带的翻译模型）
MODEL_CANDIDATES = [
    os.environ.get("HYMT_MODEL", ""),
    r"F:\hh_si_pro\hh-translate-model\hh-translate-base.gguf",
    r"F:\hh_si_pro\hh-translate-model\hh-translate-small.gguf",
    r"F:\hh_si_pro\hh-translate-model\hh-translate-medium.gguf",
    os.path.join(HERE, "models", "hh-translate-base.gguf"),
]

# 特殊 token（从 GGUF chat_template 与词表读出来的，不要改）
BOS = "<\uff5chy_begin\u2581of\u2581sentence\uff5c>"
USER = "<\uff5chy_User\uff5c>"
ASST = "<\uff5chy_Assistant\uff5c>"

# ── 官方提示词模板（来源：Tencent-Hunyuan/HY-MT README，非推测）──────
# 官方区分两种，**不能混用**：
#   中↔XX（含中文，用中文提示词）→ 下面这个
#   XX↔XX（不含中文，用英文提示词）→ TPL_XX
# 实测差异（_bench_hymt_official.py，真实越南语 4 段）：
#     我的自造模板 + 我的参数   0.49s
#     官方模板     + 我的参数   0.49s
#     **官方模板 + 官方参数     0.25s（快 2~4 倍）**
TPL_ZH = ("将以下文本翻译为{target}，注意只需要输出翻译后的结果，"
          "不要额外解释：\n\n{src}")
TPL_XX = ("Translate the following segment into {target}, "
          "without additional explanation.\n\n{src}")

# 术语干预（官方预留的能力）。实测**真的生效**：
#     "số phét"     无术语→"各种证据"   注入后→"指纹"        ✓
#     "doãn chung dũng" 无术语→"邓忠义"  注入后→"段忠勇"      ✓
#     "quảng ninh"  无术语→"广宁"      注入后→"广宁省"      ✓
# 注意它救不了**源文本自身就是错的**情况（如 ASR 错字）—— 上游错则下游难救。
TPL_TERM = ("参考下面的翻译：\n{src_term} 翻译成 {tgt_term}\n\n" + TPL_ZH)

# 官方推荐推理参数（README 给的 JSON）。
# 我原来用 top_p=1.0 且没开 repetition_penalty —— 那很可能就是
# 复读（"thep thep thep..."）的来源。实测官方参数还快 2~4 倍。
OFFICIAL_PARAMS = {
    "temperature": 0.7,
    "top_k": 20,
    "top_p": 0.6,
    "repeat_penalty": 1.05,
}
# 兼容旧名（我最初的参数）
DEFAULT_PROMPT = TPL_ZH


def find_binary(name="llama-server.exe"):
    for d in LLAMA_DIRS:
        if d and os.path.isfile(os.path.join(d, name)):
            return os.path.join(d, name)
    return None


def find_model():
    for p in MODEL_CANDIDATES:
        if p and os.path.isfile(p):
            return p
    return None


def available():
    """本地翻译是否可用（二进制 + 模型都在）。返回 (ok, 原因)。"""
    if find_binary() is None:
        return False, "找不到 llama-server.exe（设 LLAMA_DIR 环境变量）"
    if find_model() is None:
        return False, "找不到 HY-MT 模型（设 HYMT_MODEL 环境变量）"
    return True, ""


class LocalMT:
    """管理 llama-server 子进程，并按 HY-MT 的模板发请求。"""

    def __init__(self, model=None, port=8145, n_ctx=1024, n_gpu_layers=99,
                 prompt_template=None, start_timeout=180, target="中文",
                 params=None, terms=None):
        self.model = model or find_model()
        self.bin = find_binary()
        self.port = int(port)
        self.n_ctx = int(n_ctx)
        self.ngl = int(n_gpu_layers)
        self.tpl = prompt_template or TPL_ZH
        self.target = target or "中文"
        self.params = dict(OFFICIAL_PARAMS)
        if params:
            self.params.update(params)
        # 术语表 [(源词, 目标译法)]。注入时按"源词出现在文本里"逐条加。
        self.terms = list(terms or [])
        self.start_timeout = start_timeout
        self.proc = None
        self.ok = bool(self.bin and self.model)
        self.calls = self.fails = 0
        self.t_last = 0.0
        self.t_start = 0.0
        self.last_err = ""
        self.terms_used = 0

    @property
    def base(self):
        return f"http://127.0.0.1:{self.port}"

    def start(self):
        if not self.ok:
            return False
        if self.proc and self.proc.poll() is None:
            return True
        args = [self.bin, "--model", self.model, "-ngl", str(self.ngl),
                "-c", str(self.n_ctx), "--port", str(self.port),
                "--host", "127.0.0.1"]
        t0 = time.time()
        self.proc = subprocess.Popen(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd=os.path.dirname(self.bin))
        while time.time() - t0 < self.start_timeout:
            if self.proc.poll() is not None:
                self.last_err = f"llama-server 退出码 {self.proc.returncode}"
                return False
            try:
                with urllib.request.urlopen(self.base + "/health",
                                            timeout=2) as r:
                    if r.status == 200:
                        self.t_start = time.time() - t0
                        return True
            except Exception:
                pass
            time.sleep(0.4)
        self.last_err = f"启动超时（{self.start_timeout}s）"
        return False

    def stop(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=10)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        self.proc = None

    def __call__(self, text, target=None):
        """翻译一段。target 参数只为与远端译器接口对齐（这里忽略）。"""
        text = (text or "").strip()
        if not text or not self.ok:
            return ""
        # 术语干预：只注入**在本文本里出现**的词条（官方模板）
        hits = [(s, t) for s, t in self.terms if s and s.lower() in text.lower()]
        if hits:
            self.terms_used += len(hits)
            body = "\n".join(TPL_TERM.format(src_term=s, tgt_term=t,
                                            target=self.target, src=text)
                             for s, t in hits)
            prompt = body
        else:
            prompt = self.tpl.format(target=self.target, src=text)
        payload = {"prompt": BOS + USER + prompt + ASST,
                   "n_predict": max(64, 6 * len(text)),
                   "stream": False}
        payload.update(self.params)
        body = json.dumps(payload, ensure_ascii=False).encode()
        req = urllib.request.Request(self.base + "/completion", data=body,
                                    headers={"Content-Type": "application/json"})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                j = json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            self.fails += 1
            self.last_err = f"{type(e).__name__}: {str(e)[:100]}"
            return ""
        self.calls += 1
        self.t_last = time.time() - t0
        out = (j.get("content") or "").strip()
        # 只取第一行（模型偶尔会续写）
        return out.split("\n")[0].strip()

    def status(self):
        if not self.ok:
            return "本地翻译=不可用"
        return (f"本地翻译=HY-MT {os.path.basename(self.model)}"
                f"　启动 {self.t_start:.1f}s　调用 {self.calls}"
                f"　失败 {self.fails}　上次 {self.t_last:.2f}s")


def from_args(model=None, port=8145, n_ctx=1024, terms=None):
    ok, why = available()
    if not ok:
        print(f"  本地翻译不可用：{why}")
        return None
    m = LocalMT(model=model, port=port, n_ctx=n_ctx, terms=terms)
    print(f"  启动本地翻译 {os.path.basename(m.model)} …")
    if not m.start():
        print(f"  启动失败：{m.last_err}")
        m.stop()
        return None
    print(f"  {m.status()}")
    return m


if __name__ == "__main__":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    ok, why = available()
    print(f"  available: {ok} {why}")
    print(f"  binary: {find_binary()}")
    print(f"  model : {find_model()}")
    if ok:
        m = from_args()
        if m:
            for s in ("I never thought I would see you again.",
                      "tình quảng ninh đưa ra xét sử bị cáo doãn chung dũng",
                      "Я только на это рассчитываю на самом деле, здесь женщина."):
                out = m(s)
                print(f"    {m.t_last:5.2f}s  {s[:44]}")
                print(f"            → {out[:56]}")
            m.stop()
