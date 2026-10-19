# live-subtitle 全网调研 v2：识别能力 / 稳定性 / 断句（2026-10-18 深夜）

**针对的三个痛点**（用户原话）：视频翻译模式的 **稳定性**、**识别能力**、**字幕断句**。
**与上一份调研的关系**：`RESEARCH_20261018_streaming_translation.md` 覆盖了流式架构主线
（SimulStreaming/WhisperLiveKit/AlignAtt4LLM），结论仍然是"骨架不动"。本轮换角度：
找 2025–2026 年新出的、能直接替换/补强现有部件的模型与算法。
**证据口径**沿用上一份：【论文】= 原文/官方 README 明确数值；【第三方实测】= 独立评测；【推断】= 我基于本项目代码的判断，需 POC。

---

## 0. TL;DR

**最大的一个发现：Qwen3-ASR 开源了（2026-01，Apache 2.0）——它就是 qwen3-asr-flash 的开源权重版。**

1. **识别能力**：Qwen3-ASR-0.6B/1.7B 支持 52 语言 + 22 种中文方言，**含越南语**；
   第三方独立实测越南语错误率 4.91% vs Whisper-large-v3-turbo 8.10%【第三方实测 2】；
   中文会议场景 6.88% vs Whisper-large-v3 的 19.11%（0.6B 就达到）【论文 1】；
   极端噪声下 16.17% vs Whisper 63.17%【论文 1】。1.7B bf16 约 3.4GB，4070 8GB 放得下。
   **这是本项目主力场景（越南语/中文视频）的直接升级件。**
2. **断句**：CHI 2025 的 **OptiSub** 给出了一个可照抄的全局切分算法（停顿加权 ×5 标点奖励、
   <0.2s 块剔除、显示时长规则）【论文 6】；中文生态里 15.8k star 的 **VideoCaptioner**
   验证了"**LLM 断句+校正，只发文本、模糊匹配回时间轴**"这条路【项目 7】——
   点播预翻译没有实时压力，正好能用上。
3. **稳定性**：**TEN-VAD**（2025-06 开源，306KB）边界检测精度超过 Silero，
   能抓到 Silero 漏掉的段间短暂停顿【官方 5】；视频带 BGM 时先做**人声分离**（Demucs 类）
   再识别是多个项目验证过的减幻觉手段【项目 8】【论文 9】。

---

## 1. 识别能力

### 1.1 Qwen3-ASR（本轮最推荐，P0 POC）

- **是什么**：阿里 2026-01 开源的 ASR 系列，基于 Qwen3-Omni。两个规格：
  **0.6B**（精度-效率平衡，TTFT 低至 92ms，128 并发下 2000× 实时吞吐）和
  **1.7B**（开源 ASR SOTA，与最强商用 API 相当）。配套 **Qwen3-ForcedAligner-0.6B**
  （非自回归强制对齐，11 语言，官方称超过三个最强 E2E 对齐模型）【官方 1】。
- **语言覆盖**：30 语言 + 22 中文方言，**明确含越南语、泰语、印尼语、日语、韩语、俄语**。
  语言识别准确率平均 97.9%（Whisper-large-v3 为 94.1%）【论文 1】。
- **关键数值**（WER/CER，越低越好）：

  | 场景 | Whisper-large-v3 | Qwen3-ASR-0.6B | Qwen3-ASR-1.7B |
  |---|---|---|---|
  | LibriSpeech（英文朗读） | 2.78 | — | **1.63** |
  | WenetSpeech 会议（中文） | 19.11 | **6.88** | — |
  | 内部多语新闻（15 语种） | 14.80 | 17.39 | **12.80** |
  | Fleurs（12 语种） | 5.27 | 7.57 | **4.90** |
  | 极端噪声 | 63.17 | — | **16.17** |
  | 带 BGM 整歌（中文） | — | — | **13.91**（豆包 ASR 23.99） |

  来源：Qwen3-ASR Technical Report (arXiv 2601.21337)【论文 1】、官方模型卡【官方 1】。
- **第三方独立核验**（whispernotes.app，M5 Mac，FLEURS 每语约 150 句，对照 turbo）【第三方实测 2】：
  **越南语 4.91 vs 8.10**、泰语 6.53 vs 13.40、粤语 6.44 vs 36.75、中文 6.49 vs 7.97、
  英语 4.49 vs 5.49——亚洲字符计分语言上 Qwen3-ASR-1.7B 平均 5.7% vs turbo 13.5%。
  弱点：部分欧洲语言（芬兰/匈牙利/希腊/捷克等）明显输给 Whisper；俄语基本持平（5.65 vs 5.13）。
  → **本项目主力是 vi/zh/ja/ko，全在 Qwen3-ASR 的强势区。**
- **部署**：`pip install -U qwen-asr`（transformers 后端即可离线转写）；
  `qwen-asr[vllm]` 才支持官方流式；单次最长 20 分钟音频；支持唱歌/带 BGM 歌曲。
  0.6B bf16 约 1.2GB、1.7B 约 3.4GB，4070 8GB 宽裕。
- **对本项目的接线点**【推断】：
  - **视频翻译（pretranslate.py）最直接**：离线分块转写接口和 faster-whisper 形态接近，
    加一个 `Qwen3ASRBackend` 注册进 `model_registry`，cache 模式可换成 1.7B 满血质量。
  - **直播翻译（live_subtitle_v2.py）**：可以按现有"攒批→整批转写"调用离线模式
    （batch 0.5–4s 音频对 0.6B 是小菜）；真流式要 vLLM，重，先不做。
  - **注意两个坑**：① torch 技术栈，本机已踩过 Qt→torch DLL 顺序问题（native_preload.py
    已解决，可复用）；② LLM 系 ASR **不暴露 no_speech_prob/avg_logprob/compression_ratio**，
    现有段级质量闸对它失效，需要退回文本侧过滤（`is_hallucination` 复读检测）+ VAD 前置。
- **验证方法**：`bench/eval_asr.py` 加 backend 参数，跑现有 jfk.wav + 越南语新闻样本 +
  一段带 BGM 的视频音频，对比 WER 与 cue CPS；幻觉率用一段纯音乐/静音段统计误产出条数。

### 1.2 FunASR 全家桶（中文/粤语内容的备选强化）

- **SenseVoice-Small**：中/粤/英/日/韩五语，10 秒音频 70ms（Whisper 约 470ms，快 6.7×），
  内置 fsmn-VAD + 标点恢复 + 情感/事件检测；会议实测（12 小时，中英日韩粤混合）：
  WER 6.2% vs Whisper-Large 8.7%，RTF 0.12 vs 0.8【第三方实测 3】。
- **Fun-ASR-MLT-Nano**：独立多语 checkpoint，31 语言（含 vi/th）【官方 4】；
  **Fun-ASR-Nano**：中英日 + 7 方言 26 口音，vLLM 高吞吐。
- **独特优势**：内置 cam++ 说话人分离（不用 pyannote 的 gated 模型）——
  访谈/对话类视频能顺带标说话人。
- **适用性**：【推断】中文内容优先级高于 Qwen3-ASR 之外的任何选项；越南语要走 MLT-Nano。
  Windows 可经 funasr-onnx / sherpa-onnx 跑 SenseVoice。可作为注册表里"中文优化档"。

### 1.3 sherpa-onnx 流式模型（直播模块的第三条腿）

- Zipformer-Transducer / Paraformer 流式模型，中英双语 ~100MB，CPU 上即可实时；
  2025-07 已集成 TEN-VAD【官方 5】。
- 与本项目现有 LocalAgreement 骨架不同：**真流式、逐 60ms 出部分结果、自带端点检测**。
  【推断】适合给直播翻译做"快字草稿"层（先出原文草稿稳住体验，定稿后翻），
  或作为 CPU 回退路径的升级（现在 CPU 回退固定 2s/批，是体验最差的档）。
- 风险：多语覆盖不如 Whisper/Qwen3-ASR（zh/en/ja/ko/yue 有，vi 没有）；ONNX 运行时集成成本中等。

### 1.4 降噪前置与幻觉抑制（不换模型的提升）

- **人声分离前置**：WhisperSub / SubsAI 的实践结论——嘈杂/带 BGM 素材先过 Demucs
  再识别，准确度明显提升但耗时增加，做成可选档【项目 8】。
  【推断】本项目"背景音乐与重叠说话挡不住"是 README 自认的已知限制，这条正中靶心；
  点播管线无实时压力，`--vocal-iso` 可选开关很合适。
- **Calm-Whisper（2025-05）**：>75% 的非语音幻觉可归因到 3 个 decoder 自注意力头，
  只微调这几个头幻觉率降 84.5%（UrbanSound8K 99.97%→15.51%）、LibriSpeech WER 退化 <0.1%
  【论文 9】。不微调的可操作结论：幻觉是**少数头的系统性行为**，
  SNR 门限 + 音频流检查就能把实时场景 WER 从 14.9 压到 5.1（IWSDS 2025 智能音箱论文）【论文 10】。
- **faster-whisper 现成旋钮**：`hallucination_silence_threshold`、`no_speech_threshold`、
  VAD `onset/offset` 参数，本项目还没系统化 A/B 过。
- **large-v3 vs large-v2**：VideoCaptioner 的社区反馈是 **large-v3 更易幻觉/复读，large-v2 更稳**
  【项目 7】。【推断】值得用现有 eval 基线把 large-v2 加进矩阵（缓存里没有，需下载）。

---

## 2. 断句

### 2.1 OptiSub（CHI 2025）：停顿驱动的全局切分，算法可直接照抄【论文 6】

现状：批推理路径按"词间静音 gap ≥0.75s"贪心切，再到 `subtitle_layout` 按字数切短。
OptiSub 的做法是**全局最优**而不是贪心：

1. 枚举所有候选切分方案，先做两道过滤：空间可读性（宽度超屏丢弃）、
   时间可读性（**显示 <0.2s 的块整个方案丢弃**）；
2. 损失函数 `Loss = (T-1) / sqrt(Σ 段间停顿时长)`——块数越少越好、
   切点落在越长的停顿上越好；**切点在标点后的停顿时长 ×5**（强烈偏向逻辑断点）；
3. 显示时长：从块内第一个词的开播时刻上屏，到最后一个词结束后最多再留 1s
   （与下一块重叠则提前切）。

- 论文在中/韩这种结构与英文不同的语言上也验证有效；用了词级时间戳（他们走
  MMS-1B-all + DTW，本项目 faster-whisper 的 word_timestamps 可直接替代）。
- **接线点**【推断】：`pretranslate.build_cues` / 批推后的 gap 重切换成这个优化器；
  词级时间戳已有，主要工作量是候选枚举（句内切点很少，组合数可控）。
  验证：现有 120s/386s 样本对比 cue 数、max cue 时长、CPS 分布、人工抽检可读性。

### 2.2 LLM 断句 + 校正（VideoCaptioner 验证过的中文生态方案）【项目 7】

VideoCaptioner（15.8k star）的成熟做法，三个可借鉴点：

1. **只把纯文本发给 LLM 断句/校正，不带时间轴**——处理开销小、prompt 简单；
2. **序列模糊匹配把断好的句子对回词级时间轴**——保证时间轴完全一致（它们明确用了
   "序列模糊匹配算法保证时间轴完全一致"）；
3. **文稿提示**：允许用户提供原稿/术语表辅助校正（与项目已接的 Qwen-MT 术语字段互补）。

- 【推断】这条对**视频翻译（离线）**是质量天花板最高的方案：识别完先让 qwen-mt / 通用 LLM
  做一遍"修正错别字 + 按语义重断句"，再对回时间戳，最后走现有 `subtitle_layout` 限长。
  直播路径不适用（实时性），但点播可以加 `--llm-refine` 可选档。
  成本极低（qwen-mt-flash ≈0.12 元/小时级），现有 LLM 连接复用/合并调用基建都能复用。
- 风险：LLM 改写跑偏（漏句/合并过头）——必须保留"模糊匹配失败则回退原 cue"的保险。

### 2.3 现状方向被第三方验证（不用推翻重写）

- **VMEG 生产实践**（2026）：在真实字幕上对比规则切分 vs SaT（wtpsplit，85 语言神经分句），
  结论"打平"，最终上线的仍是规则——因为**断句一半是语言问题、一半是几何问题**，
  模型只解决前者，"行放不放得下屏幕"才是交付缺陷的大头【工程实践 11】。
  → 本项目"Netflix 规则 + jieba + 限宽"的方向是对的，不建议为断句单独引入 SaT 模型。
- **眼动实验**（Gerber-Morón & Szarkowska）：观众显著偏好按句法单位断行的字幕【论文 12】
  ——继续支撑"连词/介词前断"规则。
- **stable-ts 的 regroup**：静音抑制 + 按标点/停顿重组的启发式久经考验，其 `align()` 的
  "词边界严格相接"策略可作为时间轴兜底参考【项目 13】（注意仓库已暂停开发，只借算法）。

---

## 3. 稳定性（识别链路的输入侧）

### 3.1 TEN-VAD 替换/补强 Silero【官方 5】

- 2025-06 开源（ONNX，306KB），PR 曲线全面优于 Silero 与 WebRTC VAD；
  **语音→非语音切换检测比 Silero 快数百毫秒**，且能抓到 Silero 漏掉的**段间短暂停顿**；
  RTF 0.015（Ryzen），`pip install ten-vad`。
- 对本项目的意义【推断】：① 切句边界更准 → cue 起止更贴语音，"字幕比语音早/晚半拍"
  的观感问题直接受益；② 段间短停顿被抓到 → 断句切点变多且更自然；
  ③ 直播侧 flush-on-silence 的触发更灵敏。
- 接线点：`pretranslate` 的 VAD（现在走 faster-whisper 内置 Silero）与直播 `Segmenter`
  都可加 `--vad ten` 对比档；验证用现有 `bench/_bench_dsp_gate.py` 的思路加边界偏移统计。

### 3.2 带 BGM 视频的组合拳（稳定性 + 识别同时受益）

按投入产出排序【推断】：
1. **TEN-VAD**（边界准）→ 2. **Qwen3-ASR**（官方明确针对"带 BGM 歌曲"训练，
   中文整歌 13.91% vs 豆包 23.99%【论文 1】）→ 3. **人声分离前置**（Demucs 类，可选档）。
   三层叠加基本覆盖 README 里"背景音乐挡不住"的已知限制。

### 3.3 建议补的两条工程稳定性

- **识别后端自动降级链**：Qwen3-ASR 加载失败 → faster-whisper → 远端 qwen3-asr-flash，
  与现有 device_placement/release_failed_load 同构，注册表加 `fallback` 字段即可【推断】。
- **时间轴健康检查**：pretranslate 落盘前断言 cue 单调、无重叠、end≤时长（现有 fit_cues_cps
  已是幂等整形，再加一道硬断言把"坏 SRT 写出去"变成不可能）【推断】。

---

## 3.4 追加：第二轮检索发现的更多开源模型（2026-10-18 深夜补充）

按与本项目的相关度排序：

| 模型 | 发布 | 规格 | 语言 | 许可 | 对本项目的价值 |
|---|---|---|---|---|---|
| **Dolphin**（清华+海天瑞声） | 2025-03 | base 140M / small 372M（开源）+ medium/large（未开源） | **40 个东方语言含越南语** + 22 中文方言 | Apache 2.0 | **高**：small 版官方称平均 WER 比 Whisper-large-v3 低 54.1%（其自有测试集）；还自带 VAD/分段/LID 多任务。值得和 Qwen3-ASR 一起进 POC 对照【论文：arXiv 2503.20212】 |
| **GLM-ASR-Nano-2512**（智谱） | 2025-12 | 1.5B，BF16 ~3GB | 17 语言（中/粤/英/日/韩等，**无越南语**） | MIT | 中高：中文/方言/耳语场景 SOTA，Open ASR Leaderboard 7.03 优于 Whisper-large-v3 7.4；transformers≥5.18 原生支持。中文内容备选【官方模型卡】 |
| **Omnilingual ASR**（Meta FAIR） | 2025-11 | 300M–7B 家族 + wav2vec2 编码器 | **1600+ 语言**，零样本可扩展 | Apache 2.0 | 中：本项目主力语种它不构成优势，但"给几句样本就识别新语言"的零样本能力是独门绝技；7B-LLM-ASR 在 1600 语言上 SOTA。冷门语种视频的兜底选项【官方】 |
| **Hy-MT2**（腾讯混元） | 2026-05 | 1.8B / 7B / 30B-A3B（MoE） | 33 语言 + 5 方言 | Apache 2.0 | **高（翻译侧）**：本项目本地 MT 目前是 HY-MT1.5-1.8B；Hy-MT2 明确针对**字幕本地化、术语约束、结构化输出**专项优化，官方称 7B 达 Gemini 2.5 Pro 的 96.9%。1.8B-GGUF 440MB 可 CPU 跑。本地 MT 的直接升级件【官方】 |
| **TranslateGemma**（Google） | 2026-01 | 4B / 12B / 27B | 55 语言（WMT24++） | Gemma ToU（非 OSI） | 中：第三方小模型横评里 4B 登顶（LINUX DO 实测综合 90 分）；但许可不是真正开源，且本项目已有 qwen-mt/HY-MT 双保险，优先级低【第三方实测】 |
| **Fun-ASR-Nano / MLT-Nano**（阿里 FunASR） | 2025 | Nano 级 | Nano 中英日+方言；**MLT-Nano 31 语言** | MIT | 中高：见 §1.2；MLT-Nano 是除 Qwen3-ASR 外另一个含越南语的小模型 |
| **Whistle**（Cactus） | 2026-10 | 16.9MB 单文件 | 英德法西意荷波 7 语 | Apache 2.0 | 低：无中文/越南语；但"静音直接返回空串、不进解码器"的防幻觉设计值得借鉴思路 |
| **TranscribePsy**（Tether QVAC） | 2026-10 | 254M | 25 欧洲语言 | Apache 2.0 | 低：欧洲语言端侧场景，与本项目主力语种不重合 |
| **Kyutai STT**（DSM 架构） | 2025-06 | 1B（0.5s 延迟）/ 2.6B（2.5s） | 仅英/法 | CC-BY 4.0 | 低（语言），但 **语义 VAD**（判断"说完了没有"而非"有没有声音"）的设计值得参考——直播侧句末判定的升级方向 |
| **Streaming Sortformer / Nemotron-3-Diarization**（NVIDIA） | 2025-08 / 2026-09 | 117M | 语言无关（声学） | 开放权重 | 中：流式说话人分离（4 人→8 人），直播多人对话场景可标"谁在说话"；离线首选仍是 pyannote Community-1（4.0，2026-06）【官方/综述】 |
| **FireRedChat pVAD**（小红书） | 2025-09 | — | — | 开源 | 低：个性化 VAD（只听"主人"），对话助手场景，与本项目不符 |

**新结论**：识别侧的格局已经从"Whisper 一家"变成"**东亚语言选 Qwen3-ASR/Dolphin/GLM-ASR，欧洲语言选 Parakeet，边缘端选 Moonshine/Whistle**"。
本项目主力是 vi/zh/ja/ko，候选集中在 Qwen3-ASR（已 POC）、Dolphin、Fun-ASR-MLT-Nano 三家。
翻译侧 Hy-MT2-1.8B 是本地 MT 的免费升级（现为 HY-MT1.5）。

## 3.5 POC 实测：Qwen3-ASR-0.6B vs faster-whisper-medium（2026-10-19，本机 CPU）

**环境**：Python 3.12 + qwen-asr（pip）+ 模型经 ModelScope 下载至 `models/Qwen3-ASR-0.6B`（1.9GB）；
CPU/fp32（本机 GPU 链路未修）。脚本：`bench/eval_qwen3asr.py`、`bench/_gen_tts_samples.py`、`bench/_poc_real_vi.py`；
结果 JSON：`work/asr_eval_qwen3.json`。

### 干净朗读样本（英/中/越 TTS + JFK）

| 样本 | whisper-medium (int8) | Qwen3-ASR-0.6B (fp32) |
|---|---|---|
| loopback_en（13.8s） | WER 0.000，5.8s | WER 0.000，7.5s |
| jfk_en（11s） | WER 0.000，4.4s | WER 0.000，4.4s |
| tts_zh（中文新闻腔） | **CER 0.417**，9.2s | **CER 0.000**，8.9s |
| tts_vi（越南语新闻腔） | WER 0.017，10.5s | **WER 0.000**，10.2s |

- **最炸裂的一条**：whisper-medium 把简体中文播报转写成了**繁体**（"各位觀眾晚上好，歡迎收看…"），
  CER 0.417 全是简繁差异。这意味着现有中文链路一直在把繁体原文喂给翻译模型——
  Qwen3-ASR 直接输出规范简体且一字不差。对 zh 素材这是免费的质量修复。
- 速度：CPU 上两者基本持平（0.6B fp32 ≈ medium int8）；**模型加载 2.0s vs 7.5s**（首次）。

### 真实素材（BV1mQ8EzsEpR，84s 越南语吵架场景，多人重叠口语）

| | whisper-medium | Qwen3-ASR-0.6B |
|---|---|---|
| 耗时 | 55.8s | 58.4s |
| 4-gram 复读率 | **1.49%** | **7.26%** |
| 输出形态 | 36 段、带标点、正常大小写 | 整段一次出、全小写、无标点 |

- 这段是极端对抗样本（吵架、重叠说话、口语碎句），两边文本质量都差，人工无法判优劣；
  但 **Qwen3-ASR 的 n-gram 复读率明显更高**——印证了 §1.1 的判断：LLM 系 ASR 没有
  nsp/logp/cr 信号，**必须配文本侧复读过滤（现有 `is_hallucination`）+ VAD 前置切句**才能上线。
- 另注意 Qwen3-ASR 对越南语输出**无标点、全小写**（干净 TTS 样本上有标点有大小写，
  难样本上退化），下游断句不能依赖它的标点。

### POC 结论

**接入价值确认，但有条件**：
1. 干净/新闻腔场景（点播主力素材类型）全面优于 medium，中文简繁问题是决定性优势；
2. 上线形态 = **VAD 切句 → Qwen3-ASR 逐段转写 → 文本侧复读闸**；
   不做 VAD 前置直接把长音频整块喂给它，难样本复读风险高；
3. 断句链路不能依赖它的标点输出，切句继续走"词时间戳 + 停顿"（§2.1 OptiSub 路线）；
4. GPU 链路修好后换 1.7B + fp16，质量和速度还有一倍以上的空间（官方吞吐数据）。

### 3.6 接入后实测发现：双语落盘时的比例切分错位（P1 的直接证据）

端到端跑 18s 越南语样本（qwen3-asr-0.6b → 本地 HY-MT）得到 **5 条识别段、9 条最终字幕**。
第 4/5 条把「过去一周」和「trong tuần qua」切进了两条：

    过去一周，            | 大米出口价格继续上涨
    giá gạo … tăng cao trong | tuần qua

原因是 `write_srt()` 落盘时调 `subtitle_layout.split_cue(zh, src)`，**按视觉长度比例**
把中/越两侧各切成两段。越南语「trong tuần qua」在句尾、中文「过去一周」在句首，
语序不同 → 比例切分必然把介词短语劈开，双语对不上。

这条**与识别模型无关**（whisper 也会发生，只是它的标点让句子边界更早确定），
但它说明"断句"痛点的根因不在模型，而在**切分顺序**：应当是
「整句翻译 → 按译文/停顿切分」，而不是"按源文比例切分再各自翻译"。
与 §2.1 OptiSub 的全局切分是同一件事 → 升为 P1 首项。

## 4. 建议执行顺序

| 优先级 | 事项 | 预期收益 | 工作量 | 风险 |
|---|---|---|---|---|
| ~~P0~~ ✅ | Qwen3-ASR-0.6B POC（已完成，见 §3.5）：干净样本全胜，中文简繁修复；上线需配 VAD 前置 + 文本复读闸 | 已实测 | — | — |
| ~~P0~~ ✅ | Qwen3-ASR 正式接入（2026-10-19 完成）：`qwen_asr_backend.py` 包装成 whisper 同款 `transcribe()`，VAD 切句 + 复读闸内置；`cache.asr` 默认已切到 `local:qwen3-asr-0.6b` | 视频翻译识别默认换代 | — | 已落地 |
| **P0** | TEN-VAD 对比实验（点播 + 直播 Segmenter） | 切句边界/短停顿 | 0.5 天 | 低 |
| **P1** | OptiSub 式全局切分替换贪心 gap 切 | 断句贴停顿、少碎条 | 1 天 | 低 |
| **P1** | 点播 `--llm-refine`（LLM 校正+重断句+模糊匹配回时间轴） | 识别错字与断句同时修 | 1–2 天 | 中（改写跑偏，需回退保险） |
| **P2** | 人声分离可选档 `--vocal-iso` | BGM 场景 | 1 天 | 低（耗时换质量，可选） |
| **P2** | sherpa-onnx 流式草稿层 / CPU 回退升级 | 直播 CPU 档体验 | 2–3 天 | 中 |
| **P2** | large-v2 进 eval 矩阵、幻觉旋钮 A/B | 不换栈的稳定性 | 0.5 天 | 低 |
| 观察 | Qwen3-ForcedAligner（无 vi）、Fun-ASR-MLT-Nano、Voxtral Realtime | 待 POC 后定 | — | — |

**第一步建议**（更新：POC 已完成）：下一步是正式接入——注册表加 Qwen3-ASR 后端，
按"VAD 切句 → 逐段转写 → 文本复读闸"形态进 pretranslate，同步跑 TEN-VAD 对比。

---

## 5. 来源清单

| # | 来源 | 类型 |
|---|---|---|
| 1 | Qwen3-ASR Technical Report, arXiv 2601.21337；Qwen 官方博客 qwen.ai/blog?id=qwen3asr；HF 模型卡 Qwen/Qwen3-ASR-0.6B | 【论文】【官方】 |
| 2 | whispernotes.app "Qwen3-ASR 1.7B vs Whisper Large V3 Turbo: Which Languages Win"（FLEURS 独立实测） | 【第三方实测】 |
| 3 | CSDN 会议场景多说话人评估（SenseVoice vs Whisper-Large，12h 混合语料） | 【第三方实测】 |
| 4 | FunASR 官方对比页 funasr.com/vs-whisper.html（Fun-ASR-Nano / MLT-Nano / SenseVoice 矩阵） | 【官方】 |
| 5 | TEN-framework/ten-vad GitHub README 与官方文档（PR 曲线、RTF 表、sherpa-onnx 集成公告） | 【官方】 |
| 6 | OptiSub, CHI 2025, DOI 10.1145/3706598.3714199（KAIST，停顿驱动字幕分块 + 损失函数） | 【论文】 |
| 7 | VideoCaptioner（卡卡字幕助手）GitHub README（LLM 断句/校正/文稿匹配/模糊匹配对齐，15.8k star） | 【项目】 |
| 8 | WhisperSub 构建笔记 aleix.dev/blog/2025/whispersub（stable-ts 选型、Demucs 前置权衡） | 【项目】 |
| 9 | Calm-Whisper（2025-05，3 个注意力头致 >75% 非语音幻觉，微调降 84.5%），见 EmergentMind Whisper 综述页 | 【论文】 |
| 10 | Carrick et al., IWSDS 2025（SNR 门限 + 流检查，实时 WER 14.9→5.1） | 【论文】 |
| 11 | VMEG "Where to Break a Subtitle Is Geometry, Not Language"（规则 vs SaT 生产对比） | 【工程实践】 |
| 12 | Gerber-Morón & Szarkowska, J. Eye Mov. Res. 11(3)（观众偏好句法断行，眼动实验） | 【论文】 |
| 13 | stable-ts align()/regroup 机制解析（GitCode 技术解析 + 仓库 README） | 【项目】 |
| 14 | sherpa-onnx 在线模型清单（k2-fsa 官方文档，Zipformer/Paraformer 流式模型） | 【官方】 |
