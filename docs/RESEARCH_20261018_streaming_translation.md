# live-subtitle 全网调研：流式识别 / 同传 / 字幕工程（2026-10-18）

**范围**：模块 A「直播翻译（loopback）」与模块 B「视频翻译（cache）」的算法与工程改进。
**时间窗**：2023–2026 优先，保留少量更早的奠基工作（AlignAtt 之前、AL/LAAL 定义）。
**证据口径**（全文统一）：

- 【论文】= 我读到的原文/官方 README 明确写的结论，可直接引用；括号里给来源编号。
- 【推断】= 我基于本项目代码与上述结论做的判断，**没有实测**，需要 POC 验证。
- **未核实** = 我没有拿到原文数值（页面打不开、只读到摘要、或摘要未给数），只保留机制描述。
- 日期：调研执行于 2026-10-18；标注「最后更新」的官方文档按页面显示为准。

---

## 0. 摘要

**一句话**：不换大模型也能明显变好——先补三件工程件（术语表、字幕规整、幻觉闸门），再谈换算法。

1. A 模块（直播）现有骨架方向是对的：边收边识别、只确认「两次结果里都出现过的词」。问题不在骨架，在**确认得不够聪明**和**字幕没人管格式**。
2. 2026 年最值得跟的一条线叫 **AlignAtt4LLM**：让大模型翻译时「看一眼原文注意到哪了」，够了就先吐一半译文。它做的正是我们这种「远端识别 + LLM 翻译」的流水线，论文报在 2 秒级延迟下优于基线（英→德/意；英→中效果混合）【论文 7】。
3. 最便宜的收益在三处，都不动模型：**术语表接到 Qwen-MT 官方术语字段**（官方支持，我们没用）【官方 22】、**字幕按 Netflix 规范规整**（42 字符/行、≤2 行、成人 ≤20 CPS）【官方 25】、**幻觉闸门**（Whisper 约 1% 的转写会凭空编出整句，静音越多越容易）【论文 19】。
4. B 模块（视频）有免费提速：faster-whisper 批推理，官方基准 13 分钟音频从 63 秒降到 17 秒（显存 6.1 GB），8 GB 卡刚好【论文 12】。
5. 不建议现在就把 A 模块换成 SimulStreaming/AlignAtt 原版：它要读模型内部的注意力，faster-whisper 不提供；官方还要求 ≥10 GB 显存跑大模型【论文 3】，我们是 8 GB——先做小规模验证再决定【推断】。
6. 马上能开工的 5 件：P0-1 术语表、P0-2 字幕规整、P0-3 幻觉闸门、P1-1 识别与翻译并行、P1-2 视频批推理（见 §2）。

---

## 1. 论文 / 项目表

> 「适用性」列：A = 直播翻译模块，B = 视频预翻译模块；高/中/低 = 我在本项目现状下的判断（成本已计入）。

| # | 名称 | 年份 | 链接 | 核心思想 | 关键数据（来源） | 适用性 |
|---|---|---|---|---|---|---|
| 1 | Turning Whisper into Real-Time Transcription System（Whisper-Streaming） | 2023 | https://arxiv.org/abs/2307.14743 ；https://aclanthology.org/2023.ijcnlp-demo.3/ | 用 **LocalAgreement** 策略 + 自适应延迟，把离线 Whisper 变成流式；确认两次相邻输出的最长公共前缀 | 【论文】在无分段长音频测试集上 3.3 s 延迟，并在多语会议直播中实际使用 | A 高（本项目已是同源实现） |
| 2 | whisper_streaming（参考实现） | 2023– | https://github.com/ufal/whisper_streaming | 上述论文的代码；LocalAgreement + 自适应延迟的工程实现 | 本次**未逐页核验**该仓库 README；其 server/文件模拟接口与 VAC 控制器在 SimulStreaming README 中有描述（“copied, modified” 自本仓库）【论文 3】 | A 高（可逐行对照） |
| 3 | SimulStreaming | 2025 | https://github.com/ufal/SimulStreaming | 合并 Simul-Whisper（AlignAtt）与 Whisper-Streaming；VAC + 术语/上下文 prompt；Whisper + EuroLLM 级联 | 【论文】README：比上一代 WhisperStreaming **快约 5×**；推荐 **≥10 GB 显存** 跑 large-v3(1.5B)；自述 IWSLT 2025 同传赛道最佳 | A 中（8 GB 显存吃紧，需 POC） |
| 4 | Simultaneous Translation with Offline Speech and LLM Models（CUNI @ IWSLT 2025） | 2025 | https://arxiv.org/abs/2506.17077 ；https://aclanthology.org/2025.iwslt-1.41/ | AlignAtt + 领域术语 prompt + EuroLLM 无界同传；提出改进的 ASR 延迟度量 | 【论文】相对官方基线：cs→en **+2 BLEU**，en→de/zh/ja **+13–22 BLEU**（dev set） | A/B 高（级联 + 术语注入最贴近本项目） |
| 5 | AlignAtt | 2023（Interspeech） | https://arxiv.org/abs/2305.11408 | 用 decoder 对 encoder 的 **cross-attention** 判断「危险区」，解码到接近音频末尾就停，下一块继续 | 【论文】MuST-C v1.0 八个语言对：比之前 SOTA 策略 **+2 BLEU**，延迟 **降低 0.5–0.8 s** | A 中（要 attention，CTranslate2 拿不到）【推断】 |
| 6 | Simul-Whisper | 2024（Interspeech） | https://arxiv.org/abs/2406.10052 | 用 cross-attention 的时间对齐指导自回归解码，做 chunk 级流式；再加 CIF 截断检测，专门治 chunk 边界被切断的词 | 【论文】1 s chunk 下平均绝对 WER 退化仅 **1.46%**，优于当时 SOTA 基线；无需微调 | A 中（同上，需 HF/原生实现） |
| 7 | AlignAtt4LLM（IWSLT 2026） | 2026 | https://aclanthology.org/2026.iwslt-1.32/ | **首个把 AlignAtt 用到 decoder-only LLM 翻译**：prompt 内显式给 source span、离线选翻译相关对齐头、qk 重放，得到「注意力门控的追加式提交」 | 【论文】IWSLT 2026 dev：en→de、en→it 在 **约 2 s 低延迟**与 **<4 s CU-LongYAAL** 两个区间均优于官方基线；en→zh 结果混合；方法不绑定 Gemma-4 | A 高（正对「ASR 前缀 + LLM 增量翻译」） |
| 8 | A Pocket Offline Model for Simultaneous ST（IWSLT 2026） | 2026 | https://aclanthology.org/2026.iwslt-1.22/ | 用 NeMo + **Canary-1B-v2**（1B 参数）+ AlignAtt 做离线直接语音翻译，目标是小型设备 | 【论文】摘要未给 BLEU/延迟数值 → **未核实** | B 中/高（离线、单模型、1B 可上 8 GB） |
| 9 | Moonshine | 2024 | https://arxiv.org/abs/2410.15608 | 面向实时转写的编码器-解码器；RoPE 取代绝对位置编码；按变长训练、不 padding，编码器算力随音频长度走 | 【论文】对比 Whisper tiny-en，10 s 片段**算力降 5×**，WER 不升 | A 中（英文场景；中文表现**未核实**） |
| 10 | Seamless / SeamlessM4T v2 / SeamlessStreaming | 2023 | https://arxiv.org/abs/2312.05187 | EMMA（单调多头注意力）：不等源句说完就出译文；含 SeamlessExpressive 与流式语音到语音/文本 | 【论文】首个多语种流式 S2ST/S2TT 公开系统；具体 BLEU/延迟**未核实**（只读摘要） | A 低（模型体量与显存不适合消费级） |
| 11 | WhisperX | 2023（Interspeech） | https://arxiv.org/abs/2303.00747 | VAD **Cut & Merge** 预分段 + 强制音素对齐拿词级时间戳；预分段让批推理可用 | 【论文】VAD 预分段带来 **12× 批推理加速**；长音频转写与词分割达到当时 SOTA | B 高（离线批处理正合适） |
| 12 | faster-whisper | 2024– | https://github.com/SYSTRAN/faster-whisper | CTranslate2 重实现；`BatchedInferencePipeline` 支持批推理；内置 Silero VAD | 【论文】README 基准（13 min 音频，RTX 3070 Ti 8 GB）：large-v2 fp16 beam5 **1m03s / 4525 MB**；`batch_size=8` **17s / 6090 MB**；int8 单品 59s / 2926 MB | B 高 / A 中 |
| 13 | WhisperLiveKit（WLK） | 2025–2026 | https://github.com/QuentinFuxa/WhisperLiveKit | 把 SimulStreaming/AlignAtt、LocalAgreement、VAD/VAC、翻译（NLLB / AlignAtt4LLM sidecar）打成一个服务；`context` 参数做术语条件化 | 【论文】README 默认值可直接抄：`--frame-threshold 25`、`--audio-max-len 30.0`、`--pause-segmentation-seconds 5.0`、`--asr-coalesce-min-s`（攒够新音频再跑 ASR，省编码器开销） | A 高（本项目已有 `tools/_bench_wlk*.py` 对比脚本） |
| 14 | WhisperLive（Collabora） | 2024– | https://github.com/collabora/WhisperLive | 服务端流式转写：faster-whisper / TensorRT / OpenVINO 后端；hotwords 热词、批推理、词级时间戳、在线说话人分离 | 【论文】README：`--batch_inference --batch_max_size 8 --batch_window_ms 50`；`hotwords` 透传给 faster-whisper 关键词增强；端到端延迟数值**未核实** | A 中（架构偏服务端多客户端） |
| 15 | RealtimeSTT | 2024–2026 | https://github.com/KoljaB/RealtimeSTT | 麦克风/外部音频 → VAD → 实时草稿 + 终稿；**两段式**：流式小模型出草稿，turn 结束时用强模型重解一次 | 【论文】README：CPU 生产配置 = `nemotron-3.5-asr-streaming-0.6b-560ms-int8` 出实时文本 + `parakeet-tdt-0.6b-v3-int8` 在 finalization 重解；延迟/准确率数值**未核实** | A 高（终稿重解码模式可直接搬） |
| 16 | whisper.cpp | 2023– | https://github.com/ggml-org/whisper.cpp | C/C++ 推理；`examples/stream` 为实时示例（本次已核验该目录存在）；另含 `parakeet-cli` | 【论文】仓库结构核验；实时延迟数值**未核实**（依硬件） | A 低/中（要跨语言集成，收益主要是省 Python 开销） |
| 17 | Vibe | 2024–2026 | https://github.com/thewh1teagle/vibe | Tauri 桌面转写器：系统音频/麦克风、实时预览、字幕长度预设、**stable timestamps 模式（VAD 支撑、更慢）** | 【论文】README 功能项；具体时间轴精度**未核实** | B 中（UX 与「稳定时间戳」产品化参照） |
| 18 | Silero VAD | 2021– | https://github.com/snakers4/silero-vad | 轻量 VAD（JIT ~2 MB），8k/16k 采样，MIT | 【论文】README：单 CPU 线程处理 30+ ms 音频块 **<1 ms**；训练语料覆盖 6000+ 语言 | A/B 高（已在用 faster-whisper 内置版） |
| 19 | Careless Whisper: Speech-to-Text Hallucination Harms | 2024（FAccT） | https://arxiv.org/abs/2402.08021 | 系统测量 Whisper 幻觉：静音/非人声比例高的语音更易整句编造 | 【论文】约 **1%** 转写含完全不存在的整句；其中 **38%** 含显性有害内容；失语症说话人（非人声占比高）幻觉率显著更高 | A 高（直接支撑幻觉闸门） |
| 20 | OpenAI Whisper（模型表 & turbo） | 2022– | https://github.com/openai/whisper | 多语种 ASR/翻译基座；`turbo` 是 large-v3 的**优化加速版**（官方措辞：minimal degradation） | 【论文】README：turbo **809 M**、约 **6 GB** 显存、相对速度 **~8×**，精度「minimal degradation」；并明确 **turbo 未针对翻译任务训练**（`--task translate` 会返回原语言） | B 高（纯转写提速）/ A 中 |
| 21 | Qwen3-ASR-Flash | 2025–2026 | https://www.alibabacloud.com/help/en/model-studio/qwen3-asr-flash | LLM 基座的多语种 ASR，自动判语言；官方称在复杂音频环境下仍稳定 | 【官方】按音频时长计费 $0.000032/s（北京）、RPM **100**；不支持批量推理/微调；具体 WER **未核实** | A 高（项目已在用） |
| 22 | Qwen-MT-Flash | 2026（页面最后更新 2026-10-11） | https://www.alibabacloud.com/help/en/model-studio/qwen-mt-flash | Qwen3 架构翻译模型，92 语种，**支持术语干预、格式保留、领域适配** | 【官方】上下文 16384，最大输入/输出 8192；RPM **60**、TPM 35 000（新加坡 100 000）；不批量/不微调 | A 高（项目已用，但未用术语字段） |
| 23 | Length-Adaptive Average Lagging（LAAL） | 2022 | https://aclanthology.org/2022.autosimtrans-1.2/ | 指出 Average Lagging 在「过度生成」时会被奖励，提出按长度自适应的 LAAL | 【论文】给出 LAAL 定义与反例；具体数值**未核实** | 评测 高（同传延迟指标） |
| 24 | SimulSpeech | 2020（ACL） | https://aclanthology.org/2020.acl-main.350/ | 端到端同传：单模型直接从语音出译文，避免级联的误差传播 | 仅核验标题与链接，**未读正文**；数值**未核实** | A 低（与本项目级联架构不匹配，且模型不可得） |
| 25 | Netflix English (USA) Timed Text Style Guide | 持续更新 | https://partnerhelp.netflixstudios.com/hc/en-us/articles/217350977-English-USA-Timed-Text-Style-Guide | 字幕硬规范：字符数、行数、阅读速度、断行规则 | 【官方】**42 字符/行**；**最多 2 行**；成人 **≤20 CPS**、儿童 **≤17 CPS**；断行优先在标点后、连词/介词前 | A/B 高（字幕规整的直接判据） |
| 26 | WCAG 2.2 SC 1.4.3 Contrast (Minimum) | 2023–2026 | https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html | 文本与背景对比度下限 | 【官方】普通文本 **≥4.5:1**，大字（≥18 pt 或 14 pt 粗体）**≥3:1** | A 高（浮窗配色） |
| 27 | BBC Subtitle Guidelines | 持续更新 | https://www.bbc.co.uk/accessibility/forproducts/guides/subtitles/ | BBC 字幕规范（阅读速度、断句、可读性） | **本环境访问失败**（旧地址 bbc.github.io 返回 404 并指向新地址，新地址直连报错），具体数值**未核实**，只作规范出处 | A/B 中（待人工核） |
| 28 | Beyond the Sentence: A Survey on Context-Aware MT with LLMs | 2025 | https://arxiv.org/abs/2506.07583 | 综述 LLM 时代的上下文（文档级）翻译：提示、检索、评测 | 仅核验标题与链接，**未读正文**；具体增益**未核实** | B 中（长视频上下文策略） |
| 29 | stable-ts | 2022–2025 | https://github.com/jianfch/stable-ts | Whisper 时间戳稳定化：静音抑制、gap 调整、按标点/停顿重组、断点续跑 | 【论文】README 说明 silence suppression 与 `adjust_gaps()` 的做法与参数；**仓库开发已暂停**（README 明示） | B 高（借算法，不引入依赖） |
| 30 | LiveKit Agents（turn detection / endpointing） | 2025–2026 | https://github.com/livekit/agents | 用模型判断「这句话说完了没有」，替代纯 VAD 静音阈值做端点检测 | 检索显示仓库含 `voice/turn.py` 等实现；**未读代码**，准确率/延迟数值**未核实** | A 中（可显著降句末延迟，但要额外小模型） |
| 31 | COMET: A Neural Framework for MT Evaluation | 2020 | https://arxiv.org/abs/2009.09025 | 学习型 MT 质量评估，与人工判断相关性优于 BLEU | 仅核验标题与链接，**未读正文**；相关系数**未核实** | 评测 高（离线批量打分） |

**表里没写但值得记一笔的现状数据**（本项目自测，非论文）：`tools/_bench_wlk3.py` 头部记录「我的分块路线（faster-whisper medium GPU）：落后中位 **0.92 s**」，是现有 A 模块的对照基线。

---

## 2. 针对本项目的改进建议

> 每条都写清：现状 → 做法 → 预期收益 → 实现成本 → 风险 → 验证方法（可测指标）。
> 「预期收益」里的数字，凡是带来源编号的是引用，凡是没标的都是【推断】，必须实测。

### P0（本周可做，收益确定、改动局部）

#### P0-1 术语表接到 Qwen-MT 的术语干预字段

- **现状**：`local_mt.py` 已经实现 HY-MT 的术语注入（注释里记录了「số phét → 指纹」等实测生效案例），但 `llm_translate.py` 的远端请求体只有 `model / prompt / stream`，**没有带术语参数**；同文件第 65 行注释已经写「qwen-mt 支持 translation_options 的术语定制与领域提示」，说明知道有、但没接。
- **做法**：给远端 MT 加 `translation_options`（术语表 + 领域提示），配置沿用本地 MT 的 `terms` 列表；请求失败（4xx）时自动降级为无术语请求并记日志。
- **预期收益**：【论文 22】官方明确支持术语干预；【论文 4】在级联同传里用 prompt 注入领域术语，配合 AlignAtt 拿到 +2 / +13–22 BLEU（注意那是整句 BLEU，不是术语准确率）。本项目的术语收益量级 **需自测**。
- **实现成本**：0.5–1 人日（改一个请求体 + 配置读取 + 降级分支）。
- **风险**：字段名/结构写错直接 400（项目已经踩过「qwen-mt 不接受 system 角色 → 400」的坑，见 `llm_translate.py` 注释）；术语表过长会挤占 8192 输出预算；模型可能把术语硬套到不该套的句子上。
- **验证方法**：造 50 句术语集（20 个专名/术语，含人名、地名、产品名），指标 = 术语正确率、整句 COMET、P50 延迟；A/B 对照（开/关术语）。

#### P0-2 字幕规整层（CPS / 行数 / 真实结束时间）

- **现状**：`live_subtitle_v2.py:1549` 写 SRT 用 `st` → `st + self.hold`，`hold` 默认 **6.0 s**（`live_subtitle_v2.py:1522`）——即每条字幕固定停 6 秒，**不是语音真实结束时间**；译文与原文各占一行直出，没有 CPS/行数约束。
- **做法**：ASR 已开 `word_timestamps=True`，用词级时间戳算真实语音区间替代固定 `hold`；新增 `subtitle_layout` 模块：按 Netflix 规范做 **42 字符/行、≤2 行、≤20 CPS** 的切分与压缩（压缩只动译文，原文保真），并做最小停留时长下限（避免闪屏）。

  > CJK 注意：42 字符/行 是英文（拉丁字母）口径。中文按全角字计，行业常见 16–20 字/行；Netflix 简体中文指南的具体数字我**未核实**（见 §4 说明）。建议阈值做成配置项，先按「单行不超屏宽」定标【推断】。
- **预期收益**：CPS 合规率、回看/导出 SRT 的可用性；【官方 25】给出的是行业硬规范，不是建议值。
- **实现成本**：2–3 人日 + 单测（切分/压缩的边界用例最费时间）。
- **风险**：压缩改语义；切得太碎导致字幕频繁刷新；双语两行时总字数翻倍，CPS 按"观众实际要读的那一行"算还是两行合计，需要产品定义（我的建议：按译文行算，原文行不计入 CPS）。
- **验证方法**：P90 CPS、CPS 超标率（目标 ≤5%）、行数/字符数违规率（目标 0）、cue 重叠率、人工 30 条抽检（1–5 分可读性）。

#### P0-3 幻觉硬闸门

- **现状**：`streaming_asr.py` 已开 `vad_filter=True`、`temperature=0.0`、`condition_on_previous_text=False`，并留了噪声实验注释（RMS=0.05 噪声下不开 VAD 会输出 "You"，`no_speech_prob=0.771`）。缺的是**输出侧**的后置过滤。
- **做法**：每批 ASR 输出加四道闸：① `no_speech_prob > 0.6` 且 `avg_logprob < -1.0` → 丢弃；② 与本批 confirmed 文本的词级重复率 > 0.8 → 丢弃；③ 同一短语连续重复 ≥3 次 → 截断；④ 连续 N 秒无 VAD 语音 → 不送 ASR（连推理都省）。
- **预期收益**：【论文 19】Whisper 约 1% 转写出现完全不存在的整句，且非人声占比越高越严重——直播场景里静音/音乐/游戏音效占比很高，这条是刚需；项目自己的噪声实验也印证了同类失效模式。
- **实现成本**：0.5 人日（纯后处理，不动模型）。
- **风险**：误杀轻声、耳语、气声（阈值需按素材调）；`no_speech_prob` 在远端 ASR（Qwen3-ASR-Flash）上未必返回，需要按后端分支。
- **验证方法**：纯静音 / 纯音乐 / 游戏音效各 10 min 对抗集，指标 = **每分钟假句数**（目标 0）；正常素材上 WER 相对变化 < 0.5%。

### P1（1–2 周，收益明确但要改结构）

#### P1-1 ASR 与 MT 双缓冲流水线（overlap）

- **现状**：一批 ASR 完成后才发 MT，再上屏；端到端 ≈ ASR + MT + 排队。`OPTIMIZATION_REVIEW_20261018.md` §3 已把 ASR 移出 UI 主线程，但 ASR→MT 仍是串行。
- **做法**：ASR worker 产出 `(batch_id, text, t_end)` 立即入 MT 队列；MT worker 用上一批 confirmed 文本做 context（`llm_translate.py` 已支持 `context`）；显示层按 `batch_id` 顺序提交，允许「先出原文、译文到达后就地替换」。
- **预期收益**：【推断】若 ASR 0.6 s、MT 0.4 s，理论上端到端从 ~1.0 s 降到 ~0.7 s（省掉较小的一侧）；实际受 GPU 争用影响，需实测。
- **实现成本**：2–4 人日（并发、顺序保证、取消/丢弃过期批）。
- **风险**：faster-whisper 与 llama.cpp HY-MT 若同卡，显存和算力互相拖慢，可能越并发越慢；乱序上屏会造成「字幕跳回」。
- **验证方法**：端到端延迟分解（capture → ASR done → MT done → paint）的 P50/P90；GPU 利用率与显存峰值；乱序提交次数（目标 0）。

#### P1-2 模块 B 改用 `BatchedInferencePipeline`

- **现状**：`pretranslate.py` 逐块 `transcribe`（beam 5、word_timestamps、`condition_on_previous_text=True`、`initial_prompt` 接前文），首块 30 s、后续 120 s。
- **做法**：先 VAD 预分段（WhisperX 的 Cut & Merge 思路【论文 11】），再用 `BatchedInferencePipeline.transcribe(batch_size=8)`；跨块上下文改用「每块 `initial_prompt` = 上一块末句」保留。
- **预期收益**：【论文 12】13 min 音频 large-v2 fp16 beam5：1m03s → **17 s**（batch=8，VRAM 6090 MB）；同表 int8 batch8 为 16 s / 4500 MB——**8 GB 卡装得下**。模块 B 是整片处理，批推理的等待成本几乎为零。
- **实现成本**：2–3 人日 + 回归（断点续跑、meta complete 逻辑要跟着改）。
- **风险**：批模式下 `condition_on_previous_text` / `initial_prompt` 行为不同（**需实测确认**，官方 README 未对批模式的上下文语义作承诺）；word_timestamps 在批模式下的边界精度可能变差；块内长静音导致 padding 浪费。
- **验证方法**：同素材 A/B：总耗时、WER、cue 边界与人工标注的 |Δ| P50/P90、条数变化、断点续跑一致性。

#### P1-3 时间轴规整（模块 B 优先，A 模块顺带受益）

- **现状**：`pretranslate.py:546` 的 `_should_stitch` 用「gap ≤ 0.4 s 且上句不以句末标点结尾」缝合，规则简单但没处理「字幕早于语音/拖尾」。
- **做法**：借鉴 stable-ts 的 silence suppression + `adjust_gaps`（【论文 29】，只借算法，仓库已停更不引入依赖）；若要更准，再上 WhisperX 的强制对齐（【论文 11】，需 wav2vec2 对齐模型）。
- **预期收益**：字幕起止贴合语音，减少「话说完了字幕还在」；对 CPS 计算也是前置条件。
- **实现成本**：3–5 人日；若引入对齐模型，另加下载体积与显存预算。
- **风险**：对齐模型对中文/日语的词边界定义与 SRT 需求不一致；过度裁剪造成字幕闪断。
- **验证方法**：边界误差分布（P50/P90）、cue 重叠率、最小停留违规率、与 `watch_player.py` 播放位置的同步误差。

#### P1-4 「草稿 + 终稿重解码」两段式

- **现状**：A 模块只有一遍 ASR；本地 faster-whisper 出什么就显示什么。
- **做法**：草稿继续用本地小模型（首字延迟不变）；在 VAD 判定句子结束（静音 ≥0.4 s）后，把该句音频送远端 **Qwen3-ASR-Flash** 重解一次，替换屏幕文本与 live.srt 的终稿。【论文 15】RealtimeSTT 的 CPU 生产配置就是这个模式（流式模型出草稿 + 强模型在 turn 结束重解）。
- **预期收益**：【推断】终稿 WER 下降（远端模型更强、有完整句子上下文），首字延迟不受影响；顺带缓解长音频漂移。
- **实现成本**：3 人日（句级缓冲、远端调用、替换逻辑、失败降级）。
- **风险**：RPM 100 上限（【官方 21】）在语速快的场景会被打满 → 需要队列 + 指数退避 + 采样率控制（例如只对 >1.5 s 的句子重解）；断网必须静默降级为草稿。
- **验证方法**：终稿相对草稿的 WER 降幅；远端 QPS 峰值与 429 率；断网注入测试的降级行为。

### P2（有余力再做，偏工程健壮性与体验）

| 编号 | 事项 | 现状/依据 | 做法 | 验证 |
|---|---|---|---|---|
| P2-1 | 远端连接复用与限流 | `remote_asr.py:183` 用 `urllib.request.urlopen` 每次调用新建 TLS 连接（无连接池，与 review §8 结论一致）；Qwen3-ASR RPM 100、Qwen-MT RPM 60 / TPM 35k【官方 21/22】 | 连接池 + 令牌桶 + 429 退避，指标化排队深度 | 每分钟建连次数、429 率、排队 P90 |
| P2-2 | 浮窗可读性闸门 | `overlay_qt.py` 已有 `font_size / opacity / bg_alpha / outline`，但注释里自己写了「#B7C4D8 暗淡灰 + bg_alpha=0，在视频上几乎看不见」 | 加对比度计算（对采样背景）+ 最小描边/底衬策略；不透明度设下限；默认配色满足 **4.5:1**（大字 3:1）【官方 26】 | 关键配色对比度 ≥4.5:1；可调范围不越界 |
| P2-3 | 语言锁定与切换 | WLK README 明说 `--language auto` 偏向英语；Qwen3 流式后端要求显式语言【论文 13】 | 默认锁定用户选定语言；只在长静音边界允许重估；切换时清空 ASR 缓冲与 MT context | 切语言测试集上误切次数、切换后首句 WER |
| P2-4 | 半截缓存一致性 | `pretranslate.py` 有断点续跑 + meta complete，但 meta 里没有模型/参数指纹 | meta 加 `model + params + version` 哈希；不一致时拒绝续跑并重建，避免「前 10 块旧模型、后 10 块新模型」拼接 | 篡改 meta 后的续跑行为 |
| P2-5 | 会话级观测 | 现有 bench 脚本各自为政（`bench/*.py`、`tools/_measure_e2e_lag.py`） | 统一输出一条 JSONL span 表（capture/ASR/MT/paint），bench 直接消费 | 与 `_measure_e2e_lag.py` 数值可对齐 |
| P2-6 | 模型矩阵重测 | `OPTIMIZATION_REVIEW_20261018.md` §5 已提「compute_type / 模型矩阵值得重测」 | 在 4070 8 GB 上矩阵化测：`small/medium/large-v3/turbo` × `int8_float16/float16` × `beam 1/5`，A/B 两模块分别出表 | 首字延迟、端到端 P50、VRAM 峰值、WER |

---

## 3. 评测协议建议

> 目标：任何一次改动都能用同一套指标回答「变快了还是变准了，代价是什么」。
> 口径尽量对齐社区：延迟指标参考 IWSLT 的 ASR latency 与 AL/LAAL【论文 4/23】，字幕指标参考 Netflix 规范【官方 25】。

### 3.1 延迟

**四个时间点**（一次会话内打点，单位 ms）：

| 记号 | 含义 | 打点位置建议 |
|---|---|---|
| `t_audio` | 该句第一个语音帧进入缓冲 | VAD 判定为语音的帧时间 |
| `t_asr_done` | 该批 ASR 返回 | ASR worker 出结果 |
| `t_mt_done` | 该批译文返回 | MT worker 出结果 |
| `t_paint` | 字幕真正上屏 | 浮窗 paint 回调 |

**指标**：

- **首字延迟** = `t_paint(第一条 cue) - t_audio(该句首帧)`：这是用户感知最直接的量，必须单列。
- **句末延迟** = `t_paint(终稿) - t_voice_end`：衡量「说完到看到最终字幕」。
- **端到端延迟** = `t_paint - t_audio`，报 **P50 / P90 / P99**（只看均值会被长尾骗）。
- **同传延迟指标**（离线可复算）：Average Lagging / **LAAL**【论文 23】；报「compute-aware」与「compute-unaware」两套（SimulStreaming 的 `--comp_unaware` 就是这个思路【论文 3】），后者用来区分「模型不肯早出」与「机器算得慢」。
- **分解表**：`capture / ASR / MT / paint` 四段各占 P50 百分比，避免「整体变慢但不知哪段」。

### 3.2 稳定性

| 场景 | 做法 | 通过标准（建议） |
|---|---|---|
| 长跑 | 连续 2 h 真实直播/长视频 | RSS 增长 < 10%；GPU 显存无单调上涨；缓冲锯齿不越界（`streaming_asr.py` 里 `buf_max <= max_buffer * 1.4` 的自检可复用） |
| 漂移 | 长跑中每 10 min 抽 1 min 与参考文本对齐 | cue 起点误差随时间的斜率 ≈ 0（A 模块）；B 模块全局偏移 < 100 ms |
| 切语言 | 中→英→日 连续切换 | 误切次数、切换后首句 WER、是否出现「语言粘滞」 |
| 断网 | 拔网 30 s 再恢复 | 不崩、不刷错误弹窗；恢复后自动回到远端；降级期间的 WER 记录 |
| 设备切换 | 蓝牙耳机 → HDMI → 扬声器 | 采样率 44.1/48 kHz 都能跑；无爆音/丢帧；切换后 5 s 内恢复 |
| 极端音频 | 纯静音、纯音乐、爆音、抢话 | 假句数 0；不出现重复刷屏 |

### 3.3 准确性

- **ASR**：WER（英文按词）、CER（中日按字）；分语言、分场景报。
- **MT**：BLEU（可比性差但便宜）+ **COMET**【论文 31】（离线批量，可本地 GPU 或 API）；**术语命中率**（术语表里的词是否按指定译法出现）；**人工抽检**（每次改动抽 30 条，1–5 分，双人打分取均值）。
- **字幕合规**：CPS 分布（P50/P90/超标率）、每行字符数、行数、最小/最大停留时长、cue 重叠率——判据直接用 Netflix 规范【官方 25】。
- **幻觉**：静音/音乐段每分钟假句数；整句幻觉占比（对齐【论文 19】的口径：与音频完全对不上的整句）。
- **一致性**：同一段素材跑 3 次，WER/延迟的方差（流式系统最容易在这里翻车）。

### 3.4 测试集构造

分 6 层，每层 10–20 min，总 3–5 h，全部带**人工转写 + 词级时间戳 + 术语表**：

1. 新闻播报（干净、语速快、专名多）——考术语与 CPS。
2. 会议/访谈（多人、抢话、停顿多）——考 endpointing 与说话人切换。
3. 游戏实况（BGM + 音效 + 突然大声）——考 VAD 与幻觉。
4. 影视剧（背景音乐、语气词、俚语）——考翻译可读性。
5. 电话/低码率音质——考鲁棒性。
6. 口音/中英混说——考语言判定与代码切换。

**对抗集**（各 10 min，单独报）：纯静音、纯音乐、白噪声、单频啸叫、突然爆音。

**版本化**：素材 + 标注 + 术语表放一个 `manifest.json`（含 sha256），报告里写 manifest 版本，保证「同一个 5 分钟」可比。

### 3.5 报告格式与复现

一次改动产出一张表 + 一条命令：

```
素材版本 | 首字延迟 P50/P90 | 端到端 P50/P90 | 句末 P50 | WER | COMET | 术语命中 | CPS 超标率 | 假句/min | VRAM 峰值
```

复现入口建议统一到现有工具：`bench/bench_latency.py`（延迟）、`bench/bench_localagree.py`（确认策略）、`tools/_measure_e2e_lag.py`（端到端）、`tools/quality_report.py`（质量）。新增指标（CPS/行数/假句）建议做成 `tools/quality_report.py` 的子命令，而不是再写一个脚本。

---

## 4. 参考链接清单

> 编号与正文一致。标注「访问失败」的条目本环境未能取到正文，只作规范出处引用，数值不采信。

1. Macháček, Dabre, Bojar. *Turning Whisper into Real-Time Transcription System*. IJCNLP-AACL 2023 demo. https://arxiv.org/abs/2307.14743 ・ https://aclanthology.org/2023.ijcnlp-demo.3/
2. ufal/whisper_streaming（代码）. https://github.com/ufal/whisper_streaming
3. ufal/SimulStreaming（代码 + README）. https://github.com/ufal/SimulStreaming
4. Macháček & Polák. *Simultaneous Translation with Offline Speech and LLM Models in CUNI Submission to IWSLT 2025*. https://arxiv.org/abs/2506.17077 ・ https://aclanthology.org/2025.iwslt-1.41/
5. Papi, Turchi, Negri. *AlignAtt*. Interspeech 2023. https://arxiv.org/abs/2305.11408
6. Wang et al. *Simul-Whisper: Attention-Guided Streaming Whisper with Truncation Detection*. Interspeech 2024. https://arxiv.org/abs/2406.10052
7. Fuxa & Macháček. *AlignAtt4LLM*. IWSLT 2026. https://aclanthology.org/2026.iwslt-1.32/
8. Sharipov Ortega & Macháček. *A Pocket Offline Model for Simultaneous Speech Translation*. IWSLT 2026. https://aclanthology.org/2026.iwslt-1.22/
9. Jeffries et al. *Moonshine*. 2024. https://arxiv.org/abs/2410.15608
10. Seamless Communication et al. *Seamless: Multilingual Expressive and Streaming Speech Translation*. 2023. https://arxiv.org/abs/2312.05187
11. Bain et al. *WhisperX: Time-Accurate Speech Transcription of Long-Form Audio*. Interspeech 2023. https://arxiv.org/abs/2303.00747
12. SYSTRAN/faster-whisper（README 基准表 + BatchedInferencePipeline）. https://github.com/SYSTRAN/faster-whisper
13. QuentinFuxa/WhisperLiveKit（README 参数与后端）. https://github.com/QuentinFuxa/WhisperLiveKit
14. collabora/WhisperLive（README：hotwords / batch inference）. https://github.com/collabora/WhisperLive
15. KoljaB/RealtimeSTT（README：两段式引擎配置）. https://github.com/KoljaB/RealtimeSTT
16. ggml-org/whisper.cpp（`examples/stream` 目录已核验存在）. https://github.com/ggml-org/whisper.cpp/tree/master/examples/stream
17. thewh1teagle/vibe（README：稳定时间戳模式、字幕长度预设）. https://github.com/thewh1teagle/vibe
18. snakers4/silero-vad（README：<1 ms / 30 ms 块，2 MB 模型，MIT）. https://github.com/snakers4/silero-vad
19. Koenecke et al. *Careless Whisper: Speech-to-Text Hallucination Harms*. FAccT 2024. https://arxiv.org/abs/2402.08021
20. openai/whisper（README 模型表：turbo 809M / ~6GB / ~8×；turbo 未训练翻译）. https://github.com/openai/whisper
21. 阿里云 Model Studio：*qwen3-asr-flash* 模型文档（计价、RPM 100、能力矩阵）. https://www.alibabacloud.com/help/en/model-studio/qwen3-asr-flash
22. 阿里云 Model Studio：*qwen-mt-flash* 模型文档（术语干预/格式保留/领域适配、上下文 16384、RPM 60）. https://www.alibabacloud.com/help/en/model-studio/qwen-mt-flash
23. Papi, Gaido, Negri, Turchi. *Over-Generation Cannot Be Rewarded: Length-Adaptive Average Lagging for Simultaneous Speech Translation*. AutoSimTrans 2022. https://aclanthology.org/2022.autosimtrans-1.2/ ・ https://arxiv.org/abs/2206.05807
24. *SimulSpeech: End-to-End Simultaneous Speech to Text Translation*. ACL 2020. https://aclanthology.org/2020.acl-main.350/
25. Netflix Partner Help Center. *English (USA) Timed Text Style Guide*. https://partnerhelp.netflixstudios.com/hc/en-us/articles/217350977-English-USA-Timed-Text-Style-Guide
26. W3C WAI. *Understanding SC 1.4.3: Contrast (Minimum)*（WCAG 2.2）. https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html
27. BBC. *Subtitle Guidelines*（**本环境访问失败**，仅作规范出处）. https://www.bbc.co.uk/accessibility/forproducts/guides/subtitles/
28. *Beyond the Sentence: A Survey on Context-Aware Machine Translation with Large Language Models*. 2025. https://arxiv.org/abs/2506.07583
29. jianfch/stable-ts（README：silence suppression / adjust_gaps；仓库已暂停维护）. https://github.com/jianfch/stable-ts
30. livekit/agents（turn detection / endpointing 实现）. https://github.com/livekit/agents
31. Rei et al. *COMET: A Neural Framework for MT Evaluation*. 2020. https://arxiv.org/abs/2009.09025

**核验记录（2026-10-18）**，分三档，别混：

- **已逐页抓取核验**（原文/README/官方文档）：1、3、4、5、6、7、8、9、10、11、12、13、14、15、17、18、19、20、21、22、23、25、26、29。
- **只核验了存在性/目录结构**：16（GitHub API 列目录确认 `examples/stream`、`parakeet-cli` 存在）、2（同仓库族，未单独抓页）。
- **未逐页核验，仅凭检索结果存在**：24、28、30、31（标题与链接可信，数值一律未引用）。
- **访问失败**：27（BBC 指南：`bbc.github.io` 返回 404「已迁移」，新地址直连报错）。
- **域名整体不可达**：`huggingface.co`（本次多次抓取失败）。因此 Parakeet / Canary 的模型卡数值**一律未引用**，正文也没有出现它们的 WER/RTFx 数字。

项目内部证据来自这些文件（**只读，未做任何修改**）：`streaming_asr.py`、`llm_translate.py`、`local_mt.py`、`pretranslate.py`、`live_subtitle_v2.py`、`overlay_qt.py`、`tools/_bench_wlk3.py`、`OPTIMIZATION_REVIEW_20261018.md`。
