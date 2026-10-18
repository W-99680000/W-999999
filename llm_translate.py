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
import threading
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
                    max_items=6, context=None):
    """一次调用翻译多段；返回与 texts **等长**的列表。

    失败（段数对不上/异常）就退回**逐条翻译** —— 宁可慢，不能错位。
    调用方按 zip 取用，所以返回长度必须严格等于输入长度。

    context：本批之前的若干条**原文**句子（按时间顺序）。
    为什么有效：qwen-mt 是指令免疫的纯翻译引擎，"参考上下文"这类
    提示它根本不执行、还会当成正文译出来。所以上下文只能**混进待译
    正文里当前缀**——引擎翻译整个批次时自然获得指代/省略/语气的
    上下文（"それ"译成"那个"还是"它"全看前文），译完我们把前缀
    对应的部分**切掉**只留本批。段数校验按 len(context)+len(items)
    来，对不上照常退回逐条，零风险。
    """
    items = [("" if x is None else str(x)) for x in texts]
    if not items:
        return []
    if len(items) == 1 or max_items < 2 or len(items) > max_items:
        return [translator(x, target=target) if x else "" for x in items]
    ctx = [str(x) for x in (context or []) if x]
    body = (" " + BATCH_SEP + " ").join(ctx + items)
    prompt = BATCH_PROMPT.format(sep=BATCH_SEP, target=target, body=body)
    try:
        raw = translator(prompt, target=target)
    except Exception:
        raw = ""
    if raw and BATCH_SEP in raw:
        parts = [p.strip() for p in raw.split(BATCH_SEP)]
        parts = [p for p in parts if p]
        if len(parts) == len(ctx) + len(items):
            return parts[len(ctx):]      # 切掉上下文前缀，只留本批
        # 引擎偶尔会把上下文并进第一句——段数对不上时如果只差 ctx 的量，
        # 直接按无上下文重试一次太贵，退回逐条即可（下面统一处理）
    # 退回逐条（并留下痕迹，便于诊断为什么合并失败）
    translator.batch_fallbacks = getattr(translator, "batch_fallbacks", 0) + 1
    return [translator(x, target=target) if x else "" for x in items]


class LLMTranslator:
    """一个后端，两种协议。

    kind="openai"  → POST {base}/chat/completions  （DeepSeek / vLLM / 中转）
    kind="ollama"  → POST {base}/api/generate      （Ollama 原生）
    """

    def __init__(self, base_url, model, api_key=None, kind="openai",
                 timeout=8.0, temperature=0.1, num_ctx=1024,
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
        # ── Qwen-MT 官方 translation_options（术语干预/领域提示）──
        # 只对 qwen-mt* 生效：其它 OpenAI 兼容端点不认这个字段。
        # 字段格式来自官方文档（阿里云 Model Studio, 2026-10-11）。
        self.terms = []          # [(源, 目标), ...]
        self.domains = ""        # 英文领域提示（可为空）
        self.no_options = False  # 服务端不认 translation_options 时降级
        self.option_fails = 0
        self.options_ok = 0
        self.ok = bool(self.base and self.model)
        self.calls = self.fails = 0
        self.t_last = 0.0
        # -- 连接复用（修"翻译无端断掉"）--
        # 原来每次翻译都 urllib.request.urlopen()，**每条请求新建
        # 一次 TCP+TLS 握手**，而且没有重连。空闲一会儿后服务端会关掉
        # 空闲连接、代理链路上的空闲连接更容易被掐断；下一次请求失败
        # 就直接返回空 —— 那条字幕就没译文了，看着就是"自己断了一下"。
        # 现在复用长连接，断了丢掉重建并**重试一次**。
        #
        # **必须按线程分开：http.client 不是线程安全的。**
        # 主程序的翻译池有 2 个 worker，两批翻译并发时共享同一条连接，
        # 两个响应的字节会在同一条流上交错 —— 轻则 JSON 解析失败
        # （被当成"翻译失败"返回空），重则读到另一半的响应体（错位）。
        # threading.local 让每个线程各持一条连接：既安全又能真并行。
        self._tls = threading.local()    # 每线程一条 http.client 连接（懒建）
        self._reconnects = 0             # 诊断：重连过几次

    # -- 连接管理 --
    def _conn(self):
        """拿一条本线程可用的连接（懒建；断了靠 _drop_conn 重建）。"""
        c = getattr(self._tls, "hc", None)
        if c is None:
            u = urllib.parse.urlsplit(self.base)
            if u.scheme == "https":
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                c = http.client.HTTPSConnection(
                    u.hostname, u.port or 443, timeout=self.timeout,
                    context=ctx)
            else:
                c = http.client.HTTPConnection(
                    u.hostname, u.port or 80, timeout=self.timeout)
            self._tls.hc = c
        return c

    def _drop_conn(self):
        c = getattr(self._tls, "hc", None)
        try:
            if c is not None:
                c.close()
        except Exception:
            pass
        self._tls.hc = None

    def _post_once(self, body, hdr):
        """单次 POST，用复用连接。异常直接抛给调用方。"""
        c = self._conn()
        c.request("POST", self._path(), body=body, headers=hdr)
        resp = c.getresponse()
        # **必须把响应体读空**，否则连接算脏的，无法复用。
        raw = resp.read()
        if resp.status != 200:
            raise RuntimeError(
                "HTTP %d: %s" % (resp.status,
                                 raw.decode("utf-8", "replace")[:150]))
        return json.loads(raw.decode("utf-8", "replace"))

    # ── Qwen-MT translation_options ─────────────────────────
    # 官方文档：translation_options 支持 source_lang / target_lang /
    # terms[{source,target}] / domains。source_lang 可给英文名或代码，
    # 未知用 "auto"；显式给源语言能提升准确率。
    _LANG_NAMES = {
        "en": "English", "zh": "Chinese", "zh-cn": "Chinese",
        "zh-tw": "Traditional Chinese", "ja": "Japanese",
        "ko": "Korean", "vi": "Vietnamese", "th": "Thai",
        "ru": "Russian", "es": "Spanish", "fr": "French",
        "de": "German", "pt": "Portuguese", "id": "Indonesian",
        "ms": "Malay", "ar": "Arabic", "hi": "Hindi",
    }

    @classmethod
    def _lang_name(cls, v):
        """把 "vi" / "Vietnamese" / "Simplified Chinese" 归一成官方值。"""
        if not v:
            return ""
        t = str(v).strip()
        low = t.lower().replace("_", "-")
        if low in cls._LANG_NAMES:
            return cls._LANG_NAMES[low]
        if low in ("simplified chinese", "chinese (simplified)"):
            return "Chinese"
        return t

    def _is_qwen_mt(self):
        return (self.kind == "openai"
                and (self.model or "").lower().startswith("qwen-mt"))

    def set_terms(self, terms):
        """设置术语表；支持 [(src,tgt)] 或 [{"source","target"}]。"""
        out = []
        for it in (terms or []):
            if isinstance(it, dict):
                a = (it.get("source") or "").strip()
                b = (it.get("target") or "").strip()
            else:
                try:
                    a, b = it[0], it[1]
                except Exception:
                    continue
                a, b = str(a).strip(), str(b).strip()
            if a and b:
                out.append((a, b))
        # 官方限制：整条请求 8192 输入 token，术语别无限堆
        self.terms = out[:200]

    def set_domains(self, text):
        self.domains = (text or "").strip()[:1000]

    def _options(self, text, target, source=None):
        opts = {
            "source_lang": self._lang_name(source) or "auto",
            "target_lang": self._lang_name(target) or "Chinese",
        }
        if self.terms:
            opts["terms"] = [{"source": a, "target": b}
                             for a, b in self.terms]
        if self.domains:
            opts["domains"] = self.domains
        return opts

    def _qwen_body(self, text, target, source=None):
        # messages 只能一条 user，content 只放待译文本（官方口径）。
        # 不再把 "Translate the following…" 塞进 content —— 那是给通用
        # LLM 的写法，qwen-mt 的翻译指令应放 translation_options。
        return {"model": self.model, "stream": False,
                "temperature": self.temperature, "max_tokens": 220,
                "messages": [{"role": "user", "content": text}],
                "translation_options": self._options(text, target, source)}

    # ── 请求体 ───────────────────────────────────────────────
    def _payload(self, text, target, source=None):
        # Qwen-MT：官方 translation_options（术语/领域/源语言锁定）。
        # 失败过一次（服务端不认该字段）就永久降级，不再反复 400。
        if self._is_qwen_mt() and not self.no_options:
            return self._qwen_body(text, target, source)
        # **qwen-mt 只接受 user / assistant 角色，发 system 会 400。**
        # 实测报错：Role must be in [user, assistant].
        # 这是翻译专用模型的特性 —— 翻译指令本身就该写在 user 里，
        # 它不需要（也不支持）system prompt。
        # 我第一版统一发 system+user，于是 qwen-mt 全部返回空
        # （异常被吞掉 → 空串），表面上"模式选对了但没译文"。
        #
        # source：上游 whisper 高置信检出时给的源语言全名（如 "Vietnamese"）。
        # 短碎片（3~8 个词）靠模型自己判语种**会漂**（越南语被判成泰语/
        # 粤语都实测见过），给一句来源提示就能消歧；没给就保持原样（auto）。
        src_clause = f"{source} " if source else ""
        prompt = (f"Translate the following {src_clause}text into {target}. "
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

    def _path(self):
        """请求路径（含 base 里的前缀，如 /compatible-mode/v1）。

        连接复用要求主机与路径分开给 —— http.client 建连只要
        (host, port)，路径在 request() 里给。
        """
        u = urllib.parse.urlsplit(self.base)
        prefix = u.path.rstrip("/")
        tail = "/api/generate" if self.kind == "ollama" else "/chat/completions"
        return prefix + tail

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
    def __call__(self, text, target="Simplified Chinese", source=None):
        if not self.ok or not (text or "").strip():
            return ""
        body = json.dumps(self._payload(text, target, source),
                          ensure_ascii=False).encode("utf-8")
        hdr = {"Content-Type": "application/json"}
        if self.kind == "openai" and self.key:
            hdr["Authorization"] = f"Bearer {self.key}"
        t0 = time.time()
        # 两次尝试：连接类失败很常见（服务端空闲断连、代理抽风）。
        # 丢掉旧连接重建再试一次；两次都失败才算真失败。
        tried_fallback = False
        for attempt in (0, 1):
            try:
                j = self._post_once(body, hdr)
                self.calls += 1
                self.t_last = time.time() - t0
                if self._is_qwen_mt() and not self.no_options:
                    self.options_ok += 1
                return _clean(self._extract(j))
            except Exception as e:
                self._drop_conn()
                self._reconnects += 1
                # Qwen-MT 服务端不认 translation_options（400/422）：
                # 降级回原来的 prompt 风格，重试一次并永久记住。
                msg = str(e)
                if (self._is_qwen_mt() and not self.no_options
                        and not tried_fallback
                        and ("HTTP 4" in msg
                             or "translation_options" in msg)):
                    tried_fallback = True
                    self.no_options = True
                    self.option_fails += 1
                    print(f"  ！qwen-mt 不接受 translation_options"
                          f"（{msg[:90]}），已降级为普通 prompt"
                          f"（术语注入失效）", flush=True)
                    body = json.dumps(self._payload(text, target, source),
                                      ensure_ascii=False).encode("utf-8")
                    continue
                if attempt == 1:
                    self.fails += 1
                    return ""

    def warmup(self):
        """后台预热：建 TLS 连接 + 触发服务端模型装载。

        第一句真实翻译原来要付"TCP+TLS 握手 + 模型冷启动"两份钱
        （实测首句比稳态慢 0.5~1.5s），用户在视频开头最容易注意到。
        启动时在后台线程调一次，观众看到的第一句就是热价。
        """
        try:
            self("hello", target="Simplified Chinese")
        except Exception:
            pass

    def status(self):
        if not self.ok:
            return "LLM=未配置"
        extra = ""
        if self.terms:
            extra += f"　术语 {len(self.terms)}"
        if self.option_fails:
            extra += "　选项降级"
        return (f"LLM={self.kind}:{self.model}　调用 {self.calls}"
                f"　失败 {self.fails}　重连 {self._reconnects}"
                f"　上次 {self.t_last:.2f}s{extra}")


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
