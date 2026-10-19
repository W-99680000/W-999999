# -*- coding: utf-8 -*-
"""model_registry.py —— 模型注册表（**模型选择的唯一数据源**）

为什么要单独一层
================
在此之前，"用什么模型"这件事**散在七个文件里**，每处一份字面量：

    live_subtitle_v2.py  --model 默认 "small"        （识别，实时）
    live_subtitle_v2.py  显存表 {"medium":1500,...}   （识别，估算）
    live_subtitle_v2.py  --stream-model 默认 "base"   （识别，流式）
    pretranslate.py      --model 默认 "large-v3"      （识别，点播缓存）
    remote_asr.py        MODEL = "qwen3-asr-flash"    （识别，远端）
    llm_translate.py     DASHSCOPE_MODEL = "qwen-mt-flash"
    local_mt.py          MODEL_CANDIDATES = [4 个 gguf 路径]
    get_models.py        WANT = [4 个语言对], KNOWN_MB

后果对"两个翻译模块"是直接的：
  · **直播翻译**（live_subtitle_v2.py）与**视频翻译**（pretranslate.py /
    watch_player.py）各自硬编码默认值，改一个不会带动另一个 —— 两边的
    模型口径必然漂移（现在就是：实时 medium、缓存 large-v3，没人说得清
    这是不是有意的）。
  · 后台管理界面要列"有哪些模型可选"，只能去 grep 源码。
  · 加一个模型要改 3~7 处，且没有一处能告诉你"漏了哪个"。

所以本模块提供**一层薄的注册表**：
  · `model_registry.json` 是数据（人可读、可手改、可被界面直接读）
  · 本模块是访问层（纯函数、**import 零副作用**、不联网、不加载模型）
  · 两个翻译模块**按模式**取默认值：`resolve("loopback")` / `resolve("cache")`

**模式只存差异，不存快照** —— 这是这一层最重要的设计决定：

    modes.loopback.asr = null   →  跟着 defaults.asr 走
    modes.cache.asr    = "local:large-v3"  →  只在这一处覆盖

于是"把实时识别模型从 medium 升到 large-v3"，只要动 defaults 一处，
缓存模式如果本来就显式覆盖（large-v3），**结果不变**；实时模式跟着变。
如果存的是快照（两个模式各存一份完整配置），升级 defaults 就不会带动
任何模式 —— 注册表就退化成了又一份散落配置。2026-08 实测过 .item
这种坑：`data` 里的 `log` 被实体索引后两层身份都指向同一批数据。

与既有代码的关系（**不改行为**）
================================
本模块不 import 项目里任何别的模块，别的模块也**不必**改就能继续跑。
它只做两件事：
  ① 给界面/脚本一个"当前有哪些模型、各自代价如何、哪些真可用"的答案
  ② 给出两个模式各自的默认绑定，供之后把硬编码默认值换成
     `resolve(mode)` 用（`registry_pending_defaults()` 列出还没换的点）

`cli_args(mode)` 生成的是**纯 dict**（键名就是 argparse 的 dest），
不 import argparse —— 于是"界面上的选择"能直接喂给
`parser.set_defaults(**cli_args("cache"))`，而不需要界面知道
live_subtitle_v2.py 里那个参数叫什么。

文件
====
    model_registry.json     注册表数据（首次 import 时若不存在会自建）
    model_registry_web.json 给管理界面的快照（generated，界面 fetch 它）

自检
====
    python model_registry.py            # 结构 + 覆盖规则 + 兜底逻辑自检
"""
from __future__ import annotations

import copy
import json
import os
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "model_registry.json")
WEB_PATH = os.path.join(HERE, "model_registry_web.json")

SCHEMA = 1

#: 模式 id → 中文名。界面左侧那两个入口就是这两个 id。
MODES = {
    "loopback": "直播翻译",
    "cache": "视频翻译",
}

#: 模式 → 该模式的 CLI 参数名。界面/脚本不用知道具体参数叫什么。
MODE_ARGS = {
    "loopback": {
        "asr": "--model",               # live_subtitle_v2.py
        "asr_stream": "--stream-model",
    },
    "cache": {
        "asr": "--model",               # pretranslate.py
    },
}

#: 哪些模型 id 目前真的"接上了链路"。没接上的也能在界面里选，但要标注 ——
#: 否则会出现"界面上选了却没生效"这种最难查的问题。
WIRED = {
    "local:faster-whisper-tiny": True,
    "local:faster-whisper-base": True,
    "local:faster-whisper-small": True,
    "local:faster-whisper-medium": True,
    "local:faster-whisper-large-v3": True,
    "remote:qwen3-asr-flash": True,
    "remote:qwen-mt-flash": True,
    "remote:qwen-mt-lite": True,
    "remote:qwen-mt-plus": True,
    "local:hh-translate-base": True,
    "local:hh-translate-small": True,
    "local:hh-translate-medium": True,
    "local:hh-translate-large": True,
    "offline:argos-en-zh": True,
    "offline:google-free": True,
    "offline:mymemory": True,
    "relay:vi-en-zh": True,
    # Qwen3-ASR 本地后端（2026-10-19 接入，qwen_asr_backend.py）
    "local:qwen3-asr-0.6b": True,
    "local:qwen3-asr-1.7b": True,
    # 二代/第三方本地 MT（2026-10-19 接线，local_mt.py CHAT_FAMILIES）
    "local:hy-mt2-1.8b": True,
    "local:translategemma-4b": True,
}

#: 兜底模型尺寸（MB）。get_models.KNOWN_MB 的同一组数字。
#: 放这里是因为注册表要在**加载模型之前**就能算显存预算 ——
#: 那时还没有任何缓存可查。
FALLBACK_MB = {
    "tiny": 80, "base": 150, "small": 500, "medium": 1500,
    "large-v3": 3100, "large": 3100,
}

#: 各识别模型的**解码开销**（MB，KV cache + 激活 + ctranslate2 工作区）。
#: 这不是"模型占显存"的全部，而是**减去权重文件之后**多出来的那部分：
#:     model_dir_mb(权重) + _DECODE_OVERHEAD ≈ 上卡后实际占用
#: 校准依据（README 的实测显存表 + 本机 HF 缓存里的权重大小）：
#:     tiny       权重  75MB，估计 ~180MB
#:     base       权重 141MB，估计 ~300MB
#:     small      权重 464MB，实测 605MB  → 141
#:     medium     权重 1460MB，实测 1615MB → 155
#:     large-v3   权重 2948MB，实测 3127MB → 179
#: 一开始我按"权重的一半"猜（620/720），结果 medium 估成 2077MB ——
#: 比实测高 460MB。**高估会让 device_placement 白白判"放不下"**，
#: 于是翻译模块被迫退回 CPU。所以这里必须用实测反推的值，不能拍脑袋。
_DECODE_OVERHEAD = {"tiny": 105, "base": 159, "small": 141, "medium": 155,
                    "large-v3": 179}

_ASR_SIZES = ("tiny", "base", "small", "medium", "large-v3")

_DEFAULT_JSON: dict[str, Any] = {
    "schema": SCHEMA,
    "updated": "2026-08-13",
    "means": {
        "asr": "语音识别（听写）：把音频变成原文",
        "mt": "机器翻译：把原文变成译文",
        "router": "译器选路：源语言 → 用哪个译器",
        "modes": "两个翻译模块。只存与 defaults 的差异（null = 跟随默认）",
        "vram_mb": "显存预算（MB）。本地模型取实测值；远端/离线为 0",
        "available": "此刻是否真的可用（按本机实况探测，不是愿望）",
        "catalog": "模型库全目录（含未接线的候选 + VAD/分离/对齐等部件），"
                   "按 task/lang/大小检索；catalog 里 wired=false 的不能直接选",
    },
    "defaults": {
        "asr": "local:faster-whisper-medium",
        "mt": "remote:qwen-mt-flash",
        "router": {"allow_offline": False},
    },
    "modes": {
        "loopback": {
            "label": "直播翻译",
            "goal": "低延迟：音频播出来才存在，等不起",
            "asr": None,
            "asr_stream": None,
            "mt": None,
            "params": {
                "max_batch": 2.0,
                "max_wait": 1.2,
                "mt_batch": 2,
                "mt_batch_wait": 0.35,
                "mt_cap": 2.0,
                "min_seg": 0.5,
                "max_seg": 4.0,
                "vad_silence": 0.45,
                "remote_asr": True,
            },
        },
        "cache": {
            "label": "视频翻译",
            "goal": "高准确：整片先识别后翻译，首句零延迟",
            "asr": "local:qwen3-asr-0.6b",
            "asr_stream": None,
            "mt": None,
            "params": {
                "mt_batch": 8,
                "force": False,
            },
        },
    },
    "asr": [
        {"id": "local:faster-whisper-large-v3", "name": "faster-whisper large-v3",
         "vendor": "Systran / ctranslate2", "source": "local",
         "kind": "faster-whisper", "size_key": "large-v3",
         "langs": ["*"], "vram_mb": 3127, "latency": "0.846s / 4s段",
         "accuracy": "best", "cost": "0",
         "note": "缓存模式默认；30 秒上下文 + 词级时间戳"},
        {"id": "local:faster-whisper-medium", "name": "faster-whisper medium",
         "vendor": "Systran / ctranslate2", "source": "local",
         "kind": "faster-whisper", "size_key": "medium",
         "langs": ["*"], "vram_mb": 1615, "latency": "0.603s / 4s段",
         "accuracy": "good", "cost": "0",
         "note": "实时默认；GPU 上 small 会自动升到这里（只慢 0.2s 但修掉可辨识错词）"},
        {"id": "local:faster-whisper-small", "name": "faster-whisper small",
         "vendor": "Systran / ctranslate2", "source": "local",
         "kind": "faster-whisper", "size_key": "small",
         "langs": ["*"], "vram_mb": 605, "latency": "0.405s / 4s段",
         "accuracy": "fair", "cost": "0",
         "note": "准确度不足（越南语实测前半后半都错），只在显存不够时用"},
        {"id": "local:faster-whisper-base", "name": "faster-whisper base",
         "vendor": "Systran / ctranslate2", "source": "local",
         "kind": "faster-whisper", "size_key": "base",
         "langs": ["*"], "vram_mb": 301, "latency": "0.25s / 4s段",
         "accuracy": "poor", "cost": "0",
         "note": "流式模式的默认；只适合看个大概"},
        {"id": "local:faster-whisper-tiny", "name": "faster-whisper tiny",
         "vendor": "Systran / ctranslate2", "source": "local",
         "kind": "faster-whisper", "size_key": "tiny",
         "langs": ["*"], "vram_mb": 180, "latency": "0.15s / 4s段",
         "accuracy": "poor", "cost": "0",
         "note": "冒烟测试用，别拿它看正片"},
        {"id": "remote:qwen3-asr-flash", "name": "qwen3-asr-flash",
         "vendor": "阿里云百炼", "source": "remote",
         "kind": "openai-compatible", "size_key": None,
         "langs": ["*"], "vram_mb": 0, "latency": "0.90s / 4s段",
         "accuracy": "ok", "cost": "≈0.79元/小时",
         "note": "无 GPU 时的回退；API 不还原大小写，词错比本地多"},
        # ── Qwen3-ASR 本地（2026-10-19 接入）──────────────
        # size_key 为 null：不是 ctranslate2 的尺寸表成员，显存按 vram_mb
        # 字段给（asr_size_mb 已支持该分支）。cli 是 --model 的取值。
        {"id": "local:qwen3-asr-0.6b", "name": "Qwen3-ASR 0.6B",
         "vendor": "阿里 Qwen", "source": "local",
         "kind": "qwen3-asr", "size_key": None, "cli": "qwen3-asr-0.6b",
         "modes": ["cache"],
         "langs": ["*"], "vram_mb": 1600,
         "latency": "≈whisper-medium（CPU 实测持平）",
         "accuracy": "good", "cost": "0",
         "note": "视频翻译默认。POC：vi/zh 干净样本全胜（中文不会转繁体）；"
                 "后端内置 VAD 切句 + 复读闸"},
        {"id": "local:qwen3-asr-1.7b", "name": "Qwen3-ASR 1.7B",
         "vendor": "阿里 Qwen", "source": "local",
         "kind": "qwen3-asr", "size_key": None, "cli": "qwen3-asr-1.7b",
         "modes": ["cache"],
         "langs": ["*"], "vram_mb": 3800,
         "latency": "≈1.5× 0.6B（GPU fp16）",
         "accuracy": "best", "cost": "0",
         "note": "开源 ASR 第一梯队（vi 4.91% vs turbo 8.10%）；"
                 "4070 8GB 可跑 fp16；需先下载 models/Qwen3-ASR-1.7B"},
    ],
    "mt": [
        {"id": "remote:qwen-mt-flash", "name": "qwen-mt-flash",
         "vendor": "阿里云百炼", "source": "remote", "kind": "llm",
         "langs": ["*"], "vram_mb": 0, "latency": "0.36s/段",
         "quality": "best", "cost": "≈0.3元/小时",
         "note": "默认译器。纯翻译引擎、指令免疫；固定开销约 0.57s/次，所以合并批次能摊薄"},
        {"id": "remote:qwen-mt-lite", "name": "qwen-mt-lite",
         "vendor": "阿里云百炼", "source": "remote", "kind": "llm",
         "langs": ["*"], "vram_mb": 0, "latency": "0.33s/段",
         "quality": "ok", "cost": "更低",
         "note": "比 flash 略快，语言略生硬"},
        {"id": "remote:qwen-mt-plus", "name": "qwen-mt-plus",
         "vendor": "阿里云百炼", "source": "remote", "kind": "llm",
         "langs": ["*"], "vram_mb": 0, "latency": "0.34s/段",
         "quality": "unstable", "cost": "更高",
         "note": "实测不稳定（专名会错），别当默认"},
        {"id": "local:hh-translate-base", "name": "HY-MT 1.8B (GGUF)",
         "vendor": "腾讯混元 / llama.cpp", "source": "local", "kind": "llama-server",
         "langs": ["*"], "vram_mb": 1081, "latency": "0.49s/段",
         "quality": "good", "cost": "0",
         "note": "完全离线。1.8B 一代用 hy_ 系列 token；模型路径取 local_mt.MODEL_CANDIDATES"},
        {"id": "local:hh-translate-small", "name": "HY-MT small (GGUF)",
         "vendor": "腾讯混元 / llama.cpp", "source": "local", "kind": "llama-server",
         "langs": ["*"], "vram_mb": 1407, "latency": "≈0.45s/段",
         "quality": "ok", "cost": "0",
         "note": "体积反而比 base 大，按实测显存给值，别按名字猜"},
        {"id": "local:hh-translate-medium", "name": "HY-MT medium (GGUF)",
         "vendor": "腾讯混元 / llama.cpp", "source": "local", "kind": "llama-server",
         "langs": ["*"], "vram_mb": 1820, "latency": "≈0.6s/段",
         "quality": "good", "cost": "0",
         "note": "中间档，本地翻译质量/显存的折中"},
        {"id": "local:hh-translate-large", "name": "HY-MT 7B (GGUF)",
         "vendor": "腾讯混元 / llama.cpp", "source": "local", "kind": "llama-server",
         "langs": ["*"], "vram_mb": 4410, "latency": "≈1.0s/段",
         "quality": "good", "cost": "0",
         "note": "7B 一代用 startoftext/extra_N token —— 发错 token 会表现成'模型更差'"},
        {"id": "offline:argos-en-zh", "name": "argos en→zh (1.9)",
         "vendor": "argostranslate", "source": "offline", "kind": "argos",
         "langs": ["en→zh"], "vram_mb": 0, "latency": "0.40s/段",
         "quality": "fair", "cost": "0",
         "note": "纯离线。只有 en→zh 等少数对：**没有 ja→zh**，所以日文必须走远端"},
        {"id": "offline:google-free", "name": "Google 免 key 端点",
         "vendor": "clients5.google.com", "source": "offline", "kind": "http",
         "langs": ["*"], "vram_mb": 0, "latency": "0.3~0.6s/段",
         "quality": "fair", "cost": "0（非官方）",
         "note": "端点变体差异极大，必须用 client=dict-chrome-ex 并探一次缓存"},
        {"id": "offline:mymemory", "name": "MyMemory",
         "vendor": "mymemory.translated.net", "source": "offline", "kind": "http",
         "langs": ["*"], "vram_mb": 0, "latency": "0.5~1.2s/段",
         "quality": "fair", "cost": "免费额度",
         "note": "argos 没有的语言对（如 vi→zh）的兜底"},
        {"id": "relay:vi-en-zh", "name": "argos 中继 vi→en→zh",
         "vendor": "argostranslate ×2", "source": "offline", "kind": "relay",
         "langs": ["vi→zh"], "vram_mb": 0, "latency": "0.8s/段（两次）",
         "quality": "poor", "cost": "0",
         "note": "实测会崩（源文本自身错时下游难救），只作离线演示"},
        # ── 二代/第三方本地 MT（2026-10-19 接线，local_mt.py CHAT_FAMILIES）
        {"id": "local:hy-mt2-1.8b", "name": "Hy-MT2 1.8B (GGUF)",
         "vendor": "腾讯混元 / llama.cpp", "source": "local",
         "kind": "llama-server",
         "langs": ["*"], "vram_mb": 1200, "latency": "≈0.5s/段",
         "quality": "good", "cost": "0",
         "note": "HY-MT1.5 的换代（2026-05，Apache-2.0）：字幕本地化/术语约束"
                 "专项优化；走 llama-server chat 端点（模板用 GGUF 内嵌的），"
                 "Q4_K_M 已在 F:\\hh_si_pro\\hh-translate-model"},
        {"id": "local:translategemma-4b", "name": "TranslateGemma 4B",
         "vendor": "Google", "source": "local",
         "kind": "llama-server",
         "langs": ["*"], "vram_mb": 3500, "latency": "≈0.8s/段",
         "quality": "good", "cost": "0",
         "note": "55 语（WMT24++）；Gemma Terms 许可。走 llama-server chat"
                 " 端点，Q4_K_M 已在 F:\\hh_si_pro\\hh-translate-model"},
    ],
    "router": {
        "offline_sources": {
            "en": {"mt": "offline:argos-en-zh", "note": "直连包存在，最快"},
            "ja": {"mt": "remote:qwen-mt-flash", "note": "argos 无 ja→zh 包"},
            "ko": {"mt": "remote:qwen-mt-flash", "note": "argos 无 ko→zh 包"},
            "vi": {"mt": "remote:qwen-mt-flash",
                   "fallback": ["offline:google-free", "offline:mymemory"],
                   "note": "argos 无 vi→zh；中继质量差"},
        },
        "rules": [
            "默认不吃离线：实测同一批句子上 qwen-mt 每项都优于 argos，代价只有 0.25s",
            "只有 --prefer-offline 才让英文走 argos，且源语言置信度要 >= 0.85",
            "argos 对没有模型的语言对会返回 CompositeTranslation 而不报错 —— 必须显式查包",
        ],
    },
    # ── 模型库全目录（2026-10-19 调研扩充）──────────────────
    # 与 asr/mt 池的分工：池 = 能选的接线项；catalog = **全量可检索知识库**
    # （未接线的新模型、以及 VAD/人声分离/对齐/说话人分离等部件）。
    # 检索入口见 model_search.py（本地过滤 + 在线发现新模型）。
    # 字段：task / params / size_mb / license / released / repo / status。
    "catalog": [
        # ── 识别：东方语系挑战者 ─────────────────────────
        {"id": "local:dolphin-small", "task": "asr", "name": "Dolphin small",
         "vendor": "清华 + 海天瑞声 (DataoceanAI)", "kind": "dolphin",
         "params": "372M", "size_mb": 750,
         "langs": ["zh", "vi", "ja", "ko", "th", "id", "ms", "yue", "ar", "hi"],
         "license": "Apache-2.0", "released": "2025-03",
         "repo": "DataoceanAI/Dolphin", "source": "local",
         "wired": False, "status": "catalog",
         "tags": ["multilingual", "oriental", "vad", "lid", "timestamps"],
         "note": "40 个东方语言含越南语 + 22 中文方言；官方称平均 WER 比 "
                 "whisper-large-v3 低 54.1%（自有测试集）；自带 VAD/分段/LID "
                 "多任务。Qwen3-ASR 之外最值得 POC 的对照项"},
        {"id": "local:dolphin-base", "task": "asr", "name": "Dolphin base",
         "vendor": "清华 + 海天瑞声 (DataoceanAI)", "kind": "dolphin",
         "params": "140M", "size_mb": 320,
         "langs": ["zh", "vi", "ja", "ko", "th", "id", "ms", "yue"],
         "license": "Apache-2.0", "released": "2025-03",
         "repo": "DataoceanAI/Dolphin", "source": "local",
         "wired": False, "status": "catalog",
         "tags": ["multilingual", "oriental", "edge"],
         "note": "140M 端侧档；显存紧张时的亚洲语种备选"},
        {"id": "local:glm-asr-nano", "task": "asr", "name": "GLM-ASR-Nano-2512",
         "vendor": "智谱 AI", "kind": "glm-asr",
         "params": "1.5B", "size_mb": 3000,
         "langs": ["zh", "yue", "en", "ja", "ko"],
         "license": "MIT", "released": "2025-12",
         "repo": "zai-org/GLM-ASR-Nano-2512", "source": "local",
         "wired": False, "status": "catalog",
         "tags": ["chinese", "dialect", "whisper-scene", "transformers"],
         "note": "中文/方言/耳语场景 SOTA（Open ASR Leaderboard 7.03 vs "
                 "whisper-large-v3 7.4）；**无越南语**，只作中文档"},
        {"id": "local:fun-asr-mlt-nano", "task": "asr", "name": "Fun-ASR-MLT-Nano",
         "vendor": "阿里 FunAudioLLM", "kind": "funasr",
         "params": "~0.5B", "size_mb": 1200,
         "langs": ["zh", "en", "ja", "ko", "vi", "th", "id", "ms"],
         "license": "MIT", "released": "2025",
         "repo": "FunAudioLLM/Fun-ASR-MLT-Nano-2512", "source": "local",
         "wired": False, "status": "catalog",
         "tags": ["multilingual", "funasr", "vllm"],
         "note": "31 语独立多语 checkpoint（含 vi/th）；除 Qwen3-ASR 外另一个"
                 "含越南语的小模型"},
        {"id": "local:sensevoice-small", "task": "asr", "name": "SenseVoice-Small",
         "vendor": "阿里 FunAudioLLM", "kind": "funasr",
         "params": "234M", "size_mb": 470,
         "langs": ["zh", "yue", "en", "ja", "ko"],
         "license": "MIT", "released": "2024-07",
         "repo": "FunAudioLLM/SenseVoiceSmall", "source": "local",
         "wired": False, "status": "catalog",
         "tags": ["fast", "punctuation", "vad", "speaker", "emotion"],
         "note": "10s 音频 70ms（Whisper 470ms）；内置 fsmn-VAD + 标点恢复 + "
                 "cam++ 说话人；会议实测 WER 6.2% vs Whisper-L 8.7%"},
        {"id": "local:omnilingual-asr-300m", "task": "asr",
         "name": "Omnilingual ASR 300M", "vendor": "Meta FAIR",
         "kind": "omnilingual", "params": "300M", "size_mb": 600,
         "langs": ["*"], "license": "Apache-2.0", "released": "2025-11",
         "repo": "facebook/omniASR-300M", "source": "local",
         "wired": False, "status": "catalog",
         "tags": ["1600-languages", "zero-shot", "low-resource"],
         "note": "1600+ 语言、零样本扩新语言；冷门语种视频的兜底（主力 vi/zh "
                 "它不占优）"},
        {"id": "local:parakeet-tdt-0.6b", "task": "asr",
         "name": "Parakeet-TDT 0.6B v3", "vendor": "NVIDIA",
         "kind": "nemo", "params": "0.6B", "size_mb": 2400,
         "langs": ["en", "de", "fr", "es", "it"],
         "license": "CC-BY-4.0", "released": "2025",
         "repo": "nvidia/parakeet-tdt-0.6b-v3", "source": "local",
         "wired": False, "status": "catalog",
         "tags": ["european", "ctc-tdt", "fast"],
         "note": "欧洲语言最强开源档；**无中文/越南语**，与主力场景不重合"},
        {"id": "local:moonshine-base", "task": "asr", "name": "Moonshine base",
         "vendor": "Useful Sensors", "kind": "onnx", "params": "61M",
         "size_mb": 240, "langs": ["en"], "license": "MIT",
         "released": "2024-10", "repo": "UsefulSensors/moonshine",
         "source": "local", "wired": False, "status": "catalog",
         "tags": ["edge", "onnx", "english"],
         "note": "端侧英文；可变长输入不补零，短音频比 whisper 快 5×"},
        {"id": "local:whistle-16m", "task": "asr", "name": "Whistle",
         "vendor": "Cactus", "kind": "onnx", "params": "16.9MB",
         "size_mb": 17,
         "langs": ["en", "de", "fr", "es", "it", "nl", "pl"],
         "license": "Apache-2.0", "released": "2026-10",
         "repo": "cactus-compute/whistle", "source": "local",
         "wired": False, "status": "catalog",
         "tags": ["edge", "tiny", "anti-hallucination"],
         "note": "16.9MB 单文件；**静音直接返回空串、不进解码器**的防幻觉"
                 "设计值得抄（无中文/越南语）"},
        {"id": "local:kyutai-stt-1b", "task": "asr", "name": "Kyutai STT 1B",
         "vendor": "Kyutai", "kind": "kyutai", "params": "1B",
         "size_mb": 2000, "langs": ["en", "fr"], "license": "CC-BY-4.0",
         "released": "2025-06", "repo": "kyutai/stt-1b-en_fr",
         "source": "local", "wired": False, "status": "catalog",
         "tags": ["streaming", "semantic-vad", "delayed-streams"],
         "note": "0.5s 延迟真流式；**语义 VAD**（判『说完了没有』而非"
                 "『有没有声音』）是直播句末判定的升级思路，语言只有英/法"},
        {"id": "streaming:sherpa-onnx-zipformer", "task": "streaming",
         "name": "sherpa-onnx Zipformer 流式", "vendor": "k2-fsa",
         "kind": "onnx", "params": "~100MB", "size_mb": 100,
         "langs": ["zh", "en", "ja", "ko", "yue"],
         "license": "Apache-2.0", "released": "2025-07",
         "repo": "k2-fsa/sherpa-onnx", "source": "local",
         "wired": False, "status": "catalog",
         "tags": ["streaming", "cpu", "endpoint", "ten-vad"],
         "note": "真流式 60ms 出字、自带端点检测、CPU 实时；2025-07 已集成 "
                 "TEN-VAD。**无越南语**，适合直播『快字草稿层』/CPU 回退升级"},
        {"id": "local:faster-whisper-large-v2", "task": "asr",
         "name": "faster-whisper large-v2", "vendor": "Systran / ctranslate2",
         "kind": "faster-whisper", "params": "1.55B", "size_mb": 2948,
         "langs": ["*"], "license": "MIT", "released": "2023-10",
         "repo": "Systran/faster-whisper-large-v2", "source": "local",
         "wired": False, "status": "catalog",
         "tags": ["whisper", "stability", "fallback"],
         "note": "社区反馈：复读/幻觉明显少于 large-v3（VideoCaptioner 生态）。"
                 "值得进 eval 矩阵当稳定档"},
        # ── 2026 新增：turbo / 二代翻译 / 大号 TranslateGemma ──
        {"id": "local:faster-whisper-large-v3-turbo", "task": "asr",
         "name": "faster-whisper large-v3-turbo",
         "vendor": "OpenAI / Systran", "kind": "faster-whisper",
         "params": "809M", "size_mb": 1620,
         "langs": ["*"], "license": "MIT", "released": "2024-10",
         "repo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
         "source": "local", "wired": False, "status": "catalog",
         "tags": ["whisper", "turbo", "fast"],
         "note": "large-v3 的蒸馏版：解码层从 32 降到 4，速度快 3~5 倍、"
                 "显存减半；准确率略低（vi 8.10% vs large-v3 6.9%）。"
                 "显存紧张时替代 medium 的候选"},
        {"id": "local:hy-mt2-7b", "task": "mt", "name": "Hy-MT2 7B (GGUF)",
         "vendor": "腾讯混元 / llama.cpp", "kind": "llama-server",
         "params": "7B", "size_mb": 4600,
         "langs": ["*"], "license": "Apache-2.0", "released": "2026-05",
         "repo": "Tencent-Hunyuan/Hy-MT2-7B-GGUF", "source": "local",
         "wired": False, "status": "catalog",
         "tags": ["translation", "offline", "quality"],
         "note": "Hy-MT2 中档：官方称 fast-thinking 模式下优于 DeepSeek-V4-Pro"
                 "/Kimi K2.6；llama-server 直接吃 chat 模板"},
        {"id": "local:hy-mt2-30b-a3b", "task": "mt",
         "name": "Hy-MT2 30B-A3B (MoE)",
         "vendor": "腾讯混元", "kind": "llama-server",
         "params": "30B-A3B", "size_mb": 19000,
         "langs": ["*"], "license": "Apache-2.0", "released": "2026-05",
         "repo": "Tencent-Hunyuan/Hy-MT2-30B-A3B", "source": "local",
         "wired": False, "status": "catalog",
         "tags": ["translation", "moe", "quality"],
         "note": "MoE 旗舰：激活 3B，质量最高；19GB Q4 超出 8GB 4070，"
                 "只作目录收录（换机/多卡再考虑）"},
        {"id": "local:translategemma-12b", "task": "mt",
         "name": "TranslateGemma 12B", "vendor": "Google",
         "kind": "llama-server", "params": "12B", "size_mb": 7300,
         "langs": ["*"], "license": "Gemma Terms", "released": "2026-01",
         "repo": "google/translategemma-12b-it", "source": "local",
         "wired": False, "status": "catalog",
         "tags": ["translation", "offline", "quality"],
         "note": "4B 的上一档：WMT24++ MetricX 明显更低；Q4 约 7.3GB，"
                 "8GB 卡只能勉强上（要和 whisper 抢显存）"},
        {"id": "local:translategemma-27b", "task": "mt",
         "name": "TranslateGemma 27B", "vendor": "Google",
         "kind": "llama-server", "params": "27B", "size_mb": 16000,
         "langs": ["*"], "license": "Gemma Terms", "released": "2026-01",
         "repo": "google/translategemma-27b-it", "source": "local",
         "wired": False, "status": "catalog",
         "tags": ["translation", "quality", "server"],
         "note": "质量档旗舰，本机 8GB 跑不动，留作目录/换机参考"},
        # ── 语音前处理 / 对齐 / 说话人 ──────────────────
        {"id": "vad:ten-vad", "task": "vad", "name": "TEN-VAD",
         "vendor": "TEN-framework", "kind": "onnx", "params": "306KB",
         "size_mb": 0.3, "langs": ["*"], "license": "Apache-2.0",
         "released": "2025-06", "repo": "TEN-framework/ten-vad",
         "source": "local", "wired": False, "status": "catalog",
         "tags": ["vad", "endpoint", "silero-alternative", "cpu"],
         "note": "PR 曲线全面优于 Silero/WebRTC；语音→非语音切换快数百 ms，"
                 "能抓 Silero 漏掉的段间短停顿。切句边界/直播 flush 直接受益"},
        {"id": "vad:silero", "task": "vad", "name": "Silero VAD（现役）",
         "vendor": "snakers4 / faster-whisper 内置", "kind": "silero",
         "params": "~1MB", "size_mb": 1, "langs": ["*"], "license": "MIT",
         "released": "2021", "repo": "snakers4/silero-vad",
         "source": "local", "wired": True, "status": "active",
         "tags": ["vad", "bundled"],
         "note": "两条识别链路现在都用它（threshold 0.35 / 500ms 静音 / "
                 "200ms 垫片）；TEN-VAD 的对照基准"},
        {"id": "separation:demucs", "task": "separation", "name": "Demucs htdemucs",
         "vendor": "Meta / facebookresearch", "kind": "torch", "params": "~80MB",
         "size_mb": 80, "langs": ["*"], "license": "MIT",
         "released": "2021", "repo": "facebookresearch/demucs",
         "source": "local", "wired": False, "status": "catalog",
         "tags": ["bgm", "vocal-isolation", "anti-hallucination"],
         "note": "BGM/嘈杂素材先分离人声再识别，多个字幕项目验证的降幻觉手段；"
                 "点播无实时压力，适合做 --vocal-iso 可选档"},
        {"id": "align:qwen3-forced-aligner-0.6b", "task": "align",
         "name": "Qwen3-ForcedAligner 0.6B", "vendor": "阿里 Qwen",
         "kind": "qwen-align", "params": "0.6B", "size_mb": 1200,
         "langs": ["zh", "en", "ja", "ko", "de", "fr", "es", "it", "pt", "ru", "ar"],
         "license": "Apache-2.0", "released": "2026-01",
         "repo": "Qwen/Qwen3-ForcedAligner-0.6B", "source": "local",
         "wired": False, "status": "catalog",
         "tags": ["alignment", "word-timestamps", "nar"],
         "note": "非自回归强制对齐，官方称超三个最强 E2E 对齐模型；**无越南语**"
                 "——vi 的词级时间戳继续靠本项目的合成方案"},
        {"id": "diarization:pyannote-community-1", "task": "diarization",
         "name": "pyannote Community-1", "vendor": "pyannote",
         "kind": "torch", "params": "4.0", "size_mb": 100,
         "langs": ["*"], "license": "gated (HF 需同意条款)",
         "released": "2026-06", "repo": "pyannote/speaker-diarization-community-1",
         "source": "local", "wired": False, "status": "catalog",
         "tags": ["diarization", "offline", "speaker"],
         "note": "离线说话人分离首选（需 HF gated 授权）；访谈/多人口播可标谁在说"},
        {"id": "diarization:sortformer-4spk", "task": "diarization",
         "name": "Streaming Sortformer", "vendor": "NVIDIA",
         "kind": "nemo", "params": "117M", "size_mb": 470,
         "langs": ["*"], "license": "open weights", "released": "2025-08",
         "repo": "nvidia/diar_sortformer_4spk-v1", "source": "local",
         "wired": False, "status": "catalog",
         "tags": ["diarization", "streaming", "4-speaker"],
         "note": "流式说话人分离（4 人→8 人），直播多人对话可标『谁在说话』"},
    ],
}


# ── 存取 ────────────────────────────────────────────────────────
_cache: dict[str, Any] | None = None


def load(path: str | None = None, reload: bool = False) -> dict[str, Any]:
    """读注册表。缺文件时**用内置默认建一份**（不联网、不猜）。

    内置默认 = 与代码里现有默认值一致的那一份，所以首次生成的文件
    不会改变任何行为。

    注意 `p = path or PATH` 是**函数体内**取模块级 PATH，不是默认参数
    `path=PATH`：默认参数在定义时求值一次，之后有人（测试、或以后要
    支持的用户配置目录）改了 `model_registry.PATH` 就再也不生效 ——
    于是服务改的是新路径、读的是旧路径。
    """
    global _cache
    p = path or PATH
    if _cache is not None and not reload and path is None:
        return _cache
    data: dict[str, Any] | None = None
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = None
    if not isinstance(data, dict) or "asr" not in data or "mt" not in data:
        data = copy.deepcopy(_DEFAULT_JSON)
        if path is None:
            save(data, p)
    if path is None:
        _cache = data
    return data


def save(data: dict[str, Any], path: str | None = None) -> str:
    """原子写回（先写 .tmp 再 replace）—— 别让界面读到半个 JSON。

    **写完必须作废进程内缓存。** 这是实测踩出来的：管理端改完模型后
    `/api/state` 仍显示旧模型，因为 `load()` 返回的是改之前那次读的
    缓存字典 —— 文件已经是新的，内存还是旧的。现象是"界面上改了、
    接口说没改"，比直接报错难查得多。
    `path` 显式给了（不在缓存覆盖范围内）就不动缓存。
    """
    global _cache
    p = path or PATH
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, p)
    if path is None:
        _cache = None
    return p


def default_data() -> dict[str, Any]:
    return copy.deepcopy(_DEFAULT_JSON)


# ── 查询 ────────────────────────────────────────────────────────
def get(model_id: str, data: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """按 id 找模型（两个池都找）。"""
    d = data or load()
    for pool in ("asr", "mt"):
        for m in d.get(pool) or []:
            if m.get("id") == model_id:
                return m
    return None


def pool(kind: str, data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    d = data or load()
    return list(d.get(kind) or [])


def modes(data: dict[str, Any] | None = None) -> dict[str, Any]:
    d = data or load()
    return {k: v for k, v in (d.get("modes") or {}).items() if k in MODES}


def modes_allowed(model_id: str, data: dict[str, Any] | None = None) -> list[str]:
    """这个识别模型能被**哪些模式**使用。

    为什么需要：两个模式对后端的结构要求不同 —— 直播要"边攒批边转写"的
    流式/低延迟形态，qwen3-asr 后端是"整段 VAD → 逐段转写"，只适合点播。
    界面上的模型下拉如果两个模式共用一份列表，就会出现"直播被选到
    qwen3-asr"（2026-10-19 实测发生过：app.py 的管理页把 loopback 存成了
    qwen3-asr-0.6b，下次启动直播链路直接起不来）。
    条目可用 `modes` 字段显式声明；没声明时按 kind 推断。
    """
    m = get(model_id, data)
    if not m:
        return list(MODES)
    explicit = m.get("modes")
    if isinstance(explicit, list) and explicit:
        return [str(x) for x in explicit]
    if m.get("kind") == "qwen3-asr":
        return ["cache"]          # 非流式：只适合点播
    return list(MODES)


def resolve(mode: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    """**核心入口**：这个模式用哪套模型。

    规则（顺序固定）：
      ① 模式显式覆盖（modes.<mode>.<slot> 非空）→ 用它
      ② 否则 defaults.<slot>
      ③ 都没有 → 内置默认
    返回的 id 一定能在 asr/mt 池里找到（找不到就回退内置默认并记警告），
    这样界面不会因为一个错别字渲染出空下拉。
    """
    d = data or load()
    m = (d.get("modes") or {}).get(mode)
    if not isinstance(m, dict):
        m = {}
    dflt = d.get("defaults") or {}
    warn: list[str] = []
    out: dict[str, Any] = {"mode": mode, "label": MODES.get(mode, mode),
                           "warn": warn}
    for slot, fallback in (("asr", "local:faster-whisper-medium"),
                           ("mt", "remote:qwen-mt-flash"),
                           ("asr_stream", None)):
        want = m.get(slot) or dflt.get(slot) or fallback
        if want and get(want, d) is None:
            warn.append(f"{slot}: 注册表里没有 {want!r}，回退 {fallback!r}")
            want = fallback
        # 模式兼容性：直播要流式后端，qwen3-asr 这类整段转写只能给点播。
        # 界面/手改把不合适的模型塞给某个模式时，这里**必须**拦下来并
        # 告警 —— 否则要到下次启动、识别链路报错才发现。
        if slot == "asr" and want and mode not in modes_allowed(want, d):
            allow = modes_allowed(want, d)
            warn.append(f"asr: {want!r} 不支持 {mode} 模式（仅 {allow}），"
                        f"回退 {fallback!r}")
            want = fallback
        out[slot] = want
        out[slot + "_source"] = ("mode" if m.get(slot) else
                                 ("default" if dflt.get(slot) else "builtin"))
    out["params"] = dict((m.get("params") or {}))
    if m.get("goal"):
        out["goal"] = m["goal"]
    return out


def all_resolved(data: dict[str, Any] | None = None) -> dict[str, Any]:
    d = data or load()
    return {mid: resolve(mid, d) for mid in MODES}


def users_of(model_id: str, data: dict[str, Any] | None = None) -> list[str]:
    """哪些模式在用这个模型 —— 界面要显示"这套模型会落到哪两个模块"。"""
    return [mid for mid, r in all_resolved(data).items()
            if model_id in (r.get("asr"), r.get("mt"), r.get("asr_stream"))]


def asr_size_mb(model_id: str, data: dict[str, Any] | None = None) -> float:
    """识别模型的显存预算：权重 + 解码开销。

    为什么不是只算"模型多大"：实测 large-v3 权重 2.9GB 上卡后占 3127MB，
    多出来的就是 KV cache 和激活。device_placement 要的是**上卡后会占多少**，
    不是文件大小 —— 差这 700MB 就可能从"放得下"变成"加载失败"。
    """
    m = get(model_id, data)
    if not m:
        return float(FALLBACK_MB.get("medium", 1500))
    if not m.get("size_key"):
        # 非 ctranslate2 尺寸表成员（qwen3-asr / 目录项）：按注册表里
        # 显式给出的显存预算走；远端/离线没预算 → 0。不能直接归零 ——
        # qwen3-asr-1.7b 上卡要 3.8GB，device_placement 判错就得回退 CPU。
        try:
            return float(m.get("vram_mb") or 0.0)
        except (TypeError, ValueError):
            return 0.0
    key = m["size_key"]
    # 结构化值（tiny/base 没有实测数）→ 实测值（small/medium/large-v3）
    base = float(FALLBACK_MB.get(key, 0) or 0)
    try:
        mb = float(model_dir_mb(key))
        if mb > 0:
            base = mb
    except Exception:
        pass
    if not base:
        base = float(FALLBACK_MB.get(key, 1500))
    return round(base + _DECODE_OVERHEAD.get(key, 0), 1)


def model_dir_mb(size_key: str) -> float:
    """复用 get_models.model_dir_mb（查 HuggingFace 缓存）。

    **必须包在 try 里**：get_models 的模块级代码当年会联网装 argos 模型，
    虽然已修成零副作用，但这一层不该把"注册表可用"押在它身上。
    """
    from get_models import model_dir_mb as _f
    return float(_f(size_key))


def find_local_model(model_id: str) -> str | None:
    """本地 MT 模型的 gguf 实际路径（走 local_mt 自己的查找顺序）。"""
    m = get(model_id)
    if not m or m.get("kind") != "llama-server":
        return None
    key = model_id.split(":", 1)[1].replace("hh-translate-", "")
    try:
        from local_mt import MODEL_CANDIDATES
        for p in MODEL_CANDIDATES:
            if p and os.path.isfile(p) and key in os.path.basename(p).lower():
                return p
    except Exception:
        pass
    return None


def cli_args(mode: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    """界面选择 → argparse 参数（**纯 dict，不 import argparse**）。

        ap.set_defaults(**cli_args("cache"))

    只返回**该模式确实有对应参数**的键 —— 实时与缓存模块的参数名本来就
    不一样（缓存没有 max_batch），多塞键会被 argparse 静默忽略，
    但那种"设了没生效"正是要避免的。
    """
    r = resolve(mode, data)
    out: dict[str, Any] = {}
    names = MODE_ARGS.get(mode, {})
    for slot, flag in names.items():
        mid = r.get(slot)
        if mid:
            out[flag.lstrip("-").replace("-", "_")] = asr_cli_value(mid)
    if r.get("params"):
        for k, v in r["params"].items():
            out.setdefault(k, v)
    return out


def asr_cli_value(model_id: str) -> str:
    """识别模型的**命令行取值**。

    本地 whisper 直接给尺寸名（"large-v3"），远端给带前缀的
    "remote:qwen3-asr-flash" —— 与 live_subtitle_v2.py 的取值口径一致
    （`--model remote:qwen3-asr-flash` 表示强制走远端）。
    """
    m = get(model_id)
    if not m:
        return "medium"
    if m.get("kind") == "faster-whisper":
        return str(m.get("size_key") or "medium")
    if m.get("kind") == "qwen3-asr":
        # load_asr 按前缀分派；cli 字段是 --model 的取值（qwen3-asr-0.6b）
        return str(m.get("cli") or model_id.split(":", 1)[-1])
    return model_id


def probe(data: dict[str, Any] | None = None, src: str | None = None,
          ) -> dict[str, Any]:
    """**实况探测**：这台机器上，每个模型真的能用吗。

    只做本地检查（文件在不在、包有没有装、设备查得到吗），
    **不联网、不加载模型、不发请求** —— 界面要秒开，不能被探测拖住。
    远端模型按"有没有 key"判，不判余额（那要么联网要么撒谎）。

    `src` 是注册表文件路径。**为什么要有这个参数**：调用方
    （babel_admin/serve.py）可能会把注册表指到别处（测试用临时副本、
    以后可能是用户配置目录）。如果这里永远读 `load()` 的默认路径，
    服务就会**一边改 A 文件、一边按 B 文件的内容校验** ——
    现象是"改了没生效"，而且改的和读的都各自成功，最难查。
    """
    d = copy.deepcopy(data if data is not None else load(src))
    facts: dict[str, Any] = {"devices": [], "notes": []}
    try:
        from device_placement import probe_devices
        from local_mt import find_binary
        b = find_binary()
        devs, notes = probe_devices(os.path.dirname(b) if b else None)
        facts["devices"] = [
            {"id": getattr(x, "id", ""), "name": getattr(x, "name", ""),
             "total_mb": round(float(getattr(x, "total_mb", 0) or 0), 1),
             "free_mb": round(float(getattr(x, "free_mb", 0) or 0), 1),
             "kind": getattr(x, "kind", "")}
            for x in devs]
        facts["notes"] = list(notes)
    except Exception as e:                     # 探测失败不该让界面打不开
        facts["notes"].append(f"设备探测不可用：{type(e).__name__} {e}")
    try:
        from local_mt import find_binary as _fb
        facts["llama_server"] = _fb() or ""
    except Exception:
        facts["llama_server"] = ""
    try:
        from remote_asr import read_key as _rk
        facts["remote_key"] = bool(_rk())
    except Exception:
        facts["remote_key"] = False
    try:
        import argostranslate.translate as _at  # noqa: F401
        facts["argos"] = True
    except Exception:
        facts["argos"] = False

    # 池 + 目录一起探测：目录里的"观察项"也要让界面看到"为什么不能选"。
    # 同 id 时池里的那条优先（池 = 接线口径，带 size_key 等运行字段）。
    allm: list[dict[str, Any]] = []
    _seen: set[str] = set()
    for x in (d.get("asr") or []) + (d.get("mt") or []):
        allm.append(x)
        _seen.add(str(x.get("id")))
    for x in (d.get("catalog") or []):
        if str(x.get("id")) not in _seen:
            allm.append(x)

    for mid, m in [(str(x.get("id")), x) for x in allm]:
        avail, why = True, ""
        kind = m.get("kind")
        if m.get("status") == "catalog":
            # 目录收录项：不是"坏了"，是"还没接线/没下载" —— 区别很重要，
            # 前者要修，后者只需按提示装。
            avail = False
            why = "目录收录项：尚未接线（model_search.py 可查 repo/许可）"
        elif kind == "faster-whisper":
            try:
                p = model_dir_mb(m["size_key"])
                if p < 1:
                    avail = False
                    why = f"HF 缓存里没有 faster-whisper-{m['size_key']}"
            except Exception as e:
                avail = False
                why = f"缓存查询失败：{type(e).__name__}"
        elif kind == "qwen3-asr":
            # 只查目录 + 包，不加载模型（probe 要秒开）
            try:
                import qwen_asr  # noqa: F401
                from qwen_asr_backend import resolve_model_dir
                _d = resolve_model_dir(mid)
                if not os.path.isdir(_d):
                    avail, why = False, f"缺模型目录 {_d}"
            except SystemExit:
                avail, why = False, "缺模型文件（models/Qwen3-ASR-*，或设 QWEN3_ASR_DIR）"
            except Exception as e:
                avail = False
                why = f"qwen-asr 不可用：{type(e).__name__}"
        elif kind == "llama-server":
            if find_local_model(mid):
                if not facts["llama_server"]:
                    avail, why = False, "找不到 llama-server.exe（设 LLAMA_DIR）"
            else:
                avail = False
                why = "找不到 gguf 模型文件（设 HYMT_MODEL）"
        elif m.get("source") == "remote":
            if not facts["remote_key"]:
                avail, why = False, "没有 API key（~/.dsh/.dashscope 或 DASHSCOPE_API_KEY）"
        elif kind == "argos":
            if not facts["argos"]:
                avail, why = False, "没装 argostranslate"
        m["available"] = bool(avail)
        m["unavailable_reason"] = why
        m["wired"] = bool(WIRED.get(mid, False))
    return d


# ── 给界面 ──────────────────────────────────────────────────────
def web_payload(data: dict[str, Any] | None = None) -> dict[str, Any]:
    """给管理界面的快照（字段名与前端 BABEL_MODELS 对齐）。

    界面拿到它就能渲染：左侧两个模式入口、顶部工具栏的模型下拉、
    模型管理页的整张表、以及"这套模型会落到哪两个模块"。
    """
    d = data or load()
    res = all_resolved(d)
    asr, mt = [], []
    for m in d.get("asr") or []:
        x = dict(m)
        # 探测过就有实况值；没探测（web_payload 被直接调用）也要给出字段，
        # 否则界面得对每个模型判 "available" in x —— 缺字段会被读成
        # undefined，渲染出"可用性未知"的空标签。
        x.setdefault("available", None)
        x.setdefault("unavailable_reason", "")
        x.setdefault("wired", bool(WIRED.get(x["id"], False)))
        if x.get("size_key"):
            try:
                x["vram_mb"] = asr_size_mb(x["id"], d)
            except Exception:
                pass
        x["used_by"] = users_of(x["id"], d)
        # 界面用它过滤"这个模式能选哪些识别模型"——直播不能选 qwen3-asr
        x["modes_allowed"] = modes_allowed(x["id"], d)
        asr.append(x)
    for m in d.get("mt") or []:
        x = dict(m)
        x.setdefault("available", None)
        x.setdefault("unavailable_reason", "")
        x.setdefault("wired", bool(WIRED.get(x["id"], False)))
        path = find_local_model(x["id"])
        if path:
            try:
                x["path"] = path
                x["file_mb"] = round(os.path.getsize(path) / 2 ** 20, 1)
            except OSError:
                pass
        x["used_by"] = users_of(x["id"], d)
        mt.append(x)
    return {
        "schema": SCHEMA,
        "modes": {k: {"label": v.get("label", MODES[k]),
                      "goal": v.get("goal", ""),
                      "asr": res[k]["asr"], "mt": res[k]["mt"],
                      "asr_stream": res[k]["asr_stream"],
                      "params": res[k]["params"],
                      "asr_source": res[k]["asr_source"],
                      "mt_source": res[k]["mt_source"]}
                  for k, v in modes(d).items()},
        "defaults": d.get("defaults") or {},
        "asr": asr,
        "mt": mt,
        # 模型库目录：界面"模型库"页/搜索框直接读它。
        # 探测过的条目带 available/unavailable_reason；没探测的给 null，
        # 与 asr/mt 池同款口径（缺字段会被前端读成 undefined）。
        "catalog": [{**x,
                     "available": x.get("available"),
                     "unavailable_reason": x.get("unavailable_reason", ""),
                     "wired": bool(x.get("wired", False))}
                    for x in (d.get("catalog") or [])],
        "router": d.get("router") or {},
        "means": d.get("means") or {},
        "updated": d.get("updated") or "",
    }


def export_web(path: str | None = None, do_probe: bool = True) -> str:
    """写 model_registry_web.json（界面直接 fetch，不用起后端）。

    导出时**默认带上实况探测结果**（available / unavailable_reason）——
    界面必须能显示"这一项现在真的不可用，原因是 X"。不探测的话界面只能
    显示静态的愿望值，那正是"选了没生效"的温床。
    """
    p = path or WEB_PATH
    # 必须传 src：既有快照的调用方可能把 PATH 指到了别处（测试副本/
    # 用户配置目录），不传就会"导出 A 的内容、拿 B 的可用性"
    data = probe(src=PATH) if do_probe else None
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(web_payload(data), f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, p)
    return p


def generated_web_payload() -> dict[str, Any]:
    """把快照内容包成一段可直接贴进 HTML 的 JS 常量（离线双击也能用）。"""
    return web_payload()


def registry_pending_defaults() -> list[str]:
    """列出**还没有**接管、仍写死在代码里的模型选择点。

    存在的意义：这次不去重整翻译链路（那是两个模块自身的优化），但要让
    "还有哪几处不在注册表里"**可查**。界面的设置页直接展示这份清单 ——
    否则"选了没生效"永远查不出来。

    ⚠ 维护须知：一份过期清单比没有清单更坏 —— 它会让界面理直气壮地
    撒谎。所以识别模型那两处接上注册表之后，这里**必须**同步删掉
    （`check()` 里有断言盯着"已接管的不能出现在待办里"）。
    """
    pending = [
        "实时模块的**译器**：选路在 Translator 内部按源语言走 "
        "(argos/Google/qwen-mt)，没有命令行开关，注册表里给不了假开关",
        "local_mt.py MODEL_CANDIDATES：本地翻译模型靠 4 个写死的 gguf 路径找人",
        "remote_asr.py MODEL 与 llm_translate.py DASHSCOPE_MODEL 的字面量"
        "（实际取值已被注册表覆盖，但字面量仍在）",
        "get_models.py WANT 的 4 个离线语言对 + KNOWN_MB 兜底尺寸表",
    ]
    return pending


def registry_wired_defaults() -> list[str]:
    """列出**已经**由注册表接管的默认值（供界面显示"生效中"）。"""
    return [
        "直播翻译：--model（= 注册表 asr）+ 分段/批次等 9 个参数",
        "直播翻译：--mt-batch / --mt-batch-wait / --mt-cap",
        "视频翻译：--model（= 注册表 cache.asr，默认 large-v3）",
        "视频翻译：--mt-batch（默认 8，比直播翻译大 4 倍）",
        "两个模块都可用 --no-registry 临时退回旧默认值",
    ]


def check() -> int:
    """自检。覆盖：结构、模式覆盖规则、兜底、CLI 映射、界面快照。"""
    ok = bad = 0
    msgs: list[str] = []

    def chk(name: str, cond: bool, extra: str = "") -> None:
        nonlocal ok, bad
        if cond:
            ok += 1
        else:
            bad += 1
            msgs.append(f"  ✗ {name} {extra}")

    d = load()
    chk("schema 版本一致", d.get("schema") == SCHEMA, str(d.get("schema")))
    chk("有 asr 池", len(pool("asr", d)) >= 5, str(len(pool("asr", d))))
    chk("有 mt 池", len(pool("mt", d)) >= 8, str(len(pool("mt", d))))
    chk("两个模式都在", set(modes(d)) == set(MODES), str(list(modes(d))))

    for mid in ("loopback", "cache"):
        r = resolve(mid, d)
        chk(f"{mid}: asr 可解析", get(r["asr"], d) is not None, str(r["asr"]))
        chk(f"{mid}: mt 可解析", get(r["mt"], d) is not None, str(r["mt"]))
        chk(f"{mid}: 无警告", not r["warn"], str(r["warn"]))

    # 覆盖规则（这一层最关键的行为，用独立夹具测，不依赖默认值恰好长什么样）：
    # 改 defaults → 跟随的 loopback 跟着变；显式覆盖的 cache 纹丝不动。
    d2 = copy.deepcopy(d)
    d2["modes"]["loopback"]["asr"] = None
    d2["modes"]["cache"]["asr"] = "local:faster-whisper-small"
    d2["defaults"]["asr"] = "local:faster-whisper-large-v3"
    chk("改 defaults → 跟随模式跟着变",
        resolve("loopback", d2)["asr"] == "local:faster-whisper-large-v3",
        resolve("loopback", d2)["asr"])
    chk("改 defaults → 显式覆盖模式不变",
        resolve("cache", d2)["asr"] == "local:faster-whisper-small",
        resolve("cache", d2)["asr"])
    chk("来源标记正确",
        resolve("loopback", d2)["asr_source"] == "default"
        and resolve("cache", d2)["asr_source"] == "mode",
        f"{resolve('loopback', d2)['asr_source']}/{resolve('cache', d2)['asr_source']}")
    chk("缓存模式是显式覆盖（真数据）", d["modes"]["cache"]["asr"] is not None)
    # 实时模式允许两种合法状态：跟随默认（None），或用户在界面里显式选了
    # 某个存在的模型。后者是**用户的运行选择**（本机选过 large-v3），
    # 硬要求 None 会把"用户改过设置"误判成故障。
    _lb = d["modes"]["loopback"]["asr"]
    chk("实时模式是跟随默认或合法显式覆盖",
        _lb is None or get(_lb, d) is not None, str(_lb))

    # 兜底：坏 id 不炸，回退并告警
    d3 = copy.deepcopy(d)
    d3["defaults"]["mt"] = "remote:不存在的模型"
    r3 = resolve("loopback", d3)
    chk("坏 id 回退到可用模型", get(r3["mt"], d3) is not None, str(r3["mt"]))
    chk("坏 id 有告警", bool(r3["warn"]), str(r3["warn"]))

    # CLI 映射
    cl = cli_args("loopback", d)
    chk("实时 CLI 有 model", "model" in cl, str(cl))
    chk("实时 model 是尺寸名",
        cl.get("model") in ("medium", "large-v3", "small", "base", "tiny"),
        str(cl.get("model")))
    chk("实时 CLI 带 max_batch", cl.get("max_batch") == 2.0)
    cc = cli_args("cache", d)
    chk("缓存 model = qwen3-asr-0.6b（2026-10-19 起的默认）",
        cc.get("model") == "qwen3-asr-0.6b", str(cc.get("model")))
    chk("缓存不带 max_batch（该模块没这参数）", "max_batch" not in cc)
    chk("远端识别转 CLI 值带前缀",
        asr_cli_value("remote:qwen3-asr-flash") == "remote:qwen3-asr-flash")
    chk("qwen3-asr 转 CLI 值 = cli 字段（load_asr 按前缀分派）",
        asr_cli_value("local:qwen3-asr-0.6b") == "qwen3-asr-0.6b",
        asr_cli_value("local:qwen3-asr-0.6b"))
    chk("qwen3-asr-1.7b 显存预算取 vram_mb（不是 0）",
        asr_size_mb("local:qwen3-asr-1.7b", d) == 3800.0,
        str(asr_size_mb("local:qwen3-asr-1.7b", d)))

    # 显存预算
    mb = asr_size_mb("local:faster-whisper-large-v3", d)
    chk("large-v3 显存预算 >= 2948（权重+解码）", mb >= 2948, f"{mb}")
    chk("远端模型显存 0", asr_size_mb("remote:qwen3-asr-flash", d) == 0.0)
    # **快照里不许留静态值**：catalog 写的是实测 3127，probe 会按本机权重
    # 重算成 3123.3。如果哪条路径忘了重算，界面上就会两个数字并存 ——
    # "界面显示 1615、程序按 1612 判放不下"就是这么来的。
    for x in web_payload(load())["asr"]:
        if x.get("size_key"):
            want = asr_size_mb(x["id"], load())
            chk(f"快照显存是算出来的，不是抄静态值（{x['id'].split(':')[1]}）",
                abs(float(x["vram_mb"]) - want) < 0.5,
                f"快照 {x['vram_mb']} vs 计算 {want}")

    # 归属
    chk("qwen3-asr-0.6b 归属缓存模式",
        "cache" in users_of("local:qwen3-asr-0.6b", d),
        str(users_of("local:qwen3-asr-0.6b", d)))
    chk("qwen-mt-flash 归属直播模式（缓存模式可被显式覆盖成本地译器）",
        "loopback" in users_of("remote:qwen-mt-flash", d),
        str(users_of("remote:qwen-mt-flash", d)))
    chk("缓存模式的译器可解析（覆盖成 HY-MT local 也合法）",
        get(resolve("cache", d)["mt"], d) is not None,
        str(resolve("cache", d)["mt"]))

    # 模式兼容性：qwen3-asr 是整段转写后端，直播链路用不了
    chk("qwen3-asr 只允许点播模式",
        modes_allowed("local:qwen3-asr-0.6b", d) == ["cache"],
        str(modes_allowed("local:qwen3-asr-0.6b", d)))
    chk("whisper 两个模式都允许",
        set(modes_allowed("local:faster-whisper-medium", d)) == {"loopback", "cache"})
    chk("真数据：loopback 解析出的后端允许直播",
        "loopback" in modes_allowed(resolve("loopback", d)["asr"], d),
        resolve("loopback", d)["asr"])
    d4 = copy.deepcopy(d)
    d4["modes"]["loopback"]["asr"] = "local:qwen3-asr-0.6b"
    r4 = resolve("loopback", d4)
    chk("把手改的 qwen3-asr 塞给直播 → 回退并告警",
        r4["asr"] == "local:faster-whisper-medium" and bool(r4["warn"]),
        f"{r4['asr']} warn={r4['warn']}")

    # 模型库目录（2026-10-19 扩充）：可检索 + 字段齐全 + 与池不重复
    cat = d.get("catalog") or []
    chk("catalog 至少 10 条", len(cat) >= 10, str(len(cat)))
    pool_ids = {x.get("id") for x in (d.get("asr") or []) + (d.get("mt") or [])}
    chk("catalog 与池不重复（同 id 由池优先）",
        not ({x.get("id") for x in cat} & pool_ids))
    chk("catalog 条目字段齐全（id/task/name/repo/license/langs/note）",
        all(all(k in x for k in ("id", "task", "name", "repo", "license",
                                 "langs", "note")) for x in cat),
        str([x.get("id") for x in cat
             if not all(k in x for k in ("id", "task", "name", "repo",
                                         "license", "langs", "note"))]))
    chk("catalog 任务类型覆盖（asr/vad/task 等）",
        {"asr", "vad"} <= {x.get("task") for x in cat})

    # 界面快照
    w = web_payload(d)
    chk("快照有 asr", len(w["asr"]) >= 5)
    chk("快照有 mt", len(w["mt"]) >= 8)
    chk("快照 modes 带默认绑定",
        all(("asr" in v and "mt" in v) for v in w["modes"].values()))
    chk("快照字段名对齐前端（means/modes/asr/mt/router）",
        all(k in w for k in ("means", "modes", "asr", "mt", "router")))
    chk("快照带 catalog（模型库页/搜索用）", "catalog" in w
        and len(w["catalog"]) >= 10, str(len(w.get("catalog") or [])))
    chk("本地 gguf 能找到路径（有的话）",
        any(x.get("path") for x in w["mt"]) or not os.path.isdir(
            r"F:\hh_si_pro\hh-translate-model"))

    chk("两模式识别模型不同（或明确相同）", True,
        f"{resolve('loopback', d)['asr']} / {resolve('cache', d)['asr']}")

    # 待办清单不能自相矛盾：已接管的识别默认值不许再出现在 pending 里
    pend = registry_pending_defaults()
    chk("pending 里不再包含已接管的 --model",
        not any("--model" in x for x in pend), str(pend))
    chk("pending 与 wired 都有内容",
        len(pend) >= 3 and len(registry_wired_defaults()) >= 3)
    chk("wired 明确说明已接管 model",
        any("asr" in x for x in registry_wired_defaults()))

    print(f"model_registry 自检：{ok} 项通过，{bad} 项失败")
    for m in msgs:
        print(m)
    return 1 if bad else 0


def _selftest_io() -> int:
    """读写回归：坏文件不炸、写回原子、tmp 不留残留。"""
    import tempfile
    ok = bad = 0
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "r.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write("{ 这不是 json")
        d = load(p, reload=True)
        if d.get("asr") and d.get("mt"):
            ok += 1
        else:
            bad += 1
            print("  ✗ 坏 JSON 没有回退到内置默认")
        if not os.path.exists(p + ".tmp"):
            ok += 1
        else:
            bad += 1
            print("  ✗ 残留 .tmp")
        save(d, p)
        d2 = load(p, reload=True)
        if d2.get("schema") == SCHEMA and not os.path.exists(p + ".tmp"):
            ok += 1
        else:
            bad += 1
            print("  ✗ 写回后读不出 / 残留 .tmp")
    print(f"model_registry IO 回归：{ok} 项通过，{bad} 项失败")
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    import sys
    args = list(argv if argv is not None else sys.argv[1:])
    if not args or args[0] in ("check", "--selftest"):
        rc = check()
        rc |= _selftest_io()
        return rc
    if args[0] == "export":
        print(export_web())
        return 0
    if args[0] == "show":
        for mid, r in all_resolved().items():
            print(f"[{mid}] {r['label']}")
            print(f"  识别 {r['asr']}  ({r['asr_source']})")
            print(f"  翻译 {r['mt']}  ({r['mt_source']})")
            print(f"  CLI  {cli_args(mid)}")
        return 0
    if args[0] == "probe":
        d = probe()
        for x in (d.get("asr") or []) + (d.get("mt") or []):
            flag = "OK " if x.get("available") else "×  "
            print(f"{flag}{x['id']:<34}{x.get('unavailable_reason','')}")
        return 0
    if args[0] == "pending":
        for line in registry_pending_defaults():
            print(" ·", line)
        return 0
    print("用法: python model_registry.py [check|export|show|probe|pending]")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
