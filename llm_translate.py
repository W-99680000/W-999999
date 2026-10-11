# -*- coding: utf-8 -*-
"""llm_translate.py —— 本地/远端 LLM 翻译后端（一条代码走两种端点）

为什么需要它（以及为什么不是"Ollama 专用"）
============================================
原计划照抄 Whisper-Ollama-Translator 的 Ollama 路线，但实测本机：
  · 没有 ollama 命令
  · 11434 端口没开
  · 没有任何其他本地模型服务
所以只用 Ollama 的话，这个功能在本机**一次都跑不起来**。

而"任意 OpenAI 兼容端点"覆盖面大得多，且**已经可用**（实测）：
    DeepSeek  https://api.deepseek.com      0.68s，译文质量好
    Ollama    http://127.0.0.1:11434        本地，离线
    vLLM / LM Studio / one-api / 各类中转   同协议
两者协议不同但语义一致，所以这里用一个后端 + 两种 adapter。

借鉴 Whisper-Ollama-Translator 的几点（都是它踩过的坑）
  · 用 127.0.0.1 而不是 localhost —— Windows 上 localhost 有约 2 秒解析延迟
  · temperature 0.1（翻译要稳定，不要发挥）
  · 只取第一行：小模型常在换行后写解释
  · 剥掉模型自己加的前缀（"翻译："、"中文：" 之类）
  · num_ctx 限制上下文（Ollama 专有字段）
以及它一条实测结论：**碎片输入会让小模型更慢更啰嗦**（它会试图"解释"
乱码）。所以这里加了 min_chars：太短的文本根本不送 LLM。
"""
from __future__ import annotations

import json
import os
import re
import ssl
import time
import urllib.request

# 只挡掉"不值得送"的极短碎片与噪声。
#
# 我第一版把门槛设在 12 字符，理由是借来的那条"碎片会让小模型更啰嗦"。
# **实测发现这个门槛设错了方向**：
#     长句  走 LLM 0.49~0.78s
#     短句  退回 Google **7.19s / 8.73s**（免费端点对短词的往返并不便宜）
# 短文本的 LLM 往返和长文本几乎一样（都在 0.5s 量级，提示词是常数开销），
# 所以"短句退回 Google"既没省时间、质量还更差。
# 现在只挡 2 字符以内（纯噪声/单字母），其余一律走 LLM。
# 真正的短句加速靠 PHRASEBOOK 与 LRU 缓存（都是零网络）。
MIN_CHARS = 3

# ── 阿里云百炼 qwen-mt（实测最优，作为默认后端）────────────────
# 为什么用它：这是**翻译专用**模型（基于 Qwen3），不是通用对话模型。
# 实测对比（同一批真实字幕句，本项目实际在看的语种）：
#
#   模型            中位延迟   越南语人名「doãn chung dũng」
#   qwen-mt-flash   0.36s     段忠勇        ✓  ← 用这个
#   qwen-mt-lite    0.33s     段忠勇        ✓  但语言略生硬
#   qwen-mt-plus    0.34s     杜安忠勇      ✗  不稳定
#   deepseek-chat   0.68s     Doãn Chung Dũng / 尹中勇  ✗
#
# qwen-mt 的两个关键优势：
#   ① **快 2 倍**（0.36s vs 0.68s）—— 翻译在关键路径上，直接减延迟
#   ② **越南语人名/地名按中文习惯译**（段忠勇），而 DeepSeek 要么留
#      原文要么猜错（尹中勇）。新闻字幕这是硬需求。
# 另外 qwen-mt 支持 translation_options 的术语定制与领域提示，
# 通用模型没有（见 _bench_qwen_mt.py）。
#
# 地域：key 是**按地域**签发的。北京节点最便宜（0.7/1.95 元每百万
# tokens），拿北京 key 打新加坡 endpoint 会 401（实测过）。
DASHSCOPE_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DASHSCOPE_MODEL = "qwen-mt-flash"
DASHSCOPE_KEYS_FILE = os.path.join(os.path.expanduser("~"), ".dsh",
                                   ".dashscope")


def _read_dashscope_key():
    """从 ~/.dsh/.dashscope 读 key（单独一个文件，不要落进仓库）。

    不写进 .credentials.yaml 是因为那是 DSH 自己管理的文件，我不该动。
    一行一条，`#` 开头为注释。
    """
    try:
        with open(DASHSCOPE_KEYS_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    return line
    except Exception:
        pass
    return ""


SYSTEM = ("You are a professional subtitle translator. "
          "Output ONLY the translation. "
          "No explanation, no quotes, no prefixes, no original text.")

# 模型偶尔给自己加的前缀，要剥掉
_PREFIXES = ("翻译：", "翻译:", "中文：", "中文:", "译文：", "译文:",
             "中文翻译：", "中文翻译:", "Translation:", "Translation：",
             "简体中文：", "简体中文:")


def looks_complex(text, min_chars=MIN_CHARS):
    """这段文本值不值得送 LLM？

    现在只做一件事：挡掉极短碎片与噪声。
    我第一版按"碎片会让小模型更啰嗦"设了 12 字符门槛，**方向是错的** ——
    实测短句退回 Google 要 7.19s / 8.73s，而短文本走 LLM 只要 ~0.5s
    （LLM 的往返成本是提示词常数开销，与文本长度几乎无关）。
    所以短文本既不该退回 Google、也不该另开一条路，直接送 LLM 就好。
    真正的短句加速交给 PHRASEBOOK 与 LRU 缓存（都是零网络）。
    """
    return len((text or "").strip()) >= min_chars


def _clean(s):
    """剥掉模型加的前缀与包裹引号。"""
    s = (s or "").strip()
    s = s.split("\n")[0].strip()          # 只取第一行
    for p in _PREFIXES:
        if s.startswith(p):
            s = s[len(p):].strip()
    s = s.strip().strip('"').strip("'").strip("“”").strip()
    return s


class LLMTranslator:
    """一个后端，两种协议。

    kind="openai"  → POST {base}/chat/completions  （DeepSeek / vLLM / 中转）
    kind="ollama"  → POST {base}/api/generate      （Ollama 原生）
    """

    def __init__(self, base_url, model, api_key=None, kind="openai",
                 timeout=20.0, temperature=0.1, num_ctx=1024,
                 no_system=False):
        self.base = (base_url or "").rstrip("/")
        self.model = model
        self.key = api_key or ""
        self.kind = kind
        self.timeout = float(timeout)
        self.temperature = float(temperature)
        self.num_ctx = int(num_ctx)
        # 用 127.0.0.1 而不是 localhost：Windows 上后者有解析延迟（借鉴）
        self.base = self.base.replace("//localhost:", "//127.0.0.1:")
        # qwen-mt 不接受 system 角色（实测 400），构造时置 True
        self.no_system = bool(no_system)
        self.ok = bool(self.base and self.model)
        self.calls = self.fails = 0
        self.t_last = 0.0

    # ── 请求体 ───────────────────────────────────────────────
    def _payload(self, text, target):
        # **qwen-mt 只接受 user / assistant 角色，发 system 会 400。**
        # 实测报错：Role must be in [user, assistant].
        # 这是翻译专用模型的特性 —— 翻译指令本身就该写在 user 里，
        # 它不需要（也不支持）system prompt。
        # 我第一版统一发 system+user，于是 qwen-mt 全部返回空
        # （异常被吞掉 → 空串），表面上"模式选对了但没译文"。
        prompt = (f"Translate the following text into {target}. "
                  f"Output ONLY the translation.\n\n{text}")
        if self.kind == "ollama":
            return {"model": self.model, "prompt": prompt, "stream": False,
                    "options": {"temperature": self.temperature,
                                "num_ctx": self.num_ctx,
                                "stop": ["\n"]}}
        msgs = [{"role": "user", "content": prompt}]
        if not self.no_system:
            msgs.insert(0, {"role": "system", "content": SYSTEM})
        return {"model": self.model, "stream": False,
                "temperature": self.temperature, "max_tokens": 220,
                "messages": msgs}

    def _url(self):
        return (f"{self.base}/api/generate" if self.kind == "ollama"
                else f"{self.base}/chat/completions")

    def _extract(self, j):
        if self.kind == "ollama":
            return j.get("response", "")
        try:
            return j["choices"][0]["message"]["content"]
        except Exception:
            return ""

    # ── 主入口 ───────────────────────────────────────────────
    def __call__(self, text, target="Simplified Chinese"):
        if not self.ok or not (text or "").strip():
            return ""
        body = json.dumps(self._payload(text, target),
                          ensure_ascii=False).encode("utf-8")
        hdr = {"Content-Type": "application/json"}
        if self.kind == "openai" and self.key:
            hdr["Authorization"] = f"Bearer {self.key}"
        req = urllib.request.Request(self._url(), data=body, headers=hdr)
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout,
                                        context=ctx) as r:
                j = json.loads(r.read().decode("utf-8", "replace"))
            self.calls += 1
            self.t_last = time.time() - t0
            return _clean(self._extract(j))
        except Exception:
            self.fails += 1
            return ""

    def status(self):
        if not self.ok:
            return "LLM=未配置"
        return (f"LLM={self.kind}:{self.model}　调用 {self.calls}"
                f"　失败 {self.fails}　上次 {self.t_last:.2f}s")


def from_env_or_args(base_url=None, model=None, kind=None):
    """按命令行参数 / 环境变量 / 本机常见端点，尽力找一个可用的 LLM。

    优先级：
      ① 显式给的 --llm-base / --llm-model
      ② 环境变量 LLM_BASE / LLM_MODEL / LLM_KEY（兼容 OPENAI_*、DASHSCOPE_API_KEY）
      ③ **阿里云百炼 qwen-mt-flash**（有 DASHSCOPE_API_KEY 时）
         —— 实测最优，见下面 DASHSCOPE_* 常量的注释
      ④ 本机 Ollama（127.0.0.1:11434）
      ⑤ DeepSeek（若 .dsh 凭据里或环境变量里有 key）
    找不到就返回 None —— 调用方退回 Google，不要因为没配 LLM 就报错。
    """
    base = base_url or os.environ.get("LLM_BASE") or os.environ.get("OPENAI_BASE_URL")
    mdl = model or os.environ.get("LLM_MODEL") or os.environ.get("OPENAI_MODEL")
    key = os.environ.get("LLM_KEY") or os.environ.get("OPENAI_API_KEY") or ""
    k = kind

    if base and mdl:
        return LLMTranslator(base, mdl, key, k or "openai")

    # ③ 阿里云百炼 qwen-mt-flash（实测最快最准，优先）
    ds_key = os.environ.get("DASHSCOPE_API_KEY") or _read_dashscope_key()
    if ds_key:
        return LLMTranslator(DASHSCOPE_BASE,
                             mdl or DASHSCOPE_MODEL, ds_key, "openai",
                             no_system=True)

    # ④ 本机 Ollama
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags",
                                    timeout=2) as r:
            tags = json.loads(r.read().decode("utf-8", "replace"))
        names = [m.get("name", "") for m in tags.get("models", [])]
        if names:
            return LLMTranslator("http://127.0.0.1:11434", names[0],
                                 None, "ollama")
    except Exception:
        pass

    # ⑤ DeepSeek（读 dsh 凭据）
    if not key:
        try:
            import re as _re
            p = os.path.join(os.path.expanduser("~"), ".dsh", ".credentials.yaml")
            t = open(p, encoding="utf-8", errors="replace").read()
            m = _re.search(r"DEEPSEEK_API_KEY:\s*(\S+)", t)
            if m:
                key = m.group(1).strip()
        except Exception:
            pass
    if key:
        return LLMTranslator("https://api.deepseek.com",
                             mdl or "deepseek-chat", key, "openai")
    return None
