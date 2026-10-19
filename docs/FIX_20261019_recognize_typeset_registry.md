# 2026-10-19 修复与新增：识别链路 / 第三方排版 / 模型仓库

## 一、视频翻译（网页视频）识别失败：`预翻译失败：全部完成：0/1`

### 现象
`watch --auto` 抓到 B 站视频后，反查网址、下载音频、解码全部正常，
到识别阶段 18 秒后直接报：

```
状态: 预翻译中 00:17｜识别模型 qwen3-asr-0.6b（qwen-asr / cuda）
[autopipe] 预翻译失败（预翻译失败：全部完成：0/1）
```

同一时间 `run.log` 里直播链路（faster-whisper large-v3 / float16）是好的。

### 根因（两层，叠在一起才炸）
1. **torch 是 CPU 构建**：系统 Python 3.12 里 `torch 2.13.0+cpu`，
   `torch.version.cuda = None`、`torch.cuda.is_available() = False`。
2. **设备选择用错了判据**：`qwen_asr_backend.load_qwen3_asr()` 原来调
   `gpu_setup.gpu_available()` —— 那个函数反映的是 **ctranslate2（whisper）
   的 CUDA 可用性**。本机 ctranslate2 有 CUDA、torch 没有，于是选中 `cuda`，
   `Qwen3ASRModel.from_pretrained(device_map="cuda")` 抛
   `AssertionError: Torch not compiled with CUDA enabled` → 上层捕获后
   统计为 0 段 → "全部完成：0/1"。whisper 链路不受影响，所以只有
   视频翻译（缓存模式默认 qwen3-asr）挂。

### 修复
- 装上 CUDA 版 torch：`torch==2.13.0+cu126`（与已装版本同号，只是换构建），
  验证：`2.13.0+cu126 12.6 True NVIDIA GeForce RTX 4070 Laptop GPU`。
- `qwen_asr_backend.load_qwen3_asr()` 改用 `torch.cuda.is_available()` 判定；
  torch 无 CUDA 时**显式回退 CPU 并打印原因**，不再让上层收到 0 段。
- `model_registry.json` 的 `modes.cache.asr` 归位到 `local:qwen3-asr-0.6b`
  （界面改模型时被写成 large-v3，与代码默认和自检期望不一致）。

### 验证（本机实测）
| 场景 | 结果 |
|---|---|
| `qwen_asr_backend.py samples/tts_vi.wav vi`（CUDA） | 5 段，18s 音频用时 7s（CPU 13s） |
| Qwen3-ASR-1.7B / cuda fp16 | 5 段，18s 音频用时 5s，加载 4.7s |
| `pretranslate.py samples/e2e_speech.wav --to zh --force`（走 watch 同款默认参数） | 4 条字幕 + 4 条译文，4s |
| `model_registry.py` 自检 | 53 通过 / 0 失败（IO 回归 3/0） |
| `live_subtitle_v2.py --selftest` | 174 通过 / 0 失败 |

## 二、第三方排版模块（pysubs2 → ASS）

### 为什么加
SRT 本身没有样式能力：字体、字号、描边、阴影、边距、对齐全靠播放器默认，
中文常被渲染成白字无描边、深色画面糊成一片。现有 `subtitle_layout.py`
只解决"断句/切条"，不解决"长什么样"。

### 新增 `subtitle_typeset.py`
- 依赖 **pysubs2 1.9.0**（业界通用的 ASS/SSA 读写库）。
- 三套样式预设：`ass`（双语：中文 58px 主行 + 原文 34px 小字，`\rSRC` 切样式）、
  `minimal`（单语极简）、`reading`（60px + 半透明底框 3 号边框）。
- 画布 1920×1080，左右安全边距 180px、底部 54px，`WrapStyle=2`
  （只认 `\N` 换行，不让播放器硬折），`\fad(120,120)` 淡入淡出。
- 换行自研：每行 ≤24 视觉汉字（拉丁按 0.55 折算），拉丁**只在空格断**，
  修掉 "Adiós." 被切成 "Adi"/"Nós" 的问题；双语时原文自动识别
  谁在前（pretranslate 与 live 两条链路的双语顺序相反，按"哪行更像中文"判）。
- 集成：`pretranslate.py` 每次 `write_srt()` 落盘后同步重排 `.ass`
  （`--typeset ass|minimal|reading|none`，默认 ass；缓存命中也会补排）。

### 性能论证（bench/bench_typeset.py，15 次取中位）

| 字幕文件 | 条数 | 排版总耗时 | 单条 | 占 Hy-MT2 翻译预算 |
|---|---|---|---|---|
| 越南新闻（VTV1） | 2242 | 43.57 ms | 0.0194 ms | 0.016% |
| 46 分钟 FIRE 动画 | 655 | 15.35 ms | 0.0234 ms | 0.020% |
| FX 战士久留美 第1话 | 128 | 4.99 ms | 0.0390 ms | 0.032% |
| live.srt（8 条） | 8 | 2.19 ms | 0.2743 ms | 0.229% |

结论：
- 排版是**翻译落盘之后的纯 CPU 字符串处理**，不发请求、不占 GPU，
  与 ASR/MT 不共享资源，不改变首屏字幕到达时间（首屏由首块识别+翻译决定）。
- 单条 0.02~0.04 ms，整集（千条级）<50 ms；对照本地翻译 120 ms/段、
  远端 360 ms/段，占比 **0.006%~0.03%**。
- 唯一可见成本：每批 flush 多一次文件写（ASS 体积约为 SRT 的 2.5 倍，
  2242 条 = 326 KB），实测均摊 7~12 ms/次。

## 三、模型仓库：新模型不可用 + 覆盖不全

### 修好并接线的三个模型
| 模型 | 原状态 | 现在 |
|---|---|---|
| `local:qwen3-asr-1.7B` | 只有代码条目，`models/Qwen3-ASR-1.7B` 不存在 → 不可用 | 已下载（modelscope，2 个分片），CUDA fp16 实测 5 段/18s 用 5s |
| `local:hy-mt2-1.8b` | `status=catalog`（未接线） | 已下载 Q4_K_M（1.13GB，F 盘），接进 `local_mt` |
| `local:translategemma-4b` | `status=catalog`（未接线） | 已下载 Q4_K_M（2.49GB），接进 `local_mt` |

### 接线细节（`local_mt.py`）
- `detect_family()` 改成先读 GGUF 的 `general.architecture`/`general.name`，
  再退回 bos id：`gemma3` → `gemma`；`hunyuan-dense` + bos 120000 →
  沿用 `hy18` 的 hy_ 标记（实测输出正确，0.08~0.12s/段）。
- 新增 `resolve_model()`：接受路径 / 注册表 id（`local:hy-mt2-1.8b`）/
  名字片段，找不到就报错，不静默换模型。
- `gemma` 家族：TranslateGemma 的 chat_template 要求结构化 content
  （type/source_lang_code/target_lang_code/text），llama-server 在 init 期
  渲染假消息就抛 jinja 异常直接退出。处理方式：
  - 启动加 `--no-jinja`；
  - 自己按模板原文拼 `<start_of_turn>user …<end_of_turn>\n<start_of_turn>model\n`
    走 `/completion`；源语言码用 `set_src()` 或按文本粗判（vi/zh/ja/ko/ru/en）。
- `model_manager.py` 新增 `_MT_EXTRA`，模型管理页能列出并显示这两个已装。

### 目录补全（catalog）
新增 5 条：`faster-whisper-large-v3-turbo`、`Hy-MT2-7B-GGUF`、
`Hy-MT2-30B-A3B`、`TranslateGemma-12B`、`TranslateGemma-27B`。

### 探测结果（`python model_registry.py probe`）
20 个可选模型全部 OK —— 8 个 ASR（含 qwen3-asr-0.6b/1.7b）+ 12 个 MT
（含 hy-mt2-1.8b / translategemma-4b），0 个不可用。

## 改动文件
```
qwen_asr_backend.py     设备判据改 torch.cuda.is_available()，CPU 回退可解释
local_mt.py             detect_family 读 GGUF 元数据；gemma 模板；resolve_model；set_src
subtitle_typeset.py     新增：pysubs2 排版层（ass/minimal/reading）
pretranslate.py         --typeset 参数；每次落盘同步重排 .ass；缓存命中补排；排版耗时上报
model_registry.py       两个 MT 转正 + WIRED；catalog +5；2 项自检改为允许用户显式覆盖
model_manager.py        _MT_EXTRA + mt_extra_installed()
model_registry.json     上述条目 + cache.asr 归位 qwen3-asr-0.6b
requirements.txt        pysubs2；torch cu126 安装提示
bench/bench_typeset.py  新增：排版 vs 翻译可复现基准
```
