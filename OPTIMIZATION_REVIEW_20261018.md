# live-subtitle 优化空间审查（2026-10-18）

**范围**：`D:\NEW WORK\work\live-subtitle`（当前工作树：13 个已修改文件 + 35 项未跟踪，含整个 `babel_admin/`）。本报告只读取、只做体检，**没有改动任何现有代码**；新增了两个工具文件（见 §11）。

**先给结论**：这个项目在"算法/延迟"层面已经做得相当到位（README 的延迟表、噪声门、合并翻译、设备放置、优雅停止都是真优化）。剩下的空间主要不在"再抠 100ms"，而在四处：

1. **环境已经漂移，GPU 链路当前不可用**（不是代码问题，但直接决定 1.2s 还是 4.9s 延迟）；
2. **启动感知 4.5s 全花在模型加载**，而且**是在浮窗出现之前**；
3. **ASR 在 UI 主线程里同步跑 0.3–0.9s/批**，把浮窗事件和已译好的字幕一起堵住；
4. **点播预翻译没有用 faster-whisper 的批推理**（现成能力，装好的 1.2.1 里就有），这是 VOD 路径最大的一块免费提速。

---

## 0. TL;DR（按优先级）

| # | 优化项 | 影响 | 工作量 | 风险 | 优先级 |
|---|---|---|---|---|---|
| 1 | 环境自愈：补 CUDA DLL、补 winsdk/yt-dlp、锁解释器（`tools/doctor.py` 已交付） | **GPU 0.6s/批 ↔ CPU 2s/批**，现在正掉在 CPU/远端 | 0.5h | 低 | **P0** |
| 2 | 启动并行化：先出浮窗/采集，模型后台加载；`local_files_only=True` | 感知启动 **4.5s → <0.5s**；网络抖动不再拖死启动 | 2–4h | 中 | **P0** |
| 3 | ASR 移出主线程（UI 线程只做 pump/flush） | 字幕显示延迟 −0.3~0.9s；ASR 期间浮窗不再冻 | 4–8h | 中 | **P0** |
| 4 | 静音自然边界触发 ASR 早发（flush-on-silence） | 稀疏语音首字延迟 −0.3~0.8s | 2h | 低 | P1 |
| 5 | compute_type/模型矩阵重测（int8_float16、large-v3-turbo） | 同延迟下更准，或同精度下快 1.3–2× | 2h | 低 | P1 |
| 6 | VOD 预翻译改用 `BatchedInferencePipeline` | 预翻译吞吐 2–3×（large-v3 GPU） | 3h | 中 | P1 |
| 7 | 48k→16k 换多相滤波（现状混叠仅 −4.4 dB） | ASR 输入质量；CPU +0.8% 单核 | 1h | 低（需 A/B WER） | P1 |
| 8 | 远端 ASR 连接复用（现在每次 urlopen 新建 TLS） | 无 GPU 路径每批 −0.1~0.2s | 1h | 低 | P2 |
| 9 | 观测性：日志时间戳 + 每批 JSONL 指标 + 修 "已显示 0 段" | 之后每次调优可量化/可回归 | 2h | 低 | P2 |
| 10 | 架构/仓库：拆单文件、测试出仓、overlay 去重/同步检查、依赖锁、`babel_admin/` 进 git | 维护风险（**管理端 40 个文件完全未纳管**） | 1–2 天 | 低 | P2 |

---

## 1. P0：环境已经漂移——GPU 当前不可用

**证据**（`python tools/doctor.py`，2026-10-18）：

```
FAIL  C:\Users\w\.dsh\dsh-runtimes\...\python.exe
      缺[核心]: faster_whisper, ctranslate2, PyQt5, pyaudiowpatch
      cublas64_12.dll: **缺**   cudnn64_9.dll: **缺**
CORE  C:\Users\w\AppData\Local\Programs\Python\Python312\python.exe
      缺[watch/网页视频]: winsdk, yt_dlp
      cublas64_12.dll: **缺**   cudnn64_9.dll: **缺**
GPU: NVIDIA GeForce RTX 4070 Laptop GPU 8188 MiB driver 572.83   ← 卡在
```

- 昨天 18:51 的 `run.log` 还是 `识别：本地 GPU medium float16`；今天两个解释器都找不到 `cublas64_12.dll`。最可能是 **DSH 运行时（`.dsh/dsh-runtimes/...`）在 10-17 20:45 被更新**，把项目依赖换掉了（`babel_admin/dlls.py` 的注释里已经记录过同类事故）。`nvidia-smi` 正常，torch 里也没有 CUDA DLL。
- **另一个盲区**：`dlls.py` 的 `NEED_MODULES` 只查 5 个模块（第 40 行），所以 `winsdk`/`yt_dlp` 缺失它照样判 "模块齐"。前者让 watch 的 SMTC 自动同步降级，后者让网页视频拉流路径失效。
- 模型缓存没问题：HF 里 6 个 faster-whisper 模型、5.3GB（medium/large-v3/large-v3-turbo 都在）。

**修复**（一行）：
```bat
"C:\Users\w\AppData\Local\Programs\Python\Python312\python.exe" -m pip install ^
    nvidia-cublas-cu12 nvidia-cudnn-cu12
"C:\Users\w\AppData\Local\Programs\Python\Python312\python.exe" -m pip install ^
    winsdk yt-dlp
```

**根治（建议做，约半天）**：不要让项目依赖"会被平台更新替换的系统运行时"。
1. 在项目根建 `.venv`（从当前装了核心依赖的 Python312 建），把 `requirements.txt` 装进去；
2. `requirements` 从 `>=` 改成**锁定版本**（至少加 `requirements.lock.txt`：faster-whisper 1.2.1 / ctranslate2 4.8.1 / numpy 2.5.2 / PyAudioWPatch 0.2.12.9 / PyQt5 …）；
3. `start.bat` / `watch.bat` / `babel_admin/dlls.py` 的候选顺序把 `.venv\Scripts\python.exe` 放第一；
4. `tools/doctor.py` 加进 README 的"自检"表（一条命令回答"现在到底哪里坏了"）。

---

## 2. P0：启动 4.5s，92% 是模型加载，而且发生在浮窗出现之前

**证据**（`work/startup_stats.json`，12 次启动）：

| 阶段 | 中位耗时 | 占比 |
|---|---|---|
| 转写器（模型加载 + 设备盘点） | **4.15s** | 92% |
| 音频采集 | 0.05s | 1% |
| 浮窗就绪 | 0.29s | 6% |
| **总计** | **4.49s** | — |
| 最差一次 | 13.37s（转写器 12.53s） | 网络/探测抖动 |

代码顺序：`run()` 里先建 `BatchTranscriber`（第 1877–1892 行，构造里 `WhisperModel(...)` 加载 1.5GB 权重 + CUDA context），**之后**才建采集（1951）和浮窗（1961），`SS.mark("浮窗就绪")` 在 1973。用户点"启动"后的 4.2 秒里屏幕上什么都没有；管理端 `wait_ready()` 也要等到 "转写:" 或 "浮窗位置" 行。

**优化 A（感知层，改动小）**：先建浮窗/采集（~0.3s），模型放后台线程加载，期间浮窗显示"正在加载识别模型…"，加载完再进入主循环。
```python
# run() 中示意
ov = Overlay(...); cap = LoopbackCapture(...).start()      # ~0.3s
th_box = {}
def _load_model():
    try:
        th_box["th"] = BatchTranscriber(args.model, ..., local_files_only=True)
    except Exception as e:
        th_box["err"] = e
threading.Thread(target=_load_model, daemon=True).start()
ov.idle_hint("正在加载识别模型…")   # 常驻提示，用户知道在跑
while "th" not in th_box and "err" not in th_box:
    ov.pump(); time.sleep(0.02)
```
管理端 `wait_ready` 的 marks 相应提前（"浮窗位置" 现在会先出现），把 `"转写:"` 保留为"ASR 真正就绪"。

**优化 B（真实耗时）**：`WhisperModel` 有 `local_files_only=True`（已确认 1.2.1 签名），缓存存在时直接本地加载，**跳过 huggingface 的元数据请求**——那正是最差一次 12.5s 的来源（README 里也记过"探测把启动挂死"）。当前代码只在异常后才设 `HF_HUB_OFFLINE` 重试（`live_subtitle_v2.py` 第 687–699 行），属于"先撞墙再绕"。
```python
kw = dict(device=device, compute_type=compute_type, cpu_threads=threads)
try:
    self.m = WhisperModel(model, local_files_only=True, **kw)   # 已缓存 → 秒开
except Exception:
    self.m = WhisperModel(model, **kw)                          # 首次才联网
```
把 `HF_HUB_ETAG_TIMEOUT/HF_HUB_DOWNLOAD_TIMEOUT` 再压到 5s（`gpu_setup.py` 里已有这段，但没有全局设置）。

**优化 C（重复启动，可选 P2）**：管理端现在是"每次点启动起一个新进程、重新加载 4.15s"。可加 `--warm` 常驻引擎（模型加载后空闲等控制指令），启动/停止只切采集与浮窗——重复使用场景收益最大。

---

## 3. P0：ASR 在 UI 主线程同步跑，堵住浮窗和"已译好"的字幕

**证据**：
- 主循环（第 2252–2445 行）里 `ov.pump()` 每轮只调一次（2255），而 `texts = th.transcribe_batch(...)`（2331）是**同步阻塞**的：`run.log` 实测每批 `转写 0.31–0.87s`（GPU），CPU 回退时固定 ~2.0s。
- 已译好的字幕只在 `_drain_translations()`（2286 / 2433）里显示，而 2433 在 transcribe **之后**；翻译线程在 ASR 期间完成的批次，必须等这批 ASR 结束才上屏。
- Qt 的 QTimer（`_flush_ui` 每 40ms、`_expire` 每 500ms、`_cmd_timer` 每 300ms）都靠 `processEvents()` 驱动；ASR 阻塞期间全部停摆——拖浮窗、面板命令、条目过期都会"卡一下"（CPU 回退时是 2 秒一卡）。
- 另外 `cap.read(timeout=0.25)` 在静音时会让主循环每轮最多睡 250ms，浮窗交互也会被这个粒度限制。

**优化**：把 ASR 也放进 worker（翻译已经是 worker），主线程只做：读音频入队 → `ov.pump()` → 非阻塞 drain；ASR future 完成后由主线程更新显示。
```python
_asr_pool = _concurrent.ThreadPoolExecutor(max_workers=1)
_asr_fut = None
...
while True:
    if ov: ov.pump()
    if _asr_fut is not None and _asr_fut.done():
        texts = _asr_fut.result(); _asr_fut = None
        # 走原来的 _keep / mt_pending / 提交翻译 流程
    blk = cap.read(timeout=0.05)          # 50ms，而不是 250ms
    ...
    if due and _asr_fut is None:
        _asr_fut = _asr_pool.submit(th.transcribe_batch, segs_in, _lang)
    _drain_translations(block=False)       # 每轮都排空，不再等 ASR
```
预期：**显示延迟减少 0~0.9s**（取决于翻译是否恰好在 ASR 期间完成），浮窗在 ASR 期间保持可拖动/可滚动；CPU 回退时体感改善更明显。注意 `th.last_lang`/`gate` 等状态由 worker 写、主线程读，需在 future 完成时统一取快照，避免竞态。

---

## 4. P1：静音边界提前触发 ASR（flush-on-silence）

现状触发条件（2265）：`due = pending and (batch_dur >= args.max_batch or waited >= args.max_wait)`，即最多干等 `max_wait=1.2s`。但翻译侧已经会用"自然句边界"（`_is_natural`, 2375）；ASR 侧没有用。既然 Segmenter 的静音切分说明说话人停了、下一段音频不会马上来，**此时立刻转写就是延迟最优**：

```python
_new_segs = seg.push(...)
natural_now = any(len(s)/TARGET_RATE < args.max_seg - 0.05 for s in _new_segs)
due = pending and (batch_dur >= args.max_batch or waited >= args.max_wait
                   or (natural_now and batch_dur >= args.min_flush))  # min_flush≈0.6s
```
GPU 上多一次转写的边际成本只有 0.15~0.2s（4 秒段 0.266s、2 秒段 0.192s），换 0.3~0.8s 首字延迟值得；CPU 回退时加 `--no-early-flush` 关闭（固定成本 2s 摊薄更重要）。
验证：`python tools/_measure_e2e_lag.py`（项目已有，README 引用过）跑同一段 70s 素材，对比 P50/P90。

---

## 5. P1：compute_type / 模型矩阵值得重测（尤其在 4070 上）

- 现在写死 `device="cuda", compute_type="float16"`（1881 行）。ctranslate2 在 RTX 4070（CC 8.9）上通常还能跑 `int8_float16`；显存约降 30–40%、速度常快 20–50%。这意味着两种可能：
  - medium 保持精度、延迟从 0.60s → 0.35~0.45s；
  - 或 **large-v3 换 int8 后回到 medium 的延迟**（准确度更好，README 里 large-v3 只慢 0.24s）。
- 另外 `beam_size=5` 是 CPU 时代定的（README 记录 beam=1 在 CPU 上没省，因为固定成本主导）；**GPU 上固定成本只占一小部分，beam 的边际成本会显出来**，值得重测 beam=1/5 的延迟与 WER。
- 装好的 `faster-whisper 1.2.1` 还带 `large-v3-turbo`（缓存里已有，`mobiuslabsgmbh/faster-whisper-large-v3-turbo`），8 层解码器，通常比 large-v3 快 2–3×、质量接近——很适合"准 + 快"档。
- 注册表目前没有 `compute_type` 字段，`device_placement` 的显存预算也按 float16 估的。建议：注册表加 `compute_type`，`_apply_registry` 透传，`model_manager.whisper_installed` 不变。
- 实验脚本：`tools/_bench_models.py`、`tools/_test_compare.py` 改一行即可扫矩阵（模型 × compute_type × beam）。

---

## 6. P1：点播预翻译——用现成的批推理，2–3× 免费提速

`pretranslate.py` 现在逐块调用 `model.transcribe(...)`（277、483 行，`beam_size=5, word_timestamps=True, condition_on_previous_text=True`）。装好的 faster-whisper 1.2.1 **有 `BatchedInferencePipeline`**（已确认可导入）：

```python
from faster_whisper import WhisperModel, BatchedInferencePipeline
model = load_asr(...)            # 只加载一次
pipe = BatchedInferencePipeline(model)
segs, info = pipe.transcribe(audio_chunk, batch_size=8,
                             language=lang, vad_filter=True,
                             word_timestamps=True, initial_prompt=prompt)
```
- 官方/社区实测在 GPU 上常见 2–4× 吞吐提升；large-v3 处理 1 小时视频可从 ~12 分钟降到 ~5 分钟量级。
- **代价**：`BatchedInferencePipeline` 不支持 `condition_on_previous_text=True`（会强制关掉）。当前 VOD 质量策略依赖它做跨块连贯性——建议做成 `--fast` 开关，先用 `samples/` 的素材 A/B 译文质量，再决定默认值。
- 第二个独立优化：预翻译是"解码→ASR→翻译→落盘"串行，翻译（0.2–0.6s/单元）完全可以与下一块的 ASR 重叠（单 worker 线程 + 有序落盘即可）。

---

## 7. P1：48k→16k 现在是裸线性插值，混叠只压了 4 dB

`to_mono16k`（480–491 行）用 `np.interp` 直接 3:1 抽取。48k→16k 的奈奎斯特是 8kHz，8–24kHz 会整个折叠回 0–8kHz。用合成信号量化（`tools/_bench_resample.py`，本机实测）：

| 方案 | 12k→4k 混叠（相对 440Hz） | 10k→6k 混叠 | 每 10ms 块 CPU |
|---|---|---|---|
| **linear（现状）** | **−4.4 dB** | **−4.4 dB** | 7.3 µs |
| FIR 127 taps（无 scipy 备选） | −106 dB | −96.7 dB | 139 µs |
| scipy `resample_poly` | −72 dB | −61.8 dB | 77 µs |

即：现状把高频几乎原样折回语音带（音乐/齿音/掌声会变成 4–6kHz 的"脏东西"），而代价只有单核 0.8%。把 `to_mono16k` 的最后一步换成：
```python
from scipy.signal import resample_poly
x = resample_poly(x, 1, 3).astype(np.float32)      # 48k → 16k
```
没有 scipy 的解释器（当前 system Python 就没有）用带状态的 Kaiser-FIR 抽取（块间保留滤波器尾巴，约 30 行）或加 `soxr` 依赖。验证：`samples/loopback_test.wav` 跑 `tools/_bench_models.py` 对比 WER/字错。**注意**：这是输入质量优化，不保证 WER 一定降（whisper 的 mel 有鲁棒性），必须 A/B。

---

## 8. P2：远端 ASR 还在"每批新建 TLS 连接"

`remote_asr.py` 的 `_attempt`（183 行）用 `urllib.request.urlopen(...)`，每次调用新建 HTTPS 连接；而 `llm_translate.py` 已经做了线程内 `http.client.HTTPSConnection` 复用（见其 225–260 行注释）。无 GPU 用户的远端路径每批多付一次 TLS 握手（~0.1–0.2s），还多付 base64 data-URI 的 33% 上传体积。建议：
1. 照抄 `llm_translate` 的 thread-local 连接复用；
2. 遵守 429 的 `Retry-After`（现在固定 sleep 0.25s，第 159 行）；
3. 若 API 支持，用 16k/16bit PCM 直传替代 base64 wav（先查文档，别猜）。

---

## 9. P2：观测性——现在的日志无法回答"延迟花在哪"

**证据**：
- `run.log` 的每一行都没有时间戳；只有每批的 `转写 x.xxs`，没有"音频位置/提交翻译时刻/上屏时刻"，端到端延迟只能靠 `_measure_e2e_lag.py` 离线测。
- `已显示 0 段` 有误导性：它在 `_drain_translations` 之前打印（2441 行附近），只是"此刻未就绪"，下一轮就会显示——排查时容易误判成"字幕没上屏"（`A6/深挖`那轮日志里就出现过几十行 0 段）。
- 管理端 4 个轮询器：engine 1.5s（`index.html:2462`）、overlay/repo/live 各 2s（2676/2777/2844），每个都拉 JSON；本地无所谓，但"ready" 状态最大滞后 1.5s，用户感觉启动慢半拍。

**建议**：
1. 每行日志加 `[HH:MM:SS.mmm]`（最简单：统一走 `logging`，或 `print(f"[{time.strftime('%H:%M:%S')}] ...")`）；
2. 每批写一行 JSONL 指标到 `work/metrics.jsonl`：`{t, batch, audio_s, t_asr, t_mt_submit, t_mt_done, t_show, lag_s}`——之后每次调参都能直接对比 P50/P90（现在只有 bench 脚本才有这能力）；
3. 把 `已显示` 改成 `本批提交翻译 N 段｜上批上屏 M 段`，避免歧义；
4. 管理端把轮询换成 SSE/WebSocket（`/api/events` 推 engine 日志与状态），ready 检测从 1.5s 缩到 ~0.1s，同时省 4 个定时器。

---

## 10. P2：架构/仓库——当前最大的"非性能"风险

1. **`babel_admin/` 整个目录 40 个文件未被 git 跟踪**（`git ls-files babel_admin` 返回空；`index.html` 161KB + `serve.py` 46KB 是管理端全部界面与服务）。同样未跟踪的还有 `app.py`/`model_registry.py`/`hardware_detect.py`/`admin_qt.py` 等——**一次误删/误回滚就没了**。当前工作树：13 个 M + 35 个未跟踪，建议尽快分组提交。
2. **单文件 3289 行**：`live_subtitle_v2.py` 里同时有音频、ASR、翻译、浮窗适配、SRT、主循环、541 行 selftest（2491–3031）。拆分建议（不改变行为）：
   - `core/audio.py`：`LoopbackCapture` + `to_mono16k` + `Segmenter` + `NoiseGate`（约 500 行）
   - `core/asr.py`：`BatchTranscriber` + `LanguageLock`（约 450 行）
   - `core/translate.py`：`Translator` + `LruTtlCache` + 短句表（约 430 行）
   - `core/run.py`：`run()` 主循环；`live_subtitle_v2.py` 只留 CLI 入口
3. **selftest 读自己的源码做断言**（2493 行 `_SRC = open(__file__).read()`，例如断言 `"_is_natural" in _src`）：一重构就误报 FAIL，而且 541 行测试挤在运行时文件里。建议拆到 `tests/`（pytest），`--selftest` 保留为兼容入口；少数"源码契约"（如 `.bat` 纯 ASCII、`args.X` 都有定义）改成对文件/argparse 的显式检查。
4. **overlay 的两份 1427/1438 行是刻意复刻**（`watch_overlay.py` 头部写了原因：独立设置文件、互不覆盖）。可以保留，但加一个 `tools/check_overlay_sync.py`：对两份文件做 AST/函数体相似度检查，超出阈值就告警——防止只改一边。
5. **依赖清单**：`requirements.txt` 全是 `>=`（不可复现），且缺 `app.py` 需要的 `pywebview`/`pystray`/`Pillow`（现在恰好装了，但新机器按 requirements 装完 `app.bat` 会缺 webview）。补全 + 加 lock。
6. **仓库卫生**：`samples/loopback_test.wav`（2.5MB）等音频进了 git，建议移 LFS 或改成下载脚本；`overlay_qt.json.bak` 未被忽略（加 `*.bak`）；`tools/` 63 个、`bench/` 23 个脚本——在 `tools/README.md` 里按"回答什么问题"列个索引（README 已有部分），并把一次性 debug 脚本归档。
7. **CI**：现有自检很全（live 144 项、overlay 61 项、streaming 19 项、admin API 34 项、engine control 20 项、浏览器 e2e 37 项），但没有一个入口一起跑。建议 `tools/ci.ps1`：`compileall` → `doctor` → 各 selftest → `model_registry.py check` → `test_admin_api.py`；doctor FAIL（如当前 GPU 缺失）时自动跳过 GPU 相关项而不是报红。

---

## 11. 本轮新增的两个工具（未改现有代码）

| 文件 | 用途 |
|---|---|
| `tools/doctor.py` | 一条命令体检：解释器候选 × 依赖分组 × CUDA DLL × GPU × HF 缓存 × 端口 × 关键 JSON，输出修复命令与退出码。**本报告 §1 的证据就是它生成的。** |
| `tools/_bench_resample.py` | 复现 §7 的混叠/成本数字（scipy 可用时对比 `resample_poly`，不可用时用 FIR 备选）。 |

运行：
```
python tools/doctor.py
python tools/doctor.py --json --out work/doctor.json
python tools/_bench_resample.py
```
（当前 `doctor` 会以退出码 1 结束——那是 GPU 缺失的 FAIL，不是脚本错。）

---

## 12. 不建议动的（已经验证过的部分）

- 采集块 10ms（实测到 3ms 都不丢帧，但收益只剩 20ms，且队列内存变大）；
- 自适应噪声门 P5+8dB / 上限 −48（有 85 批 0 字幕的实测依据，改回去会重演）；
- 合并翻译 + keepalive + LRU/TTL（0.61s/段 → 0.18s/段，已到位）；
- 设备放置/stableId/资源失败重试（`device_placement.py`、`release_failed_load`）；
- `SIGBREAK` 优雅停止 / 看门狗；
- PyQt 浮窗方案、纯 ASCII 的 `.bat`；
- `live.srt` 整文件重写（80 条上限，I/O 可忽略，换来播放器兼容性）。

---

## 13. 建议的执行顺序（前三步今天就能做）

1. **修环境**：pip 补 `nvidia-cublas-cu12 nvidia-cudnn-cu12` + `winsdk yt-dlp`；跑 `python tools/doctor.py` 直到 FAIL 清零。然后跑一次 `python live_subtitle_v2.py --selftest` 与 `python overlay_qt.py --selftest` 确认没被环境漂移打坏。
2. **启动并行化**（§2-A/B）：改动只在 `run()` 开头 30 行 + `BatchTranscriber.__init__` 一处，风险低；用 `work/startup_stats.json` 前后对比"总耗时/浮窗就绪"两个数。
3. **ASR 移出主线程**（§3）：改动集中在 2252–2445 的主循环；先用 `_measure_e2e_lag.py` 记下当前 P50/P90，再对比。

再往后：compute_type 矩阵（§5）与 VOD 批推理（§6）是"几乎免费的提速"，但都需要 GPU 环境恢复后才能测——所以 §1 是前置条件。

---

## 附：证据索引（可复算）

| 结论 | 证据文件/行 |
|---|---|
| 启动 4.5s、模型 4.15s | `work/startup_stats.json` |
| ASR 0.31–0.87s/批 | `run.log`（141 行，批次 1–78） |
| 模型在浮窗之前加载 | `live_subtitle_v2.py:1877–1961` |
| ASR 同步阻塞主线程 | `live_subtitle_v2.py:2252–2445`（2331） |
| 翻译已并行、显示未完全并行 | `live_subtitle_v2.py:2286, 2339–2433` |
| overlay 轮询/保存频率 | `overlay_qt.py:284–291, 420, 866, 1022, 1240` |
| 远端 ASR 无连接复用 | `remote_asr.py:177–185` |
| 预翻译逐块 transcribe | `pretranslate.py:277–305, 483–520` |
| faster-whisper 1.2.1 有批推理/local_files_only | 本机 `inspect.signature` 实测 |
| 重采样混叠 −4.4 dB | `tools/_bench_resample.py` 输出 |
| `babel_admin/` 未跟踪 | `git ls-files babel_admin` 为空 |
| 环境 FAIL 明细 | `python tools/doctor.py` |

---

## 14. 已实施（分支 opt/2026-10-18，2026-10-18）

### 14.1 环境（§1）

已装进系统 Python：
vidia-cublas-cu12 12.9.2.10、
vidia-cuda-nvrtc-cu12 12.9.86、

vidia-cudnn-cu12 9.27.0.42、winsdk 1.0.0b10、yt-dlp 2026.8.19。
python tools/doctor.py 现在 **退出码 0**（仅 WARN 管理端端口未监听，属正常）。
gpu_available() → True。

### 14.2 启动并行化（§2）

- 浮窗/采集/分段器/SRT/停止开关全部提前到模型加载之前；模型在后台线程
  加载，主线程只 pump 浮窗，浮窗先显示"正在加载识别模型…"。
- WhisperModel(..., local_files_only=True)（缓存命中时跳过 HF 元数据联网），
  pretranslate.load_asr 同改。
- 实测（同机、同解释器、旧版快照 vs 新版）：

  | 指标 | 旧版 | 新版 |
  |---|---|---|
  | 浮窗出现 | 7.0s | **0.2–0.3s** |
  | 模型就绪（ASR 可用） | 7.0s | **5.2s** |
  | 管理端 ready 检测 | 42s（管道 read 阻塞） | **6s**（改 read1 后） |

### 14.3 ASR 移出主线程（§3）

- 	ranscribe_batch 丢到单 worker；主循环只做 ov.pump() / 收音频 /
  排空译文，cap.read 超时 0.25s→0.05s，译文一回来就上屏。
- 日志改为"本批 N 段 / 累计 M 段"，不再用逐批"已显示 0 段"误导。
- 停止开关每轮检查，管理端 stop 实测 0.2s 退出。

### 14.4 顺带修掉的真实故障

1. **Qt→torch DLL 顺序**：先 import PyQt5 再 import torch，c10.dll
   初始化失败（WinError 1114），ctranslate2 import 时连带崩。新增
   
ative_preload.py，在 Qt 之前 ctypes 预载 c10.dll。
2. **管理端日志迟到 42 秒**：serve.py 子进程管道 ead(4096) 会攒满
   4KB 才返回；改 ead1(4096)（watch_player.py 同处也改）。
3. **设备偏好被临时故障写死**：CUDA 缺失那轮把 CPU 存成偏好，之后一直
   CPU。现在只在选到 GPU / 确无 GPU / 用户显式选 CPU 时保存。
4. **ctranslate2 CUDA 能力合并后仍走 Vulkan 复测**：合并设备时把
   backend 标成 cuda，省掉每次启动 1.6s 的 gpu_available() 真实前向。

### 14.5 验证记录

| 测试 | 结果 |
|---|---|
| python live_subtitle_v2.py --selftest | 174 通过 / 0 失败 |
| python tools/test_borrowed.py | 33 通过 / 0 失败 |
| python tools/test_engine_control.py | 20 通过 / 0 失败（起 6s / 停 0.2s） |
| python live_subtitle_v2.py --no-overlay 播放 samples/loopback_test.wav | SRT 产出 "Hello, this is a test / 你好，这是一次测试。" |
| python tools/doctor.py | 退出码 0，GPU 可用，6 个模型缓存 5.3GB |

---

## 15. 追加修复：点播/缓存模块实时性（2026-10-18）

用户反馈："视频缓存模块没有以前好用了，特别是没有解读缓存之后的实时性反应"。

### 15.1 根因（两条，叠在一起）

1. **启动器挑错解释器**：watch.bat/cache.bat 只探测
   aster_whisper + PyQt5（cache.bat 甚至只查 faster_whisper），会选中
   缺 winsdk / yt_dlp / v 的 Python。后果：
   - watch：SMTC 不可用 → 没有播放位置 → 缓存匹配上了字幕也不跟；
   - cache/auto：网址反查/下载失败 → 预翻译启动即挂，日志只剩
     预翻译失败：来源: <url>（watch.log 里刷了几十分钟）。
2. **SMTC 会话状态拿对象身份当键**：winsdk 的
   GlobalSystemMediaTransportControlsSession 每次 get_sessions() 都是
   **新的 Python 包装**（本机实测 [i] is b[i] 为 False）。旧
   smtc_tracker 用 id(s) 存标题/位置、用 s is self._session 做迟滞：
   - _titles[id]、_pos_seen[id] 永不命中 → 标题不稳定、"时间线在前进"恒 False；
   - 迟滞永远走"现任消失"分支 → 没有迟滞，多标签页时每拍都可能换会话；
   后果：字幕在几部片之间来回跳、缓存反复重载/预翻译被取消。

### 15.2 修复

| 文件 | 改动 |
|---|---|
| smtc_tracker.py | 状态键改为稳定键 (AUMID, 归一化标题)；迟滞切换真正生效；补上 playback_rate 外推；会话切换写日志 |
| watch_player.py | tracker 传 log=_log；--auto 下标题漂移可追溯 |
| utopipe.py | "预翻译失败：…" 结论行不再当成进度写 running（避免"预翻译中 52:35｜预翻译失败…"假进度） |
| watch.bat | 探针补 winsdk/yt_dlp/av/numpy；显式优先 %LOCALAPPDATA%\...\Python312；只给选项（如 --no-pretranslate）也进常驻模式 |
| cache.bat | 探针补 v/yt_dlp/numpy（原来只查 faster_whisper）；同样优先 Python312 |
| ench/_verify_hysteresis.py | 重写为**驱动真实 _tick** 的测试（旧版抄逻辑+FakeSession 身份稳定，正好测不出这个 bug），新增缓存标题匹配 |

### 15.3 验证

| 项 | 结果 |
|---|---|
| python bench/_verify_hysteresis.py | 16 通过 / 0 失败（含缓存标题匹配 12/12） |
| watch.bat --no-pretranslate 10s 实测 | 标题零漂移，只切换 1 次 |
| pretranslate.py <B站已缓存 URL> | 缓存命中 0.5s 返回 |
| python tools/doctor.py | PASS，系统 Python 全依赖齐（av 19.0.1 / winsdk / yt-dlp） |

---

## 17. 模块边界纠正 + 缓存模块自己的"实时性"（2026-10-18）

用户指出：**回环（任意系统音频）与缓存（点播视频实时翻译）是两个独立模块**，
我上一轮把回环引擎塞进 watch 的实时兜底是错的。已 git revert e97c27d
（提交 df1a391）撤销，两条路线恢复互不调用：

- 回环：start.bat → live_subtitle_v2.py（WASAPI 回环采集 → 实时 ASR/翻译 → 浮窗/SRT）
- 缓存：watch.bat → watch_player.py（SMTC 位置 + pre_cache 里的预翻译 srt）
  → pretranslate.py（整片预翻译，子进程）

### 17.1 缓存模块自身的实时性修复（不借回环）

1. **半截缓存自动续跑**（watch_player.py）：命中缓存但 meta.complete
   不为真、或最后一条 cue 离视频时长还差 5s 以上时，先显示已有部分，
   同时后台 ensure_async 续跑 pretranslate（断点续跑）；tick 的热重载
   每长一截就追加，浮窗提示"续译中：已译到 X 分钟 / 当前 Y 分钟"。
   实测 15:48：4 条 → 10 条，日志出现 字幕增量：8 条（覆盖到 0.8 分钟），
   覆盖到当前播放位置后自动切回按位置精确同步。
2. **常驻浮窗不再自己退出**（watch_player.py）：Qt 默认
   quitOnLastWindowClosed，浮窗反复 clear/flash 时销毁临时 QLabel
   会触发 lastWindowClosed → watch 启动 1 秒就退出（实测日志）。
   现在 setQuitOnLastWindowClosed(False)，Esc 显式 pp.quit()。
   单文件模式（watch_player.py 字幕.srt）同样处理。
3. **watch_overlay 不再预载 torch DLL**：watch 进程本身不 import torch
   （预翻译在子进程），这次预载只会多一次 DLL 加载；移除后模块边界更干净。
4. 此前已有、保留的修复：启动器依赖探针（winsdk/yt_dlp/av）、SMTC 稳定
   会话键、管理端管道 read1、autopipe 失败行不再写 running。

### 17.2 验证

| 项 | 结果 |
|---|---|
| ench/_verify_hysteresis.py | 16 通过 / 0 失败 |
| watch_player.py --auto（半截缓存） | 存活 >90s；4→10 条增量；状态行正常 |
| watch_player.py 字幕.srt --clock（单文件） | 存活，位置源 AutoClock 正常 |
| watch.bat --no-pretranslate | 标题零漂移（10s） |

---

## 18. 本轮交付：改名 / 调研 / 质量度量 / 消费级桌面（2026-10-18 下午）

### 18.1 模块改名（UI/注册表/文档/测试）

- 「实时回环系统」→ **直播翻译**，「视频缓存」→ **视频翻译**；
  代码键 loopback / cache 保持不变（避免全链路回归）。
- 改到的地方：abel_admin/index.html（导航/模式/统计/来源标签/tooltip）、
  abel_admin/serve.py 启动横幅、model_registry.py/.json 的 label、
  README、	ools/check_admin_html.py 与 	ools/e2e_admin_browser.py 的期望文案。
- 后台新增一行概念说明：**本地翻译 = 翻译引擎在本机跑；视频翻译缓存
  （pre_cache）= 字幕产物**，两者不同维度、不是二选一。

### 18.2 全网调研

docs/RESEARCH_20261018_streaming_translation.md：31 条来源（24 条逐页核验），
覆盖流式 ASR / 同传 / 字幕工程 / 消费级 UX；四条有代码位置支撑的硬发现：
① llm_translate.py 没接 Qwen-MT 术语字段；② live.srt 用固定 hold=6s、
无 CPS/行数约束；③ Whisper 约 1% 整句幻觉（Careless Whisper）；
④ faster-whisper 批推理官方基准 13min 63s→17s（8GB 可跑）。
AlignAtt/SimulStreaming 需 cross-attention 且推荐 ≥10GB 显存 → 只列 POC，不直接换。

### 18.3 质量度量与路线图

- 直播引擎新增 work/metrics_live.jsonl（每批 asr_s/audio_s/shown/lang/gate +
  启动 ready_s），	ools/quality_report.py 出 P50/P90/吞吐。
  基线：启动 4.4s、识别 P50 0.61s / P90 0.93s、吞吐 4.25×。
- docs/QUALITY_ROADMAP.md：消费级验收门槛（8h 零崩溃、首字 ≤2s、预翻译
  ≥8×、WER −10% 等）、度量体系、P0/P1/P2 清单、稳定性测试矩阵；
  已把调研的 P0（术语表/字幕规整/幻觉闸门）与 P1（ASR-MT overlap、批推理、
  时间轴规整）回填。

### 18.4 消费级桌面

- pp.py：无控制台安全日志（%LOCALAPPDATA%\BabelPlan\app.log）、命名互斥量
  单实例、子进程统一 CREATE_NO_WINDOW、关窗收托盘、退出停自己拉起的引擎。
- desktop/BabelPlan.vbs（wscript→pythonw，整链无控制台）、
  desktop/babel.ico、desktop/create_shortcut.ps1；
  uild/babel.spec + uild/build_exe.ps1（PyInstaller one-folder/windowed）。
- 管理端启动引擎改为隐藏 + work/engine_stop.flag 哨兵优雅停止
  （无控制台时 SIGBREAK 送不进去）；autopipe/watch_player/gpu/local_mt/
  dlls 的子进程也统一隐藏控制台窗口。

### 18.5 验证

| 项 | 结果 |
|---|---|
| check_admin_html.py | 28/0（新标签 + JS 语法） |
| model_registry.py check | 41/0 + IO 3/0 |
| 	est_engine_control.py | 20/0（隐藏引擎启动、停止 0.2s） |
| live_subtitle_v2.py --selftest | 174/0 |
| ench/_verify_hysteresis.py | 16/0 |
| doctor.py | 退出码 0 |
| 消费级启动（wscript→pythonw） | 2s API ready、无 conhost 子进程、单实例第二进程退出、日志正常 |
| metrics 基线 | 启动 4.4s、ASR P50 0.61s / P90 0.93s、4.25× |

---

## 19. 质量/实时性落地：Qwen-MT 术语 + SRT 真实时间轴 + 静音提前发批（2026-10-18 第二轮）

### 19.1 P0-1：Qwen-MT 官方 translation_options（术语注入）

- 依据官方文档（阿里云 Model Studio，2026-10-11）：OpenAI 兼容端点把
  	ranslation_options 顶层放进请求体；messages 只能一条 user，content 就是
  待译文本；字段 source_lang/target_lang/terms[{source,target}]/domains。
- llm_translate.py 实现：set_terms()/set_domains()、qwen-mt 走
  「纯文本 + translation_options」、服务端 400/422 时自动降级回原 prompt
  风格（只降一次并打印日志），status() 显示术语数与降级状态。
- 接线：直播翻译与视频翻译的 --mt-terms 都挂到远端 MT（原来只喂本地 HY-MT）。
- **实测 A/B**（	ools/check_qwen_terms.py，真实 API）：
  - 无术语：up主 → "UP主"；带术语 up主=创作者：→ "创作者"，Bilibili→哔哩哔哩；
  - 术语命中 2/2，	ranslation_options 被接受 4/4，无降级、4 次调用 0 失败。

### 19.2 P0-2：SRT 时间轴真实时长 + 防重叠

- 发现翻译单元用 _ph_seg = np.zeros(0.5s) 占位，SrtWriter 又固定 hold=6s：
  结果每条字幕实际只停 1 秒（0.5*1.15+0.35 被下限夹到 1.0），且连续说话时
  SRT 段大量重叠（很多播放器只显示第一条）。
- 修复：
  1. 翻译单元携带真实音频时长 mt_unit_durs，提交时用等长占位段让
     len(seg)/16000 就是真实秒数；
  2. SrtWriter.add(en, zh, dur) → srt_hold_for = clamp(dur*1.15+0.35, 1, 6)；
  3. 新条把上一条结束截到新条开始（至少 0.4s），SRT 不再重叠。
- 实测同一段样本：spans [2.455, 1.182, 1.500, 4.260]，边界首尾相接、无重叠。

### 19.3 P0-2'：静音句尾提前发批（实时性）

- 新增 --flush-min 0.6（默认，0=关闭）：Segmenter 吐出自然句尾（长度
  < max_seg，即静音切出的段）且已够 flush_min 秒，就跳过 max_batch/max_wait
  立即送 ASR。
- 指标新增 lush 字段；样本 12 批分布：max_batch=6, natural=2（2 批由
  静音提前触发），ASR P50 0.571s / P90 0.909s / 吞吐 4.4×。

### 19.4 指标与报告

- metrics_live.jsonl 每批新增：lush、ejected_hallu/lowq/remote
  （累计拒段），	ools/quality_report.py 打出发批原因分布与三类拒段。
- 调研报告 §2 增加「复核更正与落地状态」：澄清幻觉闸门/ASR-MT 并行
  **本仓库已实现**（原调研判断有误），并标注 P0-1 已实现、P0-2 部分实现。

### 19.5 验证

| 项 | 结果 |
|---|---|
| ench/_verify_quality_gates.py（新） | 25 通过 / 0 失败 |
| 	ools/check_qwen_terms.py（真实 API A/B） | 术语 2/2、选项接受 4/4 |
| live_subtitle_v2.py --selftest | 174 通过 / 0 失败 |
| ench/_verify_hysteresis.py | 16 通过 / 0 失败 |
| 	ools/check_registry_wiring.py | 20 通过 / 0 失败（新参数 39 个） |
| 	ools/check_admin_html.py | 28 通过 / 0 失败 |
| 	ools/doctor.py | 退出码 0 |
| 直播 E2E 样本 | SRT 4 条、spans [2.455,1.182,1.5,4.26]、无重叠；指标 natural=2 |

---

## 20. 视频翻译批推理（可选）+ 消费级首页（2026-10-18 第三轮）

### 20.1 视频翻译 --asr-batch（默认 0=关，长片提速）

- 实测 ench/_bench_batched_asr.py（large-v3，真实 120s 音频）：
  | 方案 | 首段产出 | 总耗时 | 段数 | 字符 | 文本相似度(词集) |
  |---|---|---|---|---|---|
  | 顺序识别（现状） | 3.36s | 12.18s | 43 | 513 | — |
  | 批推理 batch=8 | 4.24s | 4.24s | 4 | 461 | 0.83 |
  总耗时 **2.9×** 加速；代价：batch 的 segment 更粗（cue 19→11），
  所以**默认关闭**，只在 --asr-batch 8 且块 ≥60s 时启用；首块仍走流式，
  保住"第一屏"延迟。
- 接线：pretranslate.py 新增 	ranscribe_batched()，块循环里长块走
  uild_cues(batched_segments)，短块/首块仍用 iter_chunk_cues（流式）。
  集成实测：386s 音频（5 块）总用时 12s（识别+翻译），批推理路径实际跑到。
- 后续要做的（下一轮）：批推的 cue 更粗，需要在 uild_cues 前用
  word 时间戳 + 静音间隙重新切句（或做标点恢复），再把默认值打开。

### 20.2 消费级首页（后台去"丑"第一步）

- 左侧新增「总览 → 首页」，默认打开首页：两个大卡片
  「开始直播翻译」「打开视频翻译」，模型/浮窗/记录/设置仍在左侧导航；
  首页时自动隐藏工具栏。
- 实现：abel_admin/index.html 新增 iew-home、VIEW_TITLE.home、
  data-go 按钮绑定、默认 setView("home")、首页隐藏 .toolbar。
- 校验：	ools/check_admin_html.py 28/0（结构 + 两段 JS 语法）。

### 20.3 本轮验证

| 项 | 结果 |
|---|---|
| ench/_bench_batched_asr.py（新） | 120s：12.18s→4.24s（2.9×），首段 3.36→4.24s |
| pretranslate.py --asr-batch 8 集成 | 386s 音频 5 块，12s 完成，批推理路径生效 |
| ench/_verify_quality_gates.py | 25/0（回归） |
| 	ools/check_admin_html.py | 28/0（首页新增后） |
| pretranslate.py/llm_translate/live selftest | 编译通过；selftest 174/0（上一轮） |


---

## 21. 稳定性验证（看门狗）+ 一键安装/卸载（2026-10-18 第四轮）

### 21.1 看门狗：强杀引擎 → 自动重启

- 新增 `tools/test_watchdog.py`：直接驱动 `babel_admin.serve` 的
  ENGINE/看门狗线程，启动 → `proc.kill()` 强杀 → 等 pid 变化 → 等 ready
  → 主动停止。
- 实测：**7 项通过 / 0 失败**；强杀后看门狗（3s 轮询、5 分钟 3 次上限）
  拉起新进程并回到 ready，主动停止干净退出。
- 意义：管理端/桌面壳只要服务活着，引擎崩溃不再"整场没字幕"，这是
  消费级稳定性的底线。

### 21.2 一键安装 / 卸载（`desktop/install.ps1`）

- 安装：建桌面/开始菜单快捷方式（复用 create_shortcut.ps1）、写
  `%LOCALAPPDATA%\BabelPlan\install.json`（记录项目路径/时间/自启状态）；
  `-Autostart` 可选写 HKCU Run（`wscript.exe BabelPlan.vbs --tray`，用户级、
  无管理员、无黑窗）。
- 卸载：删快捷方式、删 Run 项、删 install.json；`-RemoveLogs` 才删日志。
- `-DryRun` 支持先看动作；本次 dry-run 验证输出正确（安装 3 步/卸载 3 步）。
- 注意：PowerShell 5.1 读 UTF-8 无 BOM 的中文脚本会把中文 GBK 化，可能
  吃掉后续引号/括号 —— `install.ps1` 已存为 **UTF-8 with BOM**（与
  create_shortcut.ps1 一致）。

### 21.3 验证

| 项 | 结果 |
|---|---|
| `tools/test_watchdog.py`（新） | 7/0（强杀→新 pid→ready→干净停止） |
| `desktop/install.ps1 -DryRun` | 安装/卸载动作输出正确（BOM 后解析通过） |
| 质量回归（上轮） | quality gates 25/0、admin 28/0、selftest 174/0 |


---

## 22. 视频批推理默认化 + cue 整形 + soak 冒烟（2026-10-18 第五轮）

### 22.1 批推理默认 `--asr-batch 8`（长块）

- 批推粗 segment 先用**词间静音 gap（≥0.75s）** 重切成近似句子，断句处按
  gap 大小补 `，`/`。`（批解码常丢标点）；仍超长的 cue 用 `_split_long`
  按 `MAX_CUE_DUR/MAX_CUE_CHARS` 整形。
- 120s 真实音频：流式 cues=19（max 10.67s） vs 批推+gap 切 cues=16
  （max 8.18s、带标点）；识别 12.30s→4.27s（2.88×）。
- 386s 长片 A/B：`--asr-batch 0` → 内部 32s / 总 46s / 56 条；
  默认 8 → 内部 **12s** / 总 **26s** / 52 条；各 120s 块批推 1.5–1.9s
  （约 60–80× 实时）。首块（30s）仍流式，首屏不受影响。
- 安全网：批推/整形异常时本块打印原因并**回退流式**，长片不中断。

### 22.2 soak 冒烟（`tools/soak_test.py`）

- 隐藏启动引擎，`winsound` 循环播放样本（系统声音→WASAPI 环回），
  每 15s 采样 RSS/存活/Traceback，结束时写停止哨兵。
- 90s 实测：ready 4s、**31 批**、RSS 846.4→847.4MB（+1.0MB）、
  Traceback 0、exit 0、ASR P50 0.52s → **PASS**。
- 可直接 `--seconds 28800` 跑 8h；加 `--json` 出机器可读报告。

### 22.3 验证

| 项 | 结果 |
|---|---|
| 批推 cue 质量（120s） | 2.88×，cue 19→16，max 10.67→8.18s，补标点 |
| 长片 A/B（386s） | 内部 32→12s、总 46→26s、cue 56→52 |
| `tools/soak_test.py --seconds 90` | PASS（31 批、+1MB、0 Traceback、exit 0） |
| 回归 | quality gates 25/0、admin 28/0、watchdog 7/0 |


---

## 23. 准确性基线（WER/CPS）+ 10 分钟稳定性 soak（2026-10-18 第六轮）

### 23.1 `bench/eval_asr.py`：ASR 准确性与字幕可读性基线

样本 `samples/loopback_test.wav` 是 Windows TTS 合成的已知文本（15 词 × 2 遍），
参照文本已知可算 WER；同时用 `build_cues` 算每条字幕 CPS（字符/秒）。

| 模型 | 模式 | WER | 识别耗时 | cue CPS P50 / P90 | CPS>20 占比 |
|---|---|---|---|---|---|
| medium | 顺序 | **0.000** | 0.9s | 10.14 / 10.53 | 0% |
| medium | 批推 | **0.000** | 0.6s | 8.33 / 9.49 | 0% |
| large-v3 | 顺序 | **0.000** | 1.1s | 10.63 / 11.64 | 0% |
| large-v3 | 批推 | **0.000** | 0.9s | 8.37 / 10.20 | 0% |

- 数字词归一（one↔1）后 WER 才真实：不归一会被格式差异白算 0.333。
- 基线写 `work/asr_eval.json`，改模型/切句后可直接 A/B。
- TTS 合成样本，真实素材 WER 需人工标注集（路线图保留）。

### 23.2 10 分钟 soak（直播引擎）

`python tools/soak_test.py --seconds 600`：

```
ready 4s · 600s 全程 alive · 216 批
RSS 846.4 → 848.0MB（+1.6MB）
Traceback 0 · 优雅退出 exit 0 · ASR P50 0.55s
PASS
```

配合 watchdog（强杀→自动重启）与 `--seconds 28800` 8h 入口，稳定性从
"冒烟"升级到"10 分钟实测"。

### 23.3 验证

| 项 | 结果 |
|---|---|
| `bench/eval_asr.py`（新） | WER 0.000（4 组）；CPS P50 8.3–10.6；>20 占 0% |
| `tools/soak_test.py --seconds 600` | PASS（216 批、+1.6MB、0 Traceback、exit 0） |
| 回归 | quality gates 25/0、wiring 20/0、admin 28/0、watchdog 7/0 |


---

## 24. 真实语音 WER + CPS 方案修正 + 分发打包（2026-10-18 第七轮）

### 24.1 真实语音 WER：`samples/jfk.wav`

从 whisper.cpp 官方仓库取 JFK 11s 真实语音（人工已知原文）：

    "And so my fellow Americans, ask not what your country can do for you,
     ask what you can do for your country."

`bench/eval_asr.py --audio samples/jfk.wav --ref "..."` 实测：

| 模型 | 模式 | WER | cue CPS P50/P90 | CPS>20 |
|---|---|---|---|---|
| medium | 顺序 | **0.000** | 8.12 / 8.82 | 0% |
| medium | 批推 | **0.000** | 8.85 / 9.20 | 0% |
| large-v3 | 顺序 | **0.000** | 8.29 / 9.01 | 0% |
| large-v3 | 批推 | **0.000** | 7.13 / 14.52 | 0% |

加上 TTS 样本（30 词）四组 WER 0.000，准确性与批推理回归都有了真实基线。

### 24.2 CPS 方案修正（重要）

**切开字幕不会降低 CPS** —— 字符数/总时长不变，只是摊到多条上。实测
"切成多条"后每段 CPS 依旧 ~42。正确做法只有：
① 用句间静音间隙延长显示；② 压缩译文。本轮实现 ①：
`pretranslate.fit_cues_cps()`（幂等），在每批落盘前对全局 cues 尝试用
`next.start - 0.02` 的间隙延长；无间隙的密集语音保持原样（不丢文本），
由 `bench/eval_asr.py` 的 CPS 指标暴露。`--cps-max 20`（0=关）。
- 单测：8 项（有间隙→达标、不越下一条、无间隙不诈切、合规不动、0=关）。
- 集成：386s 长片 52 条，SRT 重叠 0。

### 24.3 分发打包 `build/package_release.ps1`

- 显式白名单组装 `build/_release/BabelPlan/`（41 个根文件 + babel_admin/
  desktop/build/docs/tools/bench/samples），排除日志/状态/pre_cache/模型；
  生成 `快速开始.txt` 后压成 `BabelPlan-<日期>.zip`。
- 实测：**71 个文件 / 4.89MB → zip 2.4MB**；无 `*.log/*.srt/*.bak/state` 泄漏；
  `build/_release/` 已进 .gitignore（生成物不提交）。
- 目标机仍需 Python 3.10+（`pip install -r requirements.txt`），模型首次运行下载/复用缓存。

### 24.4 15 分钟 soak（更长时间实测）

`python tools/soak_test.py --seconds 900`：

```
ready 4s · 900s 全程 alive · 325 批 · ASR P50 0.54s
RSS 845.5 → 848.2MB（+2.7MB）
Traceback 0 · 优雅退出 exit 0
PASS
```

比上一轮 10min（216 批 / +1.6MB）更长；RSS 增长仍是个位数 MB 级，
无泄漏迹象。8h 入口保持 `--seconds 28800`。

### 24.5 样本来源

`samples/jfk.wav` 取自 whisper.cpp 官方仓库 samples/（11s JFK 公开演讲，
仅用于本地 WER 回归；原文见 §24.1）。`samples/loopback_test.wav` 与
`e2e_speech.wav` 是仓库原有 TTS 样本。


---

## 25. 修正：后台「视频翻译」页改为整合 watch 模块（2026-10-18）

用户指出：视频翻译这一块不对，它应该整合的是 **watch 模块**。此前后台
`cache` 模式点启动仍然拉起 `live_subtitle_v2.py`（直播引擎），只是把
mode 写成 cache —— 所以视频翻译页没有真正启动 watch（无 SMTC 匹配、
无自动预翻译、无按播放位置显示的浮窗）。

### 25.1 改动

- `babel_admin/serve.py`：`Engine.start(mode)` 按模式选脚本 ——
  `cache` → `watch_player.py --auto`；`loopback` → `live_subtitle_v2.py`
  不变；cache 模式自动忽略只有直播引擎认识的 `--no-srt`。
- watch 停止：`watch_player.py` 新增 `_engine_stop_requested()`，
  auto tick 每 100ms 查 `work/engine_stop.flag`（管理端无控制台，SIGBREAK
  送不进去），收到后关浮窗/关 tracker/关 autocache 正常退出；启动时清残留哨兵。
- 后台 UI：启动按钮/卡片说明/提示随模式切换（视频翻译显示"常驻监听播放器…
  已缓存秒出、没缓存边看边长"），cache 模式隐藏"不写 .srt"；
  loading 判定加入 watch 日志标记（`watch --auto 启动` / `常驻字幕已启动`）。
- 分发包/白名单补 `tools/test_watch_control.py`。

### 25.2 实测

`tools/test_watch_control.py`（新增）——**9 通过 / 0 失败**：

```
启动成功
拉的是 watch_player.py / 带 --auto / 不是 live 引擎
watch 报到 ready
日志有 watch 常驻标记 / 进程活着
停止哨兵生效、进程退出（0.2s）
不残留 pretranslate 子进程
```

实测日志（cache 启动）真实走到：SMTC 检测到 Quark 在播 → 匹配 pre_cache
字幕 → 半截缓存自动续跑 → `位置已对齐 pos=81.75s，开始同步`，
说明视频翻译页现在启动的就是 watch 的完整链路。

回归：`test_engine_control.py` 20/0（直播模式未受影响）、
`check_registry_wiring` 20/0、`check_admin_html` 28/0。


---

## 26. 修复：浮窗控制面板对视频翻译（watch）浮窗全部失效（2026-10-18）

### 26.1 根因

- 直播浮窗（overlay_qt）与视频翻译浮窗（watch_overlay）是**两套文件**：
  - 直播：设置 overlay_qt.json / 状态 overlay_state.json / 命令 control_cmd.json
  - watch：设置 watch_overlay.json / 状态 watch_overlay_state.json / 命令 watch_cmd.json
- `babel_admin/serve.py` 的 `/api/overlay` 只读写直播那一套：
  POST 只写 control_cmd.json，GET 只读 overlay_qt.json/state。
- 现在视频翻译页启动的是 watch，所以面板每一个调控都写进了 watch 根本
  不看的文件 —— 表现就是"全部失效"。

### 26.2 修复

1. POST `/api/overlay`：校验后**同时写** control_cmd.json 与 watch_cmd.json
   （两浮窗协议相同，只是文件隔离）。
2. GET `/api/overlay`：读两套设置/状态，按"在线且最近活跃"的那个返回，
   并附 `target: "loopback"|"watch"`，面板不再拿错一侧的值。
3. `watch_overlay._read_cmd()` 兼容回退：同时看 watch_cmd.json 与
   control_cmd.json，取 `_ts` 最新的命令 —— 即使后台还是旧版本（只写
   control_cmd），重启一次 watch 后调控也能生效。

### 26.3 验证

- `tools/test_overlay_control.py`（新，隔离临时文件 + 本地 HTTP）：
  **10/0** —— POST 两份都写、GET target=watch 且返回 watch 设置、
  坏字段仍 400。
- 真机端到端（不动用户会话）：在 8771 用新 serve 起临时服务，
  POST font_size=33/opacity=0.8 → 正在运行的 watch 在 2s 内应用
  （watch_overlay.json 变成 33/0.8）、GET 返回 target=watch；
  随后已恢复原值（font 24/opacity 0.92），用户 app/watch 进程未重启。
- 注意：当前正在跑的 `app.py` 里还是旧 serve 代码 —— **重启一次 App**
  后 GET/面板显示才是 watch 的真实设置；watch 本身重启后即带兼容回退。

回归：check_admin_html 28/0、wiring 20/0、watch control 9/0。


---

## 27. 新消费级后台界面（console.html，2026-10-18）

用户要求：后台要像 DeepSeek/元宝这类产品的操作界面，而不是原来的密集控制台。

### 27.1 做法

- 新增 `babel_admin/console.html`（单文件、零外部依赖、离线可用），
  路由：`/` 默认新界面，`/admin` 保留经典后台（高级参数/模型/缓存）。
- 设计：深色渐变底 + 玻璃侧边栏，五个一级入口：
  工作台 / 直播翻译 / 视频翻译 / 浮窗 / 设置；
  - 工作台：Hero 主张 + 直播/视频两大卡片（状态、实时预览、一键启动/停止）+
    引擎/浮窗/运行时长三块状态 + 最近字幕；
  - 直播翻译：实时字幕流 + 启动/停止 + 日志；
  - 视频翻译：watch 状态 + 当前字幕预览 + 启动/停止 + watch 日志；
  - 浮窗：字号/不透明度/条数滑杆、排布/对齐、恢复默认/回中/隐藏（同时写
    直播+watch 两套命令）；
  - 设置：预热/看门狗开关、经典后台入口、关于。
- 复用既有 API：`/api/engine`、`/api/live`、`/api/overlay?target=`、
  `/api/admin-config`；为首页两卡片与浮窗页补了 `?target=watch|loopback`
  强制指定（此前只有"最近活跃"自动选择）。

### 27.2 验证

- `tools/check_console_html.py`（新）：**15 项通过 / 0 失败**（五个导航页、
  四个 API、/admin 入口、无外链、内联 JS 语法 node --check）。
- 用 Edge（Playwright channel=msedge）实际渲染 1440×950：
  无 pageerror/console error；首页/视频/浮窗三张截图见 `work/console_*.png`；
  `/` 返回新界面、`/admin` 返回经典后台（HTTP 200）。
- 修复迭代：`[hidden]` 被 `.btn{display:inline-flex}` 覆盖导致"停止"按钮
  一直显示（截图发现）→ 加 `[hidden]{display:none!important}`；未运行时
  首页预览不再展示旧 live.srt 文案。
- 旧界面体检/回归不动：check_admin_html 28/0、wiring 20/0。


---

## 28. 三个问题修复：经典后台缩水 / 停止被看门狗拉起 / 黑色命令行窗口（2026-10-18）

### 28.1 经典后台"很多功能消失"

原因：上一轮给经典后台（index.html）加了「总览 → 首页」并默认落地首页，
还隐藏了工具栏；打开 `/admin` 先看到的是两个大按钮，看起来像功能没了。

修复：移除经典后台的「总览/首页」导航，默认落地改回「翻译字幕工作台」
（`setView("loopback")`）。实测 `/admin`：导航 7 项（直播/视频/记录/模型/
仓库/浮窗/设置）、工具栏可见、翻译链路卡片在、无 JS 报错。

### 28.2 启动后"停止失败"

原因：引擎优雅停止的退出码是 **1**，看门狗把它当崩溃，停止后立刻自动重启
（实测 stop 返回"已停止(exit 1)"，3 秒后新 pid 又在跑）—— 用户看到的就是
"点了停止没反应"。

修复：`Engine.intentional_stop` 标志。管理端 stop 先置 True，看门狗见到
就直接跳过；下一次 start 清掉。实测：停止后 8 秒 `running=false`，不再复活。

### 28.3 启动后跟着黑色命令行窗口

原因：serve 用 `python.exe`（控制台子系统）拉起引擎；即使加了
CREATE_NO_WINDOW，仍会分配 conhost（用户看到黑窗/命令提示符）。

修复：同目录存在 `pythonw.exe` 时改用它启动引擎/watch（GUI 子系统，
从根上不分配控制台）；日志仍走 PIPE，停止仍走 engine_stop.flag 哨兵。
实测：管理端拉起的是 `pythonw.exe`，进程**没有 conhost 子进程**；
watch 模式同样无子进程/无 conhost。

### 28.4 验证

| 项 | 结果 |
|---|---|
| `tools/test_engine_stop.py`（新） | **8/0**：pythonw、无 conhost、停止成功、8s 不被重启 |
| `/admin` 渲染（Edge） | 7 项导航、默认工作台、toolbar 可见、0 JS 错误 |
| 新界面 `/` | check_console_html 15/0（无回归） |
| 旧后台静态 | check_admin_html 28/0 |
| 临时服务 start/stop | start→pythonw，stop→running=false 且不复活 |
