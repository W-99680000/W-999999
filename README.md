# live-subtitle　外文视频实时翻译字幕

看外文视频，**既没字幕也没翻译**时，给你实时中文字幕。

置顶浮窗显示 **中文一行、原文一行**（各自居中），同步写 `.srt` 供播放器加载。

> **有 GPU 时全本地运行**：识别在显卡上跑，不联网、不花钱。
> 没有 GPU 就自动退回远端（需 key），再退回本地 CPU。

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
python live_subtitle_v2.py --device cpu            # 强制用 CPU（默认 auto）
python live_subtitle_v2.py --model large-v3        # 换更大的识别模型
python gpu_setup.py                                # 检查 GPU 与 CUDA 库状态
python live_subtitle_v2.py --selftest              # 自检（144 项）
python overlay_qt.py --selftest                    # 浮窗自检（61 项）
python overlay_qt.py --demo                        # 只测浮窗，不采集音频
python control_panel.py                            # 只开控制面板
```

**显卡要求**：NVIDIA + 已装 CUDA 运行库（见下面「GPU 与 CUDA」一节）。
没有显卡也能跑，会自动选远端或 CPU，只是慢一些。

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
| 起点（CPU 识别，串行翻译） | 4.31s | 4.80s | 6.21s | — |
| 翻译改并行 | 3.91s | 4.21s | 4.70s | — |
| 识别改远端 | 1.00s | 1.02s | 1.20s | 1.20s |
| 批次 8s → 1.5s | 0.98s | 0.94s | 1.03s | 1.08s |
| **识别改本地 GPU** | 更快且更准 | — | — | — |
| **翻译合并调用** | 翻译每段 0.61s → 0.20s | — | — | — |

**最差值从 7.72 秒降到约 1 秒** —— 看视频时最难受的是偶发卡顿，
所以尾部延迟比平均值更重要。

### GPU 上的识别成本（关键数字）

```
音频秒     CPU int8     GPU float16
  2.0s      2.033s        0.157s
  4.0s      2.134s        0.192s
  8.0s      2.288s        0.266s
 16.0s        —           0.400s
```

**CPU 上成本恒定约 2.0 秒、与音频长短无关**（那是模型前向的成本），
**GPU 上只要 0.15~0.27 秒，快 11 倍。** 各模型在 GPU 上的实测：

| 模型 | 每 4 秒段 | 显存 | 说明 |
|---|---|---|---|
| small | 0.405s | 605MB | 准确度不足（见下） |
| **medium** | **0.603s** | **1615MB** | **默认** |
| large-v3 | 0.846s | 3127MB | 更准，多 0.24s |

**GPU 上 beam_size=5 保持不变是有实测的**（`tools/_bench_gpu_beam.py`，
medium、8 段真实 4 秒素材）：beam 1 只比 beam 5 快 0.03s（0.347s vs 0.376s
中位），但文本差异率 3.0% —— beam search 在 GPU 上几乎免费，降到 1 不划算。
（"beam 没省"的旧结论来自 CPU；GPU 上重测后结论一致。）

**准确度差异（真实越南语新闻句）：**

```
原句  "Sáng hôm sau, Xuân mang xe đạp đi bán, rồi mua vé tàu về quê."

small     "Sẵn hùng sau, xung mang xe đạp đi bán, rồi buồn giết tàu về quê."
          ↑ 前半错、后半错
medium    "Sáng hôm sau, Xuân mang xe đạp đi bán, rồi mua vé tàu về quê."  ✓
large-v3  同上 ✓
远端 qwen3-asr   词错 + 全部小写（API 不还原大小写）
```

所以 **GPU 上 small 会自动升到 medium** —— 只慢 0.2 秒，但修掉可辨识的错词。

---

## 三条识别路径（自动选择）

| | **本地 GPU**（默认） | 远端 | 本地 CPU |
|---|---|---|---|
| 触发 | 有 NVIDIA + CUDA 库 | 无 GPU 且有 key | 都没有 |
| 模型 | faster-whisper medium | qwen3-asr-flash | faster-whisper small |
| 每 4 秒段 | **0.60s** | 0.90s | 2.13s |
| 显存 | 1.6GB | — | — |
| 成本 | **0** | ≈0.79 元/小时 | 0 |
| 联网 | 不需要 | 需要 | 不需要 |
| 幻觉检测信号 | **有**（nsp/logp/cr） | 无 | 有 |

**默认优先本地 GPU。** 用 `--device cpu|cuda` 可强制。

### GPU 与 CUDA

faster-whisper 走 ctranslate2，**即使有显卡也需要 cuBLAS 的 DLL**。
缺了会报：

```
RuntimeError: Library cublas64_12.dll is not found or cannot be loaded
```

**注意这个报错很有误导性：它发生在第一次前向计算时，不在加载模型时。**
所以 `WhisperModel(device='cuda')` 能成功构造，一 `transcribe` 才炸 ——
很容易读成"没有 GPU"。

补装（不需要完整 CUDA Toolkit，也不需要管理员权限）：

```bat
pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
python gpu_setup.py        # 检查状态
```

`gpu_setup.py` 会把这些 DLL 目录**前置到 PATH**。
注意：只调 `os.add_dll_directory` **不够**（实测仍然失败），
ctranslate2 的原生加载走 PATH。

**排查**：`python gpu_setup.py` 会打印显卡、显存、CUDA 库目录，
并**真跑一次前向**确认可用 —— 不信设备列表，因为缺 DLL 时设备在、
模型也能构造。

### 远端识别（无 GPU 时的回退）

`qwen3-asr-flash` 的固定开销 0.427 秒、斜率 0.029 秒/音频秒：

```
0.5 秒音频 → 0.427s        8.0 秒音频 → 0.637s
2.0 秒音频 → 0.529s       16.0 秒音频 → 0.879s
```

固定开销小，所以**批次要缩小而不是放大**：

| max_batch | 中位延迟 | P90 | 调用数/45s |
|---|---|---|---|
| 12.0s | 0.77s | 0.83s | 3 |
| 8.0s | 0.69s | 0.71s | 4 |
| 2.0s | 0.49s | 0.55s | 15 |
| **1.5s** | **0.46s** | **0.55s** | 24 |

**越小越好** —— 这点和本地 CPU 相反（那里固定成本 2.0s，必须放大批才划算）。

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
`--mt-terms` 现在**本地 HY-MT 与远端 qwen-mt 共用**一份术语表：远端按句内命中注入
（实测 `doãn chung dũng → 段忠勇`，大小写不敏感），本地沿用 local_mt 的提示词注入。

### 长连接（keep-alive）

每次调用新开 TCP+TLS 是固定开销的一部分。翻译与远端识别都改用 http.client
长连接（断线自动重建并重试一次）后实测：

```
qwen-mt    首呼 0.34s → 续呼 0.20~0.24s（省 ~0.1s/次）
qwen3-asr  首呼 0.58s → 续呼 0.38~0.42s（省 ~0.2s/次）
```

### 合并翻译：一次调用译多段

实测 `qwen-mt` 每次调用有 **0.57 秒固定开销**，真实翻译计算几乎免费：

```
耗时 ≈ 0.770ms/字符 × 长度 + 0.569s
  5 字符 → 0.554s        133 字符 → 0.660s
```

所以**逐段分开译 = 把固定开销付了多次**。合并多段成一次调用：

```
合并 4 段 0.79s　vs　逐条 2.43s　→ 每段 0.61s → 0.20s
```

三档模型（lite/flash/plus）延迟只差 0.04 秒，**换模型买不到时间**。

**合并只能用"分隔符"格式，两个坑都量过：**

| 格式 | 解析结果 |
|---|---|
| 编号行 `1. xxx` | 1/4 ✗ |
| XML `<t>xxx</t>` | 1/4 ✗ |
| 分隔符 | 4/4 ✓ |
| JSON 数组 | 4/4 ✓ |

**`qwen-mt` 是纯翻译引擎、不执行指令** —— 它把编号行/XML 当成待翻译的
正文，译了第一行就停。

**而且分隔符必须是纯字母。** 第一版用 `|||`，模型输出里确实保留了，
但**我们自己的 `clean_translation` 把它删掉了**（那个函数按白名单过滤，
只留 CJK + 拉丁字母 + 数字）：

```
'XXSEPXX' 'SEP' 'ZzQq'   保留 ✓
'|||' '@@@' '%%%' '§§§'  全被删 ✗
```

删掉后几段粘成一段、段数对不上、**静默退回逐条** —— 优化失效且无报错。
所以**决定性约束是后处理的白名单，不是模型**。

攒批上限 `--mt-batch 3`（攒几个语义单元合并），`--mt-batch-wait 2.0`
是兜底（防止为等第 N 个单元让字幕卡住）。`--mt-batch 1` 关闭合并。

### 翻译选路（每批独立决策）

短句表现在**跟检测语言走**（`Translator.set_book`）：默认译器是 auto，
原来 `PHRASEBOOK.get("auto")` 恒为空表 —— 建好的英语/越南语短句表一次都没
被用过。现在每批按检测结果切换，高频短句直接查表：零延迟、零成本、不会错。

```
本批检测到英语 且 置信度 >= 0.85  →  离线 argos（en→zh，0.04s）
其余情况                          →  qwen-mt-flash（翻译专用，0.36s）
没配 LLM                          →  免 key Google 端点（sl=auto）
```

**默认不走 argos**：实测同一批句子上 qwen-mt 每项都更优，而 argos 会把
"东京都知事" 译成 "东京都督"。代价只有 0.25 秒。
想省那 0.25 秒用 `--prefer-offline` 退回 argos。

**注意：不要用 `--from` 强制源语言。** 实测越南新闻被强制按 `en` 识别时，
whisper 不是"听错"，而是**把越南语直接意译成英文**（还译错），后面全崩。

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

**识别语言锁定**（分块路径）：每批自动检测要多跑一次前向、且短音频上会抖。
现在连续 3 批同语言高置信（≥0.85）后把语言直接传给 transcribe（跳过检测）；
每 15 批留一批探针复检，连续 2 次探针检出别的语言才解锁 —— 防止中途换内容
被锁死在错语言上。流式模式原来把 `--from`（默认 en）硬塞给识别器，
已改为默认自动检测（那条路正是"越南语被意译成错误英文"的翻版）。

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

切段也是同理：连续语音到 `max_len` 上限时不再就地硬断，而是在尾部 0.6s
窗口内找 RMS 最低点切（软切点）—— 硬切可能从一个词中间断开，断点处
ASR 必然听错半个词，而这个错会原样进字幕。

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

- **GPU 需装 CUDA 运行库**（`pip install nvidia-cublas-cu12`），
  否则会退回远端或 CPU。`python gpu_setup.py` 可诊断
- **显存占用**：medium 1.6GB、large-v3 3.1GB。8GB 卡够用，
  但同时玩游戏要注意余量
- **不打字幕的短视频/纯音乐内容**会出现少量误听；噪声门先挡静音，
  但背景音乐与重叠说话挡不住（实测依据见 `_bench_dsp_gate.py`）
- **免 key Google 端点没有 SLA**，可能被限流或下线；失败时自动退回 argos 中继
- **流式模式（`stream.bat`）长时间连续说话会积压**：CPU 上约 0.49s/次更新 × 2 次/秒
  ≈ 0.98 音频秒/秒，刚好卡在追平 1.0 的临界点
- **滚动窗口流式转写器窗口未完全有界**（`streaming_asr2.py`，未接默认路线）
- 越南语识别在口语新闻上仍不完美，**上游错则下游难救**
- **本地翻译模型没有接**：llama.cpp 官方无 Windows CUDA 预编译包，
  `llama-cpp-python` 源码编译需完整 CUDA Toolkit。而合并翻译后
  每段只要 0.20 秒，收益已经很小

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
| `live_subtitle_v2.py` | 主程序（自检 144 项） |
| `overlay_qt.py` | 浮窗（PyQt5，自检 61 项） |
| `gpu_setup.py` | **GPU 探测与 CUDA 库加载**（含自检，可单独跑） |
| `control_panel.py` | 控制面板（tkinter，独立进程） |
| `remote_asr.py` | 远端识别（qwen3-asr-flash） |
| `llm_translate.py` | LLM 翻译后端 + **合并翻译** |
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
faster-whisper      识别（GPU 或 CPU）
pyaudiowpatch       WASAPI 环回采集
PyQt5               浮窗
argostranslate      离线 en→zh（可选）
websocket-client    流式识别模块（可选，主流程不需要）
nvidia-cublas-cu12  **GPU 识别必需**（提供 cuBLAS DLL）
nvidia-cudnn-cu12   GPU 识别建议装
```

```
pip install -r requirements.txt
pip install nvidia-cublas-cu12 nvidia-cudnn-cu12   # 用 GPU 时才需要
python gpu_setup.py                                 # 确认可用
```

**模型下载**：首次运行会从 HuggingFace 下载 whisper 模型
（medium 约 1.5GB、large-v3 约 3GB）。已经下过就有缓存，是秒级加载。

---

## 自检

```
python live_subtitle_v2.py --selftest     144 项
python overlay_qt.py --selftest            61 项
python streaming_asr.py                    19 项（偶发 1 项时间带断言挂）
tools/test_borrowed.py                     30 项
tools/test_drag2.py                         9 项
python gpu_setup.py                        GPU 状态与可用性
```

`tools/` 下还有延迟诊断与基准脚本，每个都写明了它当初回答的问题：

| 脚本 | 回答的问题 |
|---|---|
| `_measure_e2e_lag.py` | 端到端字幕延迟到底多少 |
| `_bench_bottleneck.py` | 本地 CPU 延迟瓶颈在哪（定位到 2.0s 固定开销） |
| `_bench_gpu.py` | **GPU 上识别成本多少（0.15~0.40s）** |
| `_test_compare.py` | **本地 GPU vs 远端，哪个又快又准** |
| `_bench_models.py` | 换小模型能省多少、准确度掉多少 |
| `_bench_chunk_size.py` | 批次大小 vs 延迟 |
| `_bench_mt_cost.py` | **翻译的固定开销占比（0.57s 固定）** |
| `_bench_mt_batch.py` | **合并翻译用哪种格式能可靠切分** |
| `_bench_qwen_mt.py` | qwen-mt vs 通用模型 |
| `_bench_dsp_gate.py` | 纯 DSP 能否替代幻觉检测 |
| `_probe_streaming_asr.py` | 流式协议能不能用 |
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
| **从一次报错推断硬件不存在** | `cublas64_12.dll not found` → 我判定"本机无 GPU"，据此把识别搬到远端并写进文档。实际有 RTX 4070，只缺 DLL | **查一手来源**（`nvidia-smi`），不要从一次失败推断硬件 |
| **CUDA 报错发生在错误的时机** | `WhisperModel(device='cuda')` 能成功构造，一 `transcribe` 才炸 → 极易误读 | 探测 GPU 要**真跑一次前向**，不信设备列表 |
| **`os.add_dll_directory` 对 ctranslate2 无效** | 加了 DLL 目录仍然报找不到 cuBLAS | 必须**前置 PATH** |
| **纯翻译引擎不执行指令** | "请按编号输出"被 qwen-mt 当成正文，只译第一行（1/4） | 用**结构分隔符**，不要用指令式提示 |
| **优化被自己的后处理杀死** | 合并翻译的分隔符 `\|\|\|` 被 `clean_translation` 的白名单删掉 → 静默退回逐条、无报错 | 分隔符必须**能活过后处理**（用纯字母），并加断言 |
| **"用了没定义"的参数** | `args.prefer_offline` 有调用点、没 `add_argument` → 程序**启动即崩**，看起来像"不显示字幕" | 自检里加通用检查：每个 `args.X` 必须有定义 |

---

## 许可

MIT
