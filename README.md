# live-subtitle　外文视频实时翻译字幕

看外文视频，**既没字幕也没翻译**时，给你实时中文字幕。

置顶浮窗显示 **中文一行、原文一行**（各自居中），同步写 `.srt` 供播放器加载。

> **实测字幕延迟：中位 0.98 秒，最差 1.08 秒。**

---

## 快速开始

```
双击  start.bat        普通模式（分块识别，字幕与音频同步）
双击  panel.bat        同上 + 打开控制面板
双击  stream.bat       流式模式（首字更快，长时间连续说话会积压）
```

启动后**去播放你的视频**，字幕自动出现。没有声音时浮窗显示一行
`● 正在听……`，用来区分"程序在跑但没声音"和"程序没启动"。

```bat
python live_subtitle_v2.py --to zh                 # 目标语言（默认 zh）
python live_subtitle_v2.py --lang vi               # 固定源语言（默认自动检测）
python live_subtitle_v2.py --no-remote-asr         # 强制本地 whisper
python live_subtitle_v2.py --selftest              # 自检（107 项）
python overlay_qt.py --selftest                    # 浮窗自检（61 项）
python overlay_qt.py --demo                        # 只测浮窗，不采集音频
python control_panel.py                            # 只开控制面板
```

---

## 三个必须知道的限制

**1　必须走默认输出设备。** 播放器把音频指到 HDMI/蓝牙，字幕窗就一直空白。

**2　没播放 = 没字幕，这不是故障。**
WASAPI 环回只在系统音频引擎真在出声时才回传数据。
（实测：不播放时 20 秒 0 回调；一播放每秒 96,256 样本。）

**3　翻译模型按语言对装。** 离线 argos 只有 `en→zh` 等少数几对，**没有 `ja→zh`**。
所以非英语内容走在线模型（默认 qwen-mt-flash）。

---

## 延迟是怎么降下来的（每一步都有实测数字）

同一套测量工具、同一段 70 秒真实环回音频：

| 阶段 | 中位 | 平均 | P90 | 最大 |
|---|---|---|---|---|
| 起点 | 4.31s | 4.80s | 6.21s | — |
| 翻译改并行 | 3.91s | 4.21s | 4.70s | — |
| 识别改远端 | 1.00s | 1.02s | 1.20s | 1.20s |
| 批次 8s → 1.5s | **0.98s** | 0.94s | 1.03s | **1.08s** |

**最差值从 7.72 秒降到 1.08 秒** —— 看视频时最难受的是偶发卡顿，所以尾部延迟比平均值更重要。

### 为什么本地 CPU 有 2 秒硬墙

把一次 `transcribe` 的成本按音频长度量了一遍：

```
0.2 秒音频 → 2.078s        4.0 秒音频 → 2.134s
0.5 秒音频 → 1.996s        8.0 秒音频 → 2.288s
```

**成本恒定在约 2.0 秒，与音频长短无关** —— 那是模型前向计算的成本，
不是"处理 4 秒声音"的成本。

所有能想到的旋钮都量过，全都没用：

| 手段 | 效果 |
|---|---|
| beam_size 5 → 1 | 2.153 → 2.084s（-3%） |
| 关掉 VAD | 2.153 → 2.133s（-1%） |
| 线程数 4/8/12/16 | 2.411 / 2.200 / 2.052 / 2.377s（噪声级） |
| compute_type int8 → float32 | 2.200 → 3.167s（更慢） |
| 换小模型 | small 2.363s → base 0.741s → tiny 0.394s |

**换小模型是唯一有效的，但代价不可接受**：`base` 在同一段真实越南语上和 `small`
的逐词一致率只有 **0.53** —— 大量词被听错，字幕会变成胡话。

所以本地下限 = 2.0 秒（前向）+ 约 1.2 秒（等句子说完）≈ 3.2 秒。

### 远端识别为什么能解决

远端 `qwen3-asr-flash` 的固定开销只有 **0.427 秒**，斜率 0.029 秒/音频秒：

```
0.5 秒音频 → 0.427s        8.0 秒音频 → 0.637s
2.0 秒音频 → 0.529s       16.0 秒音频 → 0.879s
```

固定开销小了 5 倍，于是**批次可以缩小**（这点很反直觉，见下）。

---

## 两套识别路径

| | 远端（默认） | 本地（回退） |
|---|---|---|
| 模型 | 阿里云百炼 `qwen3-asr-flash` | faster-whisper `small` / int8 |
| 固定开销 | 0.427s | 2.0s |
| 批次 | 1.5s | 4.0s |
| 端到端延迟 | **0.98s** | 4.9s |
| 需要网络 | 是 | 否 |
| 成本 | 0.00022 元/秒 ≈ **0.79 元/小时** | 0 |
| 自动判语种 | 是 | 是 |

**没有 key 就自动走本地**，行为与旧版完全一致。强制本地用 `--no-remote-asr`。

### 批次要缩小，不是放大

我最初想反了：看到远端接受长音频，就把批次放大到 8 秒去"摊薄调用开销"。
**那是本地的逻辑**（本地固定开销 2.0 秒，只有放大批次才划算）。

远端固定开销只有 0.427 秒，**等批次攒齐的时间反而成了主要成本**。实测：

| max_batch | 中位延迟 | P90 | 调用数/45s |
|---|---|---|---|
| 12.0s | 0.77s | 0.83s | 3 |
| 8.0s | 0.69s | 0.71s | 4 |
| 4.0s | 0.59s | 0.98s | 8 |
| 2.0s | 0.49s | 0.55s | 15 |
| **1.5s** | **0.46s** | **0.55s** | 24 |
| 1.0s | 0.37s | 0.45s | 39 |

**越小越好。** 限流 100 RPM，1 秒一批 = 60 RPM 已在限内；取 1.5 秒
（延迟接近最优，调用数只有一半，留余量）。

---

## 翻译

默认用阿里云百炼 **`qwen-mt-flash`** —— 这是**翻译专用**模型（基于 Qwen3），
不是通用对话模型。实测对比（同一批真实字幕句）：

| 模型 | 中位延迟 | 越南语人名 `doãn chung dũng` |
|---|---|---|
| **qwen-mt-flash** | **0.36s** | **段忠勇** ✓ |
| qwen-mt-lite | 0.33s | 段忠勇 ✓（语言略生硬） |
| qwen-mt-plus | 0.34s | 杜安忠勇 ✗ |
| deepseek-chat | 0.68s | `Doãn Chung Dũng` / 尹中勇 ✗ |

两个实质优势：

1. **快 2 倍** —— 翻译在关键路径上，直接减延迟
2. **越南语人名按中文习惯译** —— 新闻字幕的硬需求，通用模型要么留原文要么猜错

另外 `qwen-mt` 支持 `translation_options` 的**术语定制**与**领域提示**，通用模型没有。

### 翻译选路（每批独立决策，不用累计锁定）

```
本批检测到英语 且 置信度 >= 0.85  →  离线 argos（en→zh，0.04s）
其余情况                          →  qwen-mt-flash（翻译专用，0.36s）
没配 LLM                          →  免 key Google 端点（sl=auto）
```

门槛不对称是有原因的：`argos` 要求源语言**真的是英语**，否则俄语被塞进 en→zh 模型
会出垃圾（实测过）；而走 Google/LLM 时判错也无害，它们自己会再判一次。

**注意：不要用 `--from` 强制源语言。** 实测越南新闻被强制按 `en` 识别时，
whisper 不是"听错"，而是**把越南语直接意译成英文**（还译错），后面全崩。
默认自动检测，检出 `vi` 置信度 0.976，识别正确。

---

## 浮窗操作

| 操作 | 功能 |
|---|---|
| **拖顶部横条** | 移动位置（顶部整条都能拖，不用瞄准） |
| **双击顶部横条** | 回默认位置 |
| 滑轮 / Shift+滑轮 | 字号 / 显示条数 |
| Ctrl+滑轮 / Alt+滑轮 | 不透明度 / 宽度 |
| 方向键 | 微调位置 |
| `[` `]` | 字号 -/+ |
| `-` `=` | 不透明度 -/+ |
| `J` | 对齐：居中 → 左 → 右 |
| `L` | 排布：中文在上+原文在下 / 左右两列 / 只中文 |
| `M` | 调整模式（显示边框） |
| `T` | 深浅底主题 |
| `C` | 回默认位置 |
| `H` | 隐藏 / 显示 |
| `R` | 全部重置 |
| `Esc` | 退出 |

设置持久化到 `overlay_qt.json`，下次启动保持你调好的样子。

### 默认显示参数

| 项目 | 值 | 说明 |
|---|---|---|
| `layout` | `stacked` | 中文一行、原文一行 |
| `text_align` | `center` | 每行各自居中 |
| `font_size` | 24 | 基准 720p，随屏幕等比缩放（1080p → 34px） |
| `width` | 1200 | 字幕行业惯例是每行约 42 字符 |
| `max_lines` | 3 | 保留最近 3 条 |
| `hold_seconds` | 4.0 | 每条停留 4 秒 |
| `text_color` | `#FFFFFF` | 译文（大、亮） |
| `original_text_color` | `#B7C4D8` | 原文（小、暗），比例 0.68 |
| `bg_alpha` | 0.0 | **无窗口框**，纯文字浮在画面上 |
| `opacity` | 0.92 | |
| `outline_width` | 1.65 | 黑描边，保证浅色画面上也能读 |

**无框是刻意的**：成熟方案（mpv / YouTube 字幕）都是纯文字浮在画面上，没有窗口边框。
想要边框按 `M`。

---

## 控制面板

```bat
panel.bat
```

控制面板是**独立进程**（tkinter），关掉它字幕照常运行。

三个标签页：**外观**（9 个滑块 + 排布/对齐单选 + 复选）、**快捷键**（逐条列出）、**说明**。

通信走 **JSON 文件**而不是 socket：面板往 `control_cmd.json` 写改动，浮窗每 300ms
读一次并即时应用（不用重启）；浮窗把设置写回 `overlay_qt.json`，面板定时读回刷新 ——
**所以你在浮窗上按快捷键改了字号，面板上的滑块也会跟着动。** 数值经过浮窗校验，
面板发错值不会把浮窗搞坏。

---

## 语言支持

### 识别：whisper 支持 99 种语言，但不能强制错语言

实测（真实越南新闻音频）：

```
① 自动检测：检出 vi，置信度 0.976  → 识别正确
   "Sẵn hùng sau, Xung mang xe đạp đi bán, rồi mua tàu về quê."
② 强制 en（这就是出错的原因）：
   "In the next episode, Xung Mang will go to the market and buy a boat..."
   ↑ whisper 不是"听错"，是把越南语直接意译成英文，而且译错
```

远端 `qwen3-asr-flash` 也自动判语种，并可通过 `language_hints` 提示。

### 翻译：不同语言对差别很大

| 语言对 | 路径 | 速度 | 质量 |
|---|---|---|---|
| **en → zh** | argos 直连（离线） | 0.04s/句 | 好 |
| **vi / ru / ja / ko → zh** | qwen-mt-flash | 0.36s/句 | 好，人名按中文习惯译 |
| 任意 → zh（无 LLM） | 免 key Google 端点 | 0.89s/句中位 | 可接受 |

离线 `argos` **没有 vi→zh 直连包**，早期版本只能中继（vi→en→zh），质量很糙：
`máu lạnh`（冷血）→「凉血」、人名丢失。换成 qwen-mt 之后这个问题消失。

---

## 噪声与幻觉过滤（都是实测踩出来的）

| 滤除对象 | 实测例子 | 判据 |
|---|---|---|
| 静音上编内容 | 合成噪声/正弦扫频 → whisper 输出 `"You"`，`no_speech_prob=0.771` | `vad_filter` + 段级质量闸 |
| 复读幻觉 | 同一句反复输出 | 压缩比 >= 2.4 |
| 低质量段 | — | `no_speech_prob>=0.55` 或 `avg_logprob<=-0.75` |
| 泰文/老挝文残片 | `แล ้ ว เจ ็ บ เจ ้ า` | Unicode 区间 |
| ASS 字幕样式代码 | `fn黑体fs22bord1chad03aHBE4aH00...` | 已知标签 >=3 个 |
| 维基模板 | `(原始内容存档于2019-09-29)` | 关键字 + 多个短片段无实词 |
| 变音残片 | `ườ ườ ạ ạ` | 多个短片段且无一个长度 >=3 |
| **但不能误杀** | `谢谢`、`好`、`是`、`Cảm ơn các bạn` | 单短词合法 → 必须放行 |

**翻译失败不会污染字幕**：取不到译文时保留上一帧译文（`PENDING_KEEP_OLD`），
不会显示 `[译错 ...]` 之类的占位符，也不会闪成空白。

---

## 流式识别（已实现，默认不启用）

`streaming_asr_remote.py` 实现了百炼的真流式接口
（`qwen-audio-3.0-asr-flash-streaming`，WebSocket，`run-task` / PCM 帧 / `finish-task`）。

协议里 `sentence_end` 字段就是现成的防抖动信号 —— 部分结果照显示（给即时感），
**只在定稿时翻译**，于是中文那行稳定不跳。真实序列统计：24 条部分结果里
**增长 23 次、改写 1 次**（那次是识别自我纠错 `key`→`get`），文本基本只增不减。

**但实测延迟只快 0.18 秒，所以没有接进主流程：**

```
严格口径（对比协议自带的 end_time）：
   句尾 2800ms  → 定稿 3.80s   延迟 1.00s
   句尾 6950ms  → 定稿 8.15s   延迟 1.20s
   句尾 15850ms → 定稿 16.87s  延迟 1.02s
   中位 1.02s

非流式 + 1.5s 批次 = 识别 0.45s + 平均等批 0.75s = 1.20s
```

**两种方案卡在同一面墙上**：等句子说完 + 等服务端确认。流式省掉了攒批，
但"定稿"本身要等句尾确认，把这部分又付回去了。（早期探测看到的"58× 实时"
是**吞吐**，不是"多久能宣告一句结束"。）

为 0.18 秒要付出常驻长连接 + 重连逻辑 + 双路合并 + 一整类抖动风险，不划算。
模块保留，合并状态机可离线验证（用抓下来的 `samples/streaming_msgs.json`，不联网）。

---

## 成本

```
识别  qwen3-asr-flash   0.00022 元/秒  ≈ 0.79 元/小时
翻译  qwen-mt-flash     ≈ 0.12 元/小时
────────────────────────────────────────────
合计                    ≈ 0.9 元/小时
```

限流 RPM 100，正常使用（约 12–20 段/分钟）远低于此。

---

## 环境变量与 API key

远端功能需要一把百炼 key。**仓库里没有任何 key，历史里也没有。**

### 怎么配

```
~/.dsh/.dashscope          一行一条 key，# 开头为注释（推荐）
DASHSCOPE_API_KEY          环境变量（优先于文件）
LLM_BASE / LLM_MODEL / LLM_KEY   自定义 OpenAI 兼容端点
```

`~/.dsh/.dashscope` 的内容就是一行 key：

```
# 阿里云百炼 DashScope API key（北京节点）
sk-ws-你的key
```

**key 是按地域签发的**：北京节点 (`dashscope.aliyuncs.com`) 最便宜，
拿北京 key 打新加坡 endpoint 会 401（实测）。

**没有 key 也能跑** —— 自动退回本地 whisper + 免 key Google 翻译，
行为与旧版完全一致，只是延迟从 0.9s 回到 4.9s。

### 怎么防它进仓库

`sync_release.py` 在复制完成后会**扫描整个产物目录**，发现 key 形状的
字符串就**拒绝发布并退出**（返回码 2）：

```
凭据扫描（防止 key 进公开仓库）
  !! 发现疑似凭据，**已拒绝发布**：
     llm_translate.py　sk- 开头的长密钥　sk-ws-XXXX…
```

扫描三種形状：`sk-` 开头的长串、阿里云百炼 key、以及硬编码的
`api_key = "..."` 之类赋值。`.gitignore` 里也挡了常见凭据文件名。

> **为什么这一步不能省**：key 一旦进过 git 历史，**删文件是没用的** ——
> 它会一直留在历史里，必须换 key。所以要在**提交之前**拦住。

**如果你不小心把 key 贴进了聊天、issue 或 commit message，请把它换掉。**
换 key 不需要改任何代码 —— 覆盖 `~/.dsh/.dashscope` 那个文件即可。

---

## 已知限制（如实）

- **不联网时延迟回到 4.9 秒**（本地 whisper 的 2 秒固定开销无法绕过）
- **免 key Google 端点没有 SLA**，可能被限流或下线；失败时自动退回 argos 中继
- **流式模式（`stream.bat`）长时间连续说话会积压**：CPU 上约 0.49s/次更新 × 2 次/秒
  ≈ 0.98 音频秒/秒，刚好卡在追平 1.0 的临界点。这是硬件决定的，不是实现问题
- **滚动窗口流式转写器窗口未完全有界**（`streaming_asr2.py`，未接默认路线）
- 越南语识别在口语新闻上仍不完美，**上游错则下游难救**

---

## 参考的方案

| 来源 | 借鉴了什么 |
|---|---|
| [mpv](https://mpv.io/manual/master/) | 字幕字号随窗口等比缩放、白色、1.65px 黑描边 |
| [Netflix Timed Text Style Guide](https://partnerhelp.netflixstudios.com/hc/en-us/articles/30806198616339-English-UK-Timed-Text-Style-Guide) | 单语字幕规范：白色、**每行约 42 字符**、断行规则 |
| [VoxGo](https://github.com/zxbb1190/VoxGo_game_voice_trans) | PyQt5 透明置顶浮窗的标志组合、翻译纪律（不补全没听到的内容） |
| [Whisper-Streaming (CUNI)](https://aclanthology.org/2023.ijcnlp-demo.3.pdf) Macháček et al. 2023 | **LocalAgreement-2** 流式策略、按已确认词时间戳裁缓冲、自适应节奏 |
| [Whisper-Ollama-Translator](https://github.com/Cyber-Wukong/Whisper-Ollama-Translator) | `PENDING_KEEP_OLD`（译文未回时不闪屏）、`127.0.0.1` 而非 `localhost`（Windows 上后者有解析延迟）、`temperature 0.1`、只取第一行、剥掉模型自加的前缀 |

---

## 文件

| 文件 | 作用 |
|---|---|
| `live_subtitle_v2.py` | 主程序（自检 107 项） |
| `overlay_qt.py` | 浮窗（PyQt5，自检 61 项） |
| `control_panel.py` | 控制面板（tkinter，独立进程） |
| `remote_asr.py` | 远端识别（qwen3-asr-flash） |
| `llm_translate.py` | LLM 翻译后端（OpenAI 兼容 / Ollama 两种协议） |
| `streaming_asr.py` | 本地流式（LocalAgreement-2，自检 19 项） |
| `streaming_asr_remote.py` | 远端流式（已实现，默认不启用） |
| `streaming_asr2.py` | 滚动窗口流式（实验，窗口未完全有界） |
| `start.bat` / `panel.bat` / `stream.bat` | 启动器（纯 ASCII，见下） |
| `get_models.py` | 装更多 argos 翻译模型 |
| `live.srt` | 实时写出的字幕，播放器可加载 |
| `run.log` | 运行日志（出错看这个） |
| `overlay_qt.json` | 浮窗设置（位置/字号/透明度…） |
| `control_cmd.json` | 控制面板 → 浮窗的命令通道 |

---

## 依赖

```
faster-whisper      本地识别（回退路径）
pyaudiowpatch       WASAPI 环回采集
PyQt5               浮窗
argostranslate      离线 en→zh（可选）
websocket-client    流式识别模块（可选，主流程不需要）
```

```
pip install -r requirements.txt
```

**模型下载**：首次运行会从 HuggingFace 下载 whisper 模型（`small` 约 500MB）。
不想联网下载就用远端识别。

---

## 自检

```
python live_subtitle_v2.py --selftest     107 项
python overlay_qt.py --selftest            61 项
python streaming_asr.py                    19 项
tools/test_borrowed.py                     30 项
tools/test_drag2.py                         9 项
```

`tools/` 下还有延迟诊断与基准脚本，每个都写明了它当初回答的问题：

| 脚本 | 回答的问题 |
|---|---|
| `_measure_e2e_lag.py` | 端到端字幕延迟到底多少 |
| `_bench_bottleneck.py` | 本地延迟瓶颈在哪（定位到 2.0s 固定开销） |
| `_bench_models.py` | 换小模型能省多少、准确度掉多少 |
| `_bench_chunk_size.py` | 批次大小 vs 延迟 |
| `_bench_qwen_mt.py` | qwen-mt vs 通用模型 |
| `_probe_streaming_asr.py` | 流式协议能不能用 |
| `_probe_streaming2.py` | 流式精确延迟 |
| `_capture_streaming.py` | 抓流式消息序列供离线验证 |

---

## 踩过的坑（都写进代码注释了）

| 坑 | 现象 | 解法 |
|---|---|---|
| **tkinter 浮窗不可见** | 系统层面全正常（IsWindowVisible、TOPMOST、DWM cloaked=0、像素里也有文字），用户就是看不到。试过 overrideredirect / Win32 强制置顶 / WS_POPUP / 原生窗口，全失败 | **换 PyQt5**：`Qt.Tool + WA_TranslucentBackground` |
| **Qt 换完仍不渲染** | 接进主程序后浮窗不出现 | **GUI 操作必须主线程**：工作线程丢队列，主线程 QTimer 取 |
| **`.bat` 中文被拆断** | cmd.exe 按 GBK 读 `.bat`，中文变乱码把命令拆开 | 启动器一律纯 ASCII |
| **设置文件自我循环** | 程序算出的坐标存回设置文件，下次读回来，坏值永远出不去 | 载入时校验，越界值丢弃 |
| **坐标系混用** | 物理 1920x1080 / 缩放 125%，Tk 报 864 而 Win32 报 1020 | 统一用 `SystemParametersInfo(SPI_GETWORKAREA)` |
| **`huggingface.co` 不通** | argos 取分词模型时 ConnectTimeout | 初始化时强制预热 + 失败降级；模型从 `argos-net.com` 直链下 |
| **"字幕消失"报三次** | 浮窗代码经查全部正常（几何在屏内、可见、未 cloaked、渲染正常） | 原因是 `bg_alpha=0` 时**空浮窗就是一条全透明窄带**，"在跑但没声音"和"没启动"看起来一样 → 加常驻"正在听"提示 |
| **吞掉所有异常会伪装成业务失败** | `_google_free_ok()` 一直返回 False，实为模块顶部漏了 `import ssl`，`NameError` 被静默吞掉 | 不要把"所有异常"一概吞掉 |
| **翻译专用模型不接受 system 角色** | qwen-mt 每次返回空，表面看"模式选对了但没译文" | 只发 `user` 消息（`no_system`） |
| **同尺寸下换布局买不到高度** | 左右两列和上下两行实测高度完全相同（311px） | 真正的杠杆是字号，不是排布 |

---

## 许可

MIT
