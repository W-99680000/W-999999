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

**点播视频（本地文件 / B站等链接）还有一条更好的路** —— 整片预翻译，
见下面「点播预翻译」一节。直播只能用上面的实时路线（音频播出来才存在），
点播则推荐预翻译：识别用 large-v3 吃满 30 秒上下文、翻译喂完整句，
首句零延迟，质量天花板完全不一样。

```
双击  watch.bat                                      常驻：任意视频全自动出字幕
双击  watch.bat 电影.mp4                              预翻译 + 浮窗同步观看
双击  watch.bat https://www.bilibili.com/video/BVxxx/  同上（网页视频）
双击  cache.bat 电影.mp4                              只产双语 .srt，不开浮窗
```

`watch.bat` 不带参数=**常驻模式**：开着它，之后无论你在浏览器里点开哪个
视频，只要历史里有网址，它就会自己去预翻译并把字幕按播放位置显示出来
（已缓存的秒开）。这是最省事的一种用法。


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

### 启动与主线程（2026-10-18 这轮）

- **浮窗先出、模型后台加载**：原来 `WhisperModel` 在主线程加载（旧环境实测
  4.15s、占启动 92%），浮窗要等它加载完才出现，开头几秒音频也丢了。现在
  浮窗/采集约 0.3s 就绪，模型在后台线程加载，期间浮窗显示"正在加载识别
  模型…"；采集队列（16s 容量）兜住开头音频。
  同机对比（2026-10-18，同一套系统 Python/GPU，旧版快照 vs 新版）：
  **浮窗出现 7.0s → 0.3s；模型就绪 7.0s → 5.2s**（后者顺带删掉了一次
  重复的 `gpu_available()` 真实前向：ctranslate2 已经报过 CUDA 支持，
  合并设备时直接标 cuda，不再每次白等 1.6s）。
- **ASR 不再堵主线程**：`transcribe_batch` 移到单 worker（GPU 0.3~0.9s/批、
  CPU 回退约 2s）。之前这期间 Qt 事件循环停摆——浮窗拖不动、已译好的字幕
  也要等 ASR 结束才上屏；现在主循环约 50ms pump 一次，译文一回来就上屏。
- **停止信号真的能停**：主循环每轮检查 `stop`（原 `_install_kill_switch`
  只 set 不读，管理端点停止要等 12s 超时硬杀）。实测管理端停止 0.2s 返回、
  exit code 正常。
- **管理端日志实时**：`serve.py` 原先把子进程管道 `read(4096)`（会攒满
  4KB 才返回）当实时读用 —— 引擎自己 5 秒就绪，管理端却 42 秒才看到
  "转写:"。改成 `read1(4096)` 后 6 秒就绪（`watch_player.py` 同一处也修了）。
- **设备偏好不再被"临时故障"写死**：CUDA 库缺失那次曾把 CPU 自动存成偏好，
  环境修好后仍一直用 CPU。现在只有真选了 GPU、机器确实没有可识别 GPU、
  或用户显式指定 CPU 时才保存偏好。
- **Qt 与 torch 的 DLL 顺序**：本机先 import PyQt5 再 import torch 会让
  `c10.dll` 初始化失败（ctranslate2 在 import 时就 import torch，整条链起
  不来）。新增 `native_preload.py`，在 Qt 之前用 ctypes 预载 c10.dll。
- **环境体检**：`python tools/doctor.py`（解释器×依赖×CUDA DLL×GPU×
  模型缓存×端口×配置 JSON，一条命令回答"现在哪里坏了、怎么修"）。

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

### 语言切换为什么不卡了（2026-10 这轮优化）

切语言原来会**冻结几秒**，根因有三个，都修掉了：

| 根因 | 修法 |
|---|---|
| 译器缓存 `_tr_cache` 写了从不读，切回见过的语言照样全量重建 | 命中缓存只换引用，**零成本** |
| 未命中时在主循环同步构建（argos 扫包 + 预热推理 + 网络探测），构建期间音频积压、字幕冻结 | **后台线程构建，旧译器继续服务**，构建完下一批生效 |
| `src="auto"`（默认路由）也走一遍 argos 包扫描，纯白费 | auto 时**完全跳过 argos**，构建成本 ≈ 0 |

配套：启动时后台**预热翻译连接**（TLS 握手 + 服务端模型装载），
视频第一句翻译不付冷启动的钱。

### 翻译准确性与实时性（同轮）

- **源语言提示**：whisper 置信 ≥0.85 时把检出的语种（如 Vietnamese）
  写进 LLM 提示词。短碎片（3~8 个词）靠模型自己判语种会漂
  （越南语被判成泰语/粤语都实测见过），一句提示就能消歧。
- **短句表按检测语言兜底**：auto 模式下 PHRASEBOOK 原来命中不了
  （译器按构造时的 src 选表，auto 是空表），现在按当批检测语言查，
  "cảm ơn"→"谢谢" 这类高频句零网络零延迟。
- **LLM 连接按线程分开**：翻译池 2 个 worker 原来共享一条
  http.client 连接（非线程安全），并发时响应字节会交错 —— 改成
  每线程一条长连接，既安全又能真并行。
- **LLM 超时 20s → 8s**：一次卡死原来要冻 20 秒。
- **流式模式语言跟随修复**：原来把源语言钉死在 "en"，越南语内容
  被强制按英语解码；现在与分块路线一致，默认自动检测、
  `--lang` + `--no-detect` 才固定，且检测结果写回供语言锁定用。

### 后端选择

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

## 点播预翻译（本地文件 / 网页链接）

实时路线有一个结构性天花板：音频必须播出来才能听到，识别窗只有几秒、
模型只能用 medium 保速度、翻译单元被切句切碎。点播视频整个文件都在手里，
这些约束全部不成立 —— 所以有第二条管线（`pretranslate.py`）：

```
来源（本地文件 / B站等 URL）
  → 抽音频（本地用 PyAV 直解，URL 用 yt-dlp，B站走官方 API 兜底）
  → large-v3 全文识别（30s 上下文 + VAD + 词级时间戳，离线不计时）
  → 词时间戳切句 → 合并/限长成 cue（读得完、不黏词）
  → qwen-mt 整句批量翻译（带源语言提示，复用实时路线的全部优化）
  → 双语 .srt（中文一行、原文一行）
```

实测（B站 1.4 分钟越南语视频）：识别 10s + 翻译 4s，**15 秒出全片字幕**，
语言检测 vi 置信 0.98。一部电影（2h）约 10~15 分钟，跑过一次进缓存，
第二次秒开。

两种用法：

- **`watch.bat 视频`** —— 预翻译 + 浮窗同步观看。播放位置**全自动同步**：
  有 mpv 时走 IPC；否则走 **Windows SMTC**（系统媒体会话）——浏览器播
  B 站/YouTube、PotPlayer 播本地文件都会自动上报进度，**开播即自动对上，
  暂停/拖进度条自动跟随，什么都不用按**；个别不上报进度的冷门播放器
  才退回手动时钟（空格对齐，←/→ 校准）。
- **`watch.bat --auto`** —— 常驻模式，**点播的终极形态**：
  开着它随便用什么看什么，SMTC 报出标题后**先查缓存**，
  没缓存就**自动反查网址并后台预翻译**，就绪后自动按播放位置上字幕。
  从"手动 cache.bat 预翻译"变成"打开就看，全自动"。
- **`cache.bat 视频`** —— 只产出同名双语 `.srt`，交给播放器
  （mpv / PotPlayer 对同名 srt 自动加载），对齐零维护。

缓存在 `pre_cache/`（按"来源 + 模型 + 目标语言"做键），`--force` 可重跑；
翻译有断点续跑（中途 Ctrl+C，下次从断点继续）。

### 点播/缓存实时性修复（2026-10-18）

症状：**缓存明明在，播放时字幕不跟 / 乱跳**；`--auto` 反复
"预翻译失败：来源: …"，缓存永远建不完。两个根因都修了：

1. **启动器挑错解释器**：`watch.bat` / `cache.bat` 只按
   `faster_whisper + PyQt5` 探测，会选中缺 `winsdk` / `yt_dlp` / `av` 的
   Python —— 于是 SMTC 拿不到播放位置（字幕不跟）、网址反查/下载失败
   （预翻译启动即挂，日志只剩一行"来源: …"）。现在探针把三个依赖都算上，
   并显式优先 `%LOCALAPPDATA%\Programs\Python\Python312`；这种"缺库"
   用 `python tools/doctor.py` 一条命令就能查出来。
2. **SMTC 会话状态拿对象身份当键**：winsdk 每次 `get_sessions()` 都返回
   **新的 Python 包装对象**（实测 `a is b` 为 False）。旧代码用 `id(s)`
   缓存标题、"时间线在前进"、用 `s is self._session` 做迟滞切换 ——
   全部永不命中。后果：多标签页时字幕在几部片之间来回跳、缓存标题对不上、
   预翻译被反复取消。现在改成 `(AUMID, 标题)` 稳定键 + 真正的迟滞切换
   （挑战者连续 4 拍确认），并启用 SMTC 上报的播放倍速外推（1.5x/2x 追剧
   不再越看越漂）。

验证：`python bench/_verify_hysteresis.py`（16 项，含缓存标题匹配 12/12）；
`watch.bat --no-pretranslate` 实测 10 秒内标题零漂移；B 站已缓存网址
走 pretranslate 缓存命中 **0.5 秒**返回。

### 全自动路径：在播视频 → 字幕（`--auto`）

```
系统媒体会话（SMTC）
  ├─ 标题 ──→ browser_history.search_all()  浏览器历史里反查候选网址
  │              └─ 多个候选？用 SMTC 的**时长**核对，挑真正在播的那个
  ├─ 时长 ──→ （消歧 + 估算预翻译耗时）
  └─ 位置 ──→ 字幕按播放位置显示
                        ↑
        autopipe.AutoCache 后台线程 → 子进程 pretranslate.py → 双语 srt
```

三个关键设计，都是踩出来的：

1. **标题 → 网址不能只取"最近访问"**。实测同一个标题在历史里对应 4 个
   不同 BV 号（B 站推荐位自动连播会边切 URL 边沿用旧标题）。4 个候选
   时长分别是 20/32/39/85 秒，只按时间取会取到 20 秒那个 —— 拿错片子
   去预翻译，字幕自然全错。**时长是唯一稳定判据**（`probe_duration`）。
2. **读历史库要复制**（连同 `-wal`/`-journal`）。浏览器一直占着这个
   SQLite 库，直接读会被锁；复制一份再读，原库一个字节都不碰。
3. **预翻译放子进程**。它要拉 large-v3、吃 GPU、跑几分钟，放主进程里
   浮窗会卡死。子进程还让进度（"识别 46%"）能流式回显到浮窗上。

覆盖范围：任何**有网址**的视频。B 站/YouTube 等站点经 yt-dlp；
B 站额外走官方 API（实测 yt-dlp 的 B 站提取器被风控）。
**直播页（live.bilibili.com 等）自动跳过** —— 播出去的音频不存在，
预翻译无从谈起，那种场景走回环路线（`start.bat`）。

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
| `pretranslate.py` | **点播预翻译管线**（文件/URL → large-v3 → 双语 srt） |
| `watch_player.py` | 预翻译字幕的浮窗同步观看器（mpv IPC / **SMTC 自动** / 手动时钟，`--auto` 常驻） |
| `smtc_tracker.py` | 系统媒体会话追踪（读浏览器/播放器进度与时长，可单独跑探测） |
| `browser_history.py` | 标题 → 网址反查（读夸克/Edge/Chrome 历史库，可单独跑：`python browser_history.py "标题"`） |
| `autopipe.py` | 全自动编排：标题 → 反查网址 → 时长消歧 → 子进程预翻译 → srt |
| `watch.bat` / `cache.bat` | 点播观看 / 只产 srt 的启动器 |
| `pre_cache/` | 预翻译缓存（音频、断点、成品 srt） |
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
| **探测把启动挂死** | `gpu_available()` 里 `WhisperModel("tiny")` 的联网请求没有超时，本机 huggingface 不通 → **永久卡住**；而它是在建浮窗之前调的，现象是"启动后什么都没有、也没报错" | 探测加**死线**（线程 + join 超时）、离线优先、联网重试死线更短；并且把这一步的耗时打进日志 |
| **两个 Python 抢活** | 本机两个 3.12：PATH 里那个装了依赖但**没 nvidia cuBLAS**，另一个有 → 用错了 GPU 就用不上，还会拖慢启动 | 由 `babel_admin/dlls.py` **按"能不能加载 cublas64_12.dll"**挑解释器，判据写进代码而不只在 .bat |
| **日志区一直是空的** | 子进程输出用 `readline()` 读 → 没有换行就一直等；界面看着像"点了没反应" | 按字节读、遇 `\r`/`\n` 切行（进度条只用回车刷新） |
| **"关掉了浮窗还在"** | Windows 没有真 SIGTERM，`terminate()` ≈ 硬杀；而 `SIGBREAK` 用 `getattr(signal,…)` **取不到**（报 `Unsupported signal: 21`） | 显式从 signal 模块取 SIGBREAK 送 CTRL_BREAK，走正常退出；超时才硬杀 |

---

## 模型注册表与后台管理端

**模型选择只有一处真相**：`model_registry.json`。
两个翻译模块（**实时回环系统** / **视频缓存**）都从它取默认值，
后台界面改的也是它 —— 不存在"界面一套、程序一套"。

**管理端同时托管翻译链路**：界面上的「启动翻译链路」按钮会拉起
`live_subtitle_v2.py` 子进程 —— **浮窗是原生窗口，只有链路跑起来才有**。
以前管理端只是一个配置页面，界面上没有任何启动入口，所以"打开后台
管理系统"看到的是一片空，而且不报错（最难查的那种）。

```
双击  admin.bat           后台管理端（http://127.0.0.1:8770）
                          → 点「启动翻译链路」→ 屏幕底部出现置顶浮窗

python model_registry.py show        # 两个模式各自解析出的模型与参数
python model_registry.py probe       # 本机实况：哪些模型真的可用
python model_registry.py pending     # 还没接管的硬编码默认值清单
python model_registry.py check       # 注册表自检（41 项 + IO 回归 3 项）

python live_subtitle_v2.py --print-models    # 实时模块实际拿到的模型
python live_subtitle_v2.py --no-registry     # 绕过注册表（回旧默认值）
python pretranslate.py --print-models a.mp4  # 缓存模块实际拿到的模型

python babel_admin/dlls.py              # 挑"真能跑 GPU"的那个 Python（见下）
python tools/check_registry_wiring.py   # 注册表 ↔ 命令行参数 接线校验（20 项）
python tools/check_admin_html.py        # 界面 HTML 体检：配平/零外链/JS 语法（27 项）
python tools/test_admin_api.py          # 管理端 API 回归（34 项）
python tools/test_engine_control.py     # **链路启停**：真起进程、真看浮窗（20 项）
python tools/e2e_admin_browser.py       # **真浏览器**端到端（37 项，含断网退化）
```

### 用哪个 Python 起链路（这一步决定 GPU 能不能用）

本机有**两个 Python 3.12**，依赖装得互补 —— 选错的那个会让 GPU 用不了：

| 解释器 | 模块 | `cublas64_12.dll` | 结果 |
|---|---|---|---|
| `~\.dsh\dsh-runtimes\...\python.exe` | 齐 | **有**（98MB） | `gpu_available()` 5.9s → True |
| `...\Programs\Python\Python312\python.exe` | 齐 | **没有** | 报 `Library cublas64_12.dll is not found`，退回 CPU/远端 |

所以这个选择由**代码**做（`babel_admin/dlls.py`），而不是只写在 .bat 里 ——
管理端是从界面按钮起进程的，那条路径根本不经过 .bat。判据是
"**哪个 python 真能加载 CUDA 库**"，不是"哪个 python 存在"。

### 验证清单（都是可重跑的，不是"我试过了"）

| 脚本 | 回答什么问题 | 项数 |
|---|---|---|
| `model_registry.py check` | 注册表结构、模式覆盖规则、兜底、显存口径 | 41 + 3 |
| `tools/check_registry_wiring.py` | 注册表给的键在 argparse 里真的存在吗、类型相容吗 | 20 |
| `tools/check_admin_html.py` | 界面标签配平、零外链、JS 语法 | 27 |
| `tools/test_admin_api.py` | 界面改模型 → 落盘 → 模块读到的就是它 | 34 |
| `tools/test_engine_control.py` | 界面点启动 → 浮窗真的出现；点停止 → 不残留 | 20（`--quick` 9） |
| `tools/e2e_admin_browser.py` | 真浏览器里渲染出来的到底是不是注册表的数据 | 37 |

`e2e_admin_browser.py` 是这套里最值钱的一个：前面几个都在"代码/文本"
层面断言，而**界面是 JS 渲染的** —— 文件里写着模型名不代表页面上显示了。
它用本机 Edge 无头打开真服务，导出渲染后的 DOM 再断言，并且测了
"服务挂掉时页面必须退化到内置数据并明显标出数据源"（白屏是最常见的
交付事故，且最难提前发现）。

`test_engine_control.py` 则回答用户真正会问的那个问题 ——
"我点了启动，浮窗到底出来没有"。它会真起子进程、真等浮窗、真停掉，
并断言**进程没有残留**（残留 = 浮窗关不掉）。

### 两个模式怎么共用一套模型选择

`modes` 里**只存与 defaults 的差异**，`null` 表示跟随默认：

```json
"defaults": { "asr": "local:faster-whisper-medium", "mt": "remote:qwen-mt-flash" },
"modes": {
  "loopback": { "asr": null,                     "mt": null },   // 都跟随默认
  "cache":    { "asr": "local:faster-whisper-large-v3", "mt": null }
}
```

于是：

| 改哪里 | 实时回环系统 | 视频缓存 |
|---|---|---|
| 改 `defaults.asr` | **跟着变** | 不变（自己覆盖了 large-v3） |
| 改 `modes.cache.asr` | 不变 | 只这里变 |
| 界面点"两个都用" | 变 | 变（同时写 defaults） |

如果两个模式各存一份**完整快照**，改 defaults 就谁也带不动 ——
注册表会退化成又一份散落配置，这正是它要解决的问题。

### 每个模型的代价（注册表里逐条记着）

| 识别模型 | 上卡显存 | 每 4 秒段 | 准确度 | 备注 |
|---|---|---|---|---|
| faster-whisper medium | 1612MB | 0.603s | good | 实时默认 |
| faster-whisper large-v3 | 3123MB | 0.846s | best | 缓存默认 |
| faster-whisper small | 602MB | 0.405s | fair | 越南语实测错词 |
| qwen3-asr-flash（远端） | 0 | 0.90s | ok | 无 GPU 时回退 |

显存那列是**权重 + 解码开销**，不是文件大小：2.9GB 的权重上卡后占
3123MB，多出来的是 KV cache 与激活。差别决定 device_placement 判
"放得下"还是"加载失败"。我第一版按"权重的一半"估解码开销，medium
估成 2077MB（实测 1615MB）—— 高估会让翻译模块白白退回 CPU，
所以这几个数是**用实测反推**的，不是拍的。

### 界面怎么读数据

`babel_admin/serve.py` 是个只监听 `127.0.0.1`、只用标准库的本地服务：

```
GET  /api/models    模型快照（含实况可用性 + 归属哪两个模块）
GET  /api/state     两个模式当前解析结果
POST /api/select    改一个字段（白名单字段 + 模型 id 必须存在）
POST /api/set-mode  改一个模式的绑定
```

三条硬约束，都是为了"选了必须生效"：

1. **写白名单**：只认 `PATCHABLE` 里那几个字段，不是通用配置写入器。
2. **id 必须存在**：错别字返回 400 并列出可选值，不静默落盘。
3. **不可用的模型默认拒绝**：本机跑不起来的模型不许一键切过去（`force=true` 可强改）。

---

## 许可

MIT
