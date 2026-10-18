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
import struct
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
    # 7B（HY-MT1.5-7B-GGUF，Q8 约 4.4GB）—— 之前漏了这条，界面误报
    # "找不到 gguf 模型文件"（文件其实在 F:，只是候选表没收录）
    r"F:\hh_si_pro\hh-translate-model\hh-translate-large.gguf",
    os.path.join(HERE, "models", "hh-translate-base.gguf"),
]

# 项目 models/ 和 F: 的 HY-MT 目录里的所有 gguf 都算候选
# （model_manager 下载到 models/；已有的一代/7B 装在 F:，别写死漏项）
try:
    import glob as _glob
    for _d in (os.path.join(HERE, "models"),
               r"F:\hh_si_pro\hh-translate-model"):
        for _p in sorted(_glob.glob(os.path.join(_d, "*.gguf"))):
            if _p not in MODEL_CANDIDATES:
                MODEL_CANDIDATES.append(_p)
except Exception:
    pass

# ── 两代模型用**不同的 token 体系**，模板必须按模型选 ────────────
# 这是实测踩出来的：我以为 HY-MT 家族只有一个模板，把 1.8B 的标记发给 7B，
# 结果 7B 看起来"更差"（空输出、中英混杂、复读）。实际是**发错了 token**。
#
# 1.8B（hy_ 系列标记）
#     <|hy_begin_of_sentence|><|hy_User|>{user}<|hy_Assistant|>
#     bos=120000  eos=120020  pad=120002
# 7B（startoftext / extra_N 系列）
#     user 轮      → <|startoftext|>{content}<|extra_0|>
#     system 轮    → <|startoftext|>{content}<|extra_4|>
#     assistant 轮 → {content}<|eos|>
#     bos=127958  eos=127960  pad=127961
#
# 两套都是从各自 GGUF 的 tokenizer.chat_template 与词表读出来的。
BOS = "<\uff5chy_begin\u2581of\u2581sentence\uff5c>"      # 1.8B
USER = "<\uff5chy_User\uff5c>"
ASST = "<\uff5chy_Assistant\uff5c>"
SOT = "<|startoftext|>"                                    # 7B
EX0 = "<|extra_0|>"

MODEL_FAMILIES = {
    "hy18": lambda body: BOS + USER + body + ASST,
    "hy7": lambda body: SOT + body + EX0,
}


def _skip_gguf_value(f, vt):
    """跳过一项 GGUF 元数据值（只为找 bos_token_id，别的不需要）。"""
    sizes = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8,
             12: 8}
    if vt == 8:
        n = struct.unpack("<Q", f.read(8))[0]
        f.seek(n, 1)
    elif vt == 9:
        et = struct.unpack("<I", f.read(4))[0]
        n = struct.unpack("<Q", f.read(8))[0]
        if et == 8:
            for _ in range(n):
                ln = struct.unpack("<Q", f.read(8))[0]
                f.seek(ln, 1)
        else:
            f.seek(n * sizes.get(et, 0), 1)
    else:
        f.seek(sizes.get(vt, 0), 1)


def detect_family(path):
    """判断模型是 1.8B 一代还是 7B 一代。

    看 **bos token id** —— 两代之间最明确的区别（120000 vs 127958）。
    读不到就按文件大小兜底（7B 的 Q4 约 4.4GB，1.8B 约 1.1GB）。
    """
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"GGUF":
                return "hy18"
            f.read(4)
            f.read(8)                              # n_tensors
            n_kv = struct.unpack("<Q", f.read(8))[0]
            for _ in range(n_kv):
                n = struct.unpack("<Q", f.read(8))[0]
                key = f.read(n).decode("utf-8", "replace")
                vt = struct.unpack("<I", f.read(4))[0]
                if key == "tokenizer.ggml.bos_token_id" and vt == 4:
                    v = struct.unpack("<I", f.read(4))[0]
                    return "hy7" if v > 126000 else "hy18"
                _skip_gguf_value(f, vt)
    except Exception:
        pass
    try:
        return "hy7" if os.path.getsize(path) > 3_000_000_000 else "hy18"
    except Exception:
        return "hy18"


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
        # 模型代次决定用哪套 token 标记（见 MODEL_FAMILIES 的说明）
        self.family = detect_family(self.model) if self.model else "hy18"
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
            cwd=os.path.dirname(self.bin),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
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
        payload = {"prompt": MODEL_FAMILIES[self.family](prompt),
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
                f"（{self.family}）"
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
