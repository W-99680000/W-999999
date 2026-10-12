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

import http.client
import json
import os
import re
import ssl
import time
import urllib.parse
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


# ── 合并翻译（一次调用译多段）──────────────────────────────────
#
# 为什么需要：实测（_bench_mt_cost.py）每次调用有 **0.57 秒固定开销**，
# 而真实翻译计算几乎为 0 ——
#     qwen-mt-flash: 耗时 ≈ 0.770ms/字符 × 长度 + 0.569s
#     5 字符 0.554s　→　133 字符 0.660s（长 27 倍，耗时只涨 19%）
# 所以同一批里多段分开译，等于把固定开销付了多次。
#
# 什么格式能用（_bench_mt_batch.py 实测，4 段）：
#     编号行   解析 1/4  ✗  "1. 好吧。"
#     XML标记  解析 1/4  ✗  "<t>好的。</t>"
#     分隔符   解析 4/4  ✓
#     JSON数组 解析 4/4  ✓
# **qwen-mt 是纯翻译引擎、不执行指令** —— 它把"编号行/XML"当成待翻译的
# 正文，译了第一行就停。而分隔符与 JSON 结构被当作文本保留下来。
# 所以只能用分隔符，不能用"请按编号输出"这类指令式提示。
#
# 效果：4 段合并 0.73s vs 逐条 2.36s（每段 0.59s → 0.18s）。
# **分隔符必须是纯字母。** 我第一版用 |||，模型输出里确实保留了，
# 但**我们自己的 clean_translation 把它删掉了** ——
# 那个函数用白名单过滤（只留 CJK + 拉丁字母 + 数字），
# 竖线/at 号/百分号/段落分隔符全在白名单外。
# 实测（_bench_sep 系列）：
#     'XXSEPXX' 保留 ✓   'SEP' 保留 ✓   'ZzQq' 保留 ✓
#     '|||' '@@@' '%%%' '§§§' '\u2028' 全被删 ✗
# 所以用什么分隔符的决定性约束不是模型，是**我们自己后处理的白名单**。
BATCH_SEP = "XXSEPXX"

BATCH_PROMPT = (
    "把下面用 {sep} 分隔的每一段翻译成{target}。"
    "输出同样用 {sep} 分隔，段数必须与输入完全一致，只输出译文。\n\n{body}"
)


def translate_batch(translator, texts, target="Simplified Chinese",
                    max_items=6):
    """一次调用翻译多段；返回与 texts **等长**的列表。

    失败（段数对不上/异常）就退回**逐条翻译** —— 宁可慢，不能错位。
    调用方按 zip 取用，所以返回长度必须严格等于输入长度。
    """
    items = [("" if x is None else str(x)) for x in texts]
    if not items:
        return []
    if len(items) == 1 or max_items < 2 or len(items) > max_items:
        return [translator(x, target=target) if x else "" for x in items]
    body = (" " + BATCH_SEP + " ").join(items)
    prompt = BATCH_PROMPT.format(sep=BATCH_SEP, target=target, body=body)
    try:
        raw = translator(prompt, target=target)
    except Exception:
        raw = ""
    if raw and BATCH_SEP in raw:
        parts = [p.strip() for p in raw.split(BATCH_SEP)]
        parts = [p for p in parts if p]
        if len(parts) == len(items):
            return parts
    # 退回逐条（并留下痕迹，便于诊断为什么合并失败）
    # **必须打出来，不能只在计数器里** —— 这个项目踩过同类坑：
    # 分隔符被后处理删掉时合并翻译静默失效，表面一切正常，
    # 只是翻译变慢了。退回原因分三种：空返回 / 无分隔符 / 段数对不上。
    translator.batch_fallbacks = getattr(translator, "batch_fallbacks", 0) + 1
    n_fb = translator.batch_fallbacks
    if n_fb <= 3 or n_fb % 20 == 0:
        if not raw:
            why = "空返回"
        elif BATCH_SEP not in raw:
            why = f"输出无分隔符（{raw[:40]!r}…）"
        else:
            why = f"段数对不上（出 {len(parts)} / 入 {len(items)}）"
        print(f"  ！合并翻译第 {n_fb} 次退回逐条：{why}", flush=True)
    return [translator(x, target=target) if x else "" for x in items]


class LLMTranslator:
    """一个后端，两种协议。

    kind="openai"  → POST {base}/chat/completions  （DeepSeek / vLLM / 中转）
    kind="ollama"  → POST {base}/api/generate      （Ollama 原生）

    **长连接**：每次调用新开 TCP+TLS 的代价是 qwen-mt 那 0.57s 固定开销
    的重要组成部分（握手一次约 0.1~0.3s）。这里用 http.client 保持连接，
    只在连接断裂时重建并重试一次。
    """

    def __init__(self, base_url, model, api_key=None, kind="openai",
                 timeout=8.0, temperature=0.1, num_ctx=1024,
                 no_system=False, terms=None):
        self.base = (base_url or "").rstrip("/")
        self.model = model
        self.key = api_key or ""
        self.kind = kind
        # timeout 从 20s 收紧到 8s：显示层 4s 就放弃队头了，20s 的超时
        # 意味着"显示放弃后翻译线程还在干等十几秒"，兜底（google-free）
        # 永远来不及生效。8s 内失败 → 立刻走兜底，字幕不断。
        self.timeout = float(timeout)
        self.temperature = float(temperature)
        self.num_ctx = int(num_ctx)
        # 用 127.0.0.1 而不是 localhost：Windows 上后者有解析延迟（借鉴）
        self.base = self.base.replace("//localhost:", "//127.0.0.1:")
        # qwen-mt 不接受 system 角色（实测 400），构造时置 True
        self.no_system = bool(no_system)
        # 术语表 [(原文, 译法), ...]：qwen-mt 走 translation_options 注入，
        # 其他后端忽略（本地 HY-MT 的术语在 local_mt.py 里处理）
        self.terms = list(terms or [])
        self.terms_used = 0
        self.ok = bool(self.base and self.model)
        self.calls = self.fails = 0
        self.t_last = 0.0
        self._hc = None             # 长连接（懒建）
        self._reconnects = 0

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
        # max_tokens 不能写死 220：合并翻译多段时输出被截断 → 分隔符段数
        # 对不上 → translate_batch 静默退回逐条（优化失效且无报错，
        # 这个项目已经踩过同类坑）。按输入长度放大：中文约 1 字 ≈ 1 token，
        # 译文长度与原文同量级，留足余量并封顶。
        max_tokens = max(220, min(1024, len(text) + 64))
        body = {"model": self.model, "stream": False,
                "temperature": self.temperature, "max_tokens": max_tokens,
                "messages": msgs}
        # ── qwen-mt 术语定制（translation_options）─────────────
        # 只注入**本句命中**的术语（全量注入会稀释提示、还可能撞 API
        # 的术语条数上限）。这是官方能力：terms 的 source/target 对。
        # 只对 qwen-mt 系列下发，其他 OpenAI 兼容端点不认识这个字段。
        if self.terms and "qwen-mt" in (self.model or ""):
            hits = [(s, t) for s, t in self.terms
                    if s and s.lower() in text.lower()]
            if hits:
                self.terms_used += len(hits)
                body["translation_options"] = {
                    "terms": [{"source": s, "target": t} for s, t in hits]}
        return body

    def _path(self):
        """请求路径（含 base 里的前缀，如 /compatible-mode/v1）。"""
        u = urllib.parse.urlsplit(self.base)
        prefix = u.path.rstrip("/")
        tail = "/api/generate" if self.kind == "ollama" else "/chat/completions"
        return prefix + tail

    def _extract(self, j):
        if self.kind == "ollama":
            return j.get("response", "")
        try:
            return j["choices"][0]["message"]["content"]
        except Exception:
            return ""

    # ── 长连接管理 ───────────────────────────────────────────
    def _conn(self):
        """拿一个可用连接（懒建 + 断线重建）。"""
        if self._hc is None:
            u = urllib.parse.urlsplit(self.base)
            if u.scheme == "https":
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                self._hc = http.client.HTTPSConnection(
                    u.hostname, u.port or 443, timeout=self.timeout,
                    context=ctx)
            else:
                self._hc = http.client.HTTPConnection(
                    u.hostname, u.port or 80, timeout=self.timeout)
        return self._hc

    def _drop_conn(self):
        try:
            if self._hc is not None:
                self._hc.close()
        except Exception:
            pass
        self._hc = None

    def _post_once(self, body, hdr):
        """单次 POST；返回解析后的 JSON。异常直接抛给调用方。"""
        c = self._conn()
        c.request("POST", self._path(), body=body, headers=hdr)
        resp = c.getresponse()
        raw = resp.read()                    # 必须读空，连接才能复用
        if resp.status != 200:
            raise RuntimeError(
                f"HTTP {resp.status}: {raw.decode('utf-8', 'replace')[:150]}")
        return json.loads(raw.decode("utf-8", "replace"))

    # ── 主入口 ───────────────────────────────────────────────
    def __call__(self, text, target="Simplified Chinese"):
        if not self.ok or not (text or "").strip():
            return ""
        body = json.dumps(self._payload(text, target),
                          ensure_ascii=False).encode("utf-8")
        hdr = {"Content-Type": "application/json"}
        if self.kind == "openai" and self.key:
            hdr["Authorization"] = f"Bearer {self.key}"
        t0 = time.time()
        for attempt in (0, 1):
            try:
                j = self._post_once(body, hdr)
                self.calls += 1
                self.t_last = time.time() - t0
                return _clean(self._extract(j))
            except Exception:
                # 连接类失败很常见（服务端空闲断连 / 代理抽风）：
                # 丢掉旧连接重建，重试一次；再失败才算真失败。
                self._drop_conn()
                self._reconnects += 1
                if attempt == 1:
                    self.fails += 1
                    return ""

    def status(self):
        if not self.ok:
            return "LLM=未配置"
        return (f"LLM={self.kind}:{self.model}　调用 {self.calls}"
                f"　失败 {self.fails}　重连 {self._reconnects}"
                f"　术语命中 {self.terms_used}　上次 {self.t_last:.2f}s")


def from_env_or_args(base_url=None, model=None, kind=None, terms=None):
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
        return LLMTranslator(base, mdl, key, k or "openai", terms=terms)

    # ③ 阿里云百炼 qwen-mt-flash（实测最快最准，优先）
    ds_key = os.environ.get("DASHSCOPE_API_KEY") or _read_dashscope_key()
    if ds_key:
        return LLMTranslator(DASHSCOPE_BASE,
                             mdl or DASHSCOPE_MODEL, ds_key, "openai",
                             no_system=True, terms=terms)

    # ④ 本机 Ollama
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags",
                                    timeout=2) as r:
            tags = json.loads(r.read().decode("utf-8", "replace"))
        names = [m.get("name", "") for m in tags.get("models", [])]
        if names:
            return LLMTranslator("http://127.0.0.1:11434", names[0],
                                 None, "ollama", terms=terms)
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
                             mdl or "deepseek-chat", key, "openai",
                             terms=terms)
    return None
