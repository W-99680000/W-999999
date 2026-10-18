# -*- coding: utf-8 -*-
"""babel_admin/serve.py —— 巴别塔计划 · 后台管理端（本地服务）

为什么需要一个服务，而不是直接双击 index.html
=============================================
双击打开（file://）时前端**读不到 model_registry_web.json** ——
浏览器的 file:// 有跨源限制，fetch 本地 json 会被拦。原型阶段可以在
HTML 里内联一份常量顶着，但那份常量会和真注册表漂移，于是界面上
"选了 large-v3、程序跑的还是 medium"，而且查不出来。

所以这里给一个**只监听 127.0.0.1、只用标准库**的小服务：

    GET  /                      后台界面（babel_admin/index.html）
    GET  /model_registry_web.json   模型快照（含实况可用性）
    GET  /api/models            同上的 /api 别名
    GET  /api/state             当前两个模块解析出的模型与参数
    POST /api/select            改模型选择 → 落 model_registry.json
    POST /api/set-mode          批量改某个模式的绑定
    POST /api/export            重算快照（改了注册表 json 之后用）
    GET  /api/check             注册表 + 接线自检的结果
    GET/POST /api/engine        引擎状态/启停
    GET/POST /api/overlay       浮窗设置读取 / 实时调节（写 control_cmd.json）
    GET  /api/live              live.srt 尾部字幕（快速同传页用）
    GET/POST /api/model-repo    模型仓库：搜索/下载/取消/设默认/卸载
    GET/POST /api/hardware      硬件检测（POST 强制重测）
    GET/POST /api/admin-config  后台自身配置（预热 / 看门狗开关）
    GET  /api/startup-stats     最近启动耗时
    POST /api/prewarm           手动触发一次预热

**为什么不做成"界面写状态、程序读状态"的两份文件**：
注册表只有一份（model_registry.json），界面改的就是程序读的那个文件。
两个翻译模块通过 `model_registry.cli_args()` 取默认值
（见 live_subtitle_v2._apply_registry / pretranslate._apply_registry），
所以"界面改了 → 下次启动就是新模型"，中间没有第二处真相。

安全口径（本地单用户工具）
==========================
· 只绑 127.0.0.1，不对外
· POST 只认下面 PATCHABLE 白名单里的字段 —— 不是"通用配置写入器"，
  避免把服务写成一个能被随便改文件的洞
· 模型 id 必须真实存在于 asr/mt 池里，否则 400（挡错别字）
· 目标模型若**本机不可用**，默认拒绝（除非 force=true）——
  防止在界面上一键把自己切到一个跑不起来的模型上

用法
====
    python babel_admin/serve.py              # 打开 http://127.0.0.1:8770
    python babel_admin/serve.py --port 8899
    python babel_admin/serve.py --no-open    # 不起浏览器
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STOP_FILE = os.path.join(ROOT, "work", "engine_stop.flag")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import model_registry as R  # noqa: E402

try:
    import dlls as DLLS      # babel_admin/dlls.py（同目录）
except Exception:            # pragma: no cover - 缺了也不该整站起不来
    DLLS = None

# 模型仓库 / 硬件检测（从 admin_qt.py 合并进来的能力）。
# 缺了不致命：对应 API 返回 503，其余页面照常工作。
try:
    import model_manager as MM
except Exception:
    MM = None
try:
    import hardware_detect as HW
except Exception:
    HW = None

INDEX = os.path.join(HERE, "index.html")
# 消费级新界面；旧后台保留在 /admin（高级参数/模型/缓存）
CONSOLE = os.path.join(HERE, "console.html")

# 浮窗与后台自身的文件（与 admin_qt.py 用的是同一份，单一真相）
OVERLAY_SETTINGS = os.path.join(ROOT, "overlay_qt.json")
OVERLAY_STATE = os.path.join(ROOT, "overlay_state.json")
OVERLAY_CMD = os.path.join(ROOT, "control_cmd.json")
# watch（视频翻译）浮窗是独立一套文件：设置/状态/命令都分开，
# 见 watch_overlay.py 头部说明（两边共用会互相冲掉设置）。
WATCH_SETTINGS = os.path.join(ROOT, "watch_overlay.json")
WATCH_STATE = os.path.join(ROOT, "watch_overlay_state.json")
WATCH_CMD = os.path.join(ROOT, "watch_cmd.json")
ADMIN_CFG = os.path.join(ROOT, "work", "admin_qt.json")
STARTUP_STATS = os.path.join(ROOT, "work", "startup_stats.json")
LIVE_SRT = os.path.join(ROOT, "live.srt")

# 浮窗可调字段白名单（和 PATCHABLE 一个思路：服务不是通用写入器）
_OVERLAY_NUM = {
    "font_size", "ref_height", "original_ratio", "opacity", "outline_width",
    "bg_alpha", "border_width", "max_lines", "hold_seconds",
    "max_lines_per_entry", "width", "max_entry_chars", "line_height",
    "gap_px", "x", "y",
}
_OVERLAY_COLOR = {
    "text_color", "original_text_color", "outline_color", "bg_color",
    "border_color",
}
_OVERLAY_BOOL = {"show_original", "edit_mode", "theme_dark_video",
                 "show_idle_hint"}
_OVERLAY_ENUM = {"layout": {"inline", "stacked", "zh_only"},
                 "text_align": {"center", "left", "right"}}
_OVERLAY_ACTIONS = {"recenter", "toggle", "reset"}


def _read_json_file(p: str, default=None):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def _write_overlay_cmd(changes: dict, path: str = "") -> None:
    """浮窗命令协议（与 admin_qt._write_cmd / control_panel.write_cmd 相同）：
    浮窗每 300ms 轮询命令文件，读到 _ts 变就应用 _changes。

    path 为空时同时写 control_cmd.json（直播浮窗）与 watch_cmd.json
    （视频翻译浮窗）—— 面板不知道用户此刻看的是哪种浮窗，两套都发最稳；
    两个浮窗的协议相同，只是文件隔离。"""
    targets = [path] if path else [OVERLAY_CMD, WATCH_CMD]
    for p in targets:
        cur = _read_json_file(p, {})
        cur["_ts"] = time.time()
        cur["_changes"] = changes
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False)
        os.replace(tmp, p)


def _validate_overlay(changes: dict) -> tuple[bool, str]:
    if not isinstance(changes, dict) or not changes:
        return False, "changes 必须是非空对象"
    for k, v in changes.items():
        if k == "_action":
            if v not in _OVERLAY_ACTIONS:
                return False, f"未知浮窗动作 {v!r}"
        elif k in _OVERLAY_NUM:
            if v is not None and (isinstance(v, bool)
                                  or not isinstance(v, (int, float))):
                return False, f"{k} 必须是数字（收到 {v!r}）"
        elif k in _OVERLAY_COLOR:
            if not (isinstance(v, str)
                    and re.fullmatch(r"#[0-9a-fA-F]{6}", v)):
                return False, f"{k} 必须是 #RRGGBB 颜色（收到 {v!r}）"
        elif k in _OVERLAY_BOOL:
            if not isinstance(v, bool):
                return False, f"{k} 必须是布尔（收到 {v!r}）"
        elif k in _OVERLAY_ENUM:
            if v not in _OVERLAY_ENUM[k]:
                return False, f"{k} 只能是 {sorted(_OVERLAY_ENUM[k])}（收到 {v!r}）"
        else:
            return False, f"字段 {k!r} 不在浮窗白名单里"
    return True, "ok"


_ADMIN_DEFAULTS = {"prewarm": True, "watchdog": True}


def _admin_cfg() -> dict:
    """读后台自身配置。文件不存在/缺字段时回退默认值 ——
    与看门狗/预热调用点的 .get(k, True) 语义保持一致，
    否则前端开关会显示"关"而实际行为是"开"。"""
    d = dict(_ADMIN_DEFAULTS)
    d.update(_read_json_file(ADMIN_CFG, {}))
    return d


def _save_admin_cfg(d: dict) -> None:
    os.makedirs(os.path.dirname(ADMIN_CFG), exist_ok=True)
    tmp = ADMIN_CFG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ADMIN_CFG)


def prewarm() -> str:
    """预读模型文件 + CUDA DLL（逻辑同 admin_qt.prewarm）。
    只读文件进系统缓存，不把模型加载进显存。"""
    done = []
    try:
        if MM:
            d = MM.current_defaults()
            asr = (d.get("asr") or "local:faster-whisper-medium")
            size = asr.replace("local:faster-whisper-", "")
            p = MM.whisper_installed(size)
            if p:
                n = 0
                for root, _dirs, files in os.walk(p):
                    for f in files:
                        fp = os.path.join(root, f)
                        try:
                            with open(fp, "rb") as fh:
                                while fh.read(1 << 22):
                                    pass
                            n += 1
                        except OSError:
                            pass
                done.append(f"模型文件 {n} 个已入缓存")
    except Exception:
        pass
    if DLLS:
        try:
            env = DLLS.env_for_engine()
            for pth in (env.get("PATH") or "").split(os.pathsep):
                if "nvidia" in pth.lower() and os.path.isdir(pth):
                    for f in os.listdir(pth):
                        if f.endswith(".dll"):
                            try:
                                with open(os.path.join(pth, f), "rb") as fh:
                                    fh.read(1 << 20)
                            except OSError:
                                pass
            done.append("CUDA DLL 已预读")
        except Exception:
            pass
    return "；".join(done) if done else "没有可预热的内容"


def _watchdog_loop() -> None:
    """引擎看门狗（从 admin_qt 合并）：意外退出自动重启，5 分钟内最多 3 次。
    下沉到服务层之后，只要后台服务活着就有守护，不再依赖某个窗口开着。"""
    crash: list[float] = []
    gave_up = False
    while True:
        time.sleep(3.0)
        try:
            if not _admin_cfg().get("watchdog", True):
                continue
            if ENGINE.intentional_stop:
                # 用户主动停止：不重启；直到下一次 start() 清标志
                continue
            if ENGINE.alive():
                crash.clear()
                gave_up = False
                continue
            # 只有"被我们启动过、又意外死掉"才重启；手动停止（exit 0）不管
            if ENGINE.started_at == 0 or ENGINE.exit_code in (0, None):
                continue
            if ENGINE.stopped_at and time.time() - ENGINE.stopped_at > 600:
                continue
            now = time.time()
            crash = [t for t in crash if now - t < 300]
            if len(crash) >= 3:
                if not gave_up:
                    gave_up = True
                    ENGINE._push("· 看门狗：已连续崩溃 3 次，停止自动重启，请查看日志")
                continue
            crash.append(now)
            ENGINE._push(f"· 看门狗：引擎退出（exit {ENGINE.exit_code}），"
                         f"自动重启（第 {len(crash)} 次）")
            ENGINE.start(mode=ENGINE.mode or "loopback")
        except Exception:
            pass


_BG_STARTED = False


def start_background(prewarm_on_open: bool = True) -> None:
    """启动看门狗线程，并按配置做一次预热。幂等。
    main() 与桌面壳（app.py）都会调它。"""
    global _BG_STARTED
    if _BG_STARTED:
        return
    _BG_STARTED = True
    threading.Thread(target=_watchdog_loop, daemon=True).start()
    if prewarm_on_open and _admin_cfg().get("prewarm", True):
        def _pw():
            msg = prewarm()
            ENGINE._push(f"· 预热完成：{msg}")
        threading.Thread(target=_pw, daemon=True).start()


def _srt_tail(path: str, max_entries: int = 60) -> list[dict]:
    """解析 live.srt 尾部，给"快速同传"页的双栏字幕用。
    live.srt 是引擎实时覆盖写的，格式为标准 SRT；
    每块第二行是译文、可选第三行是原文（与 overlay 的双语规范一致）。"""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            raw = f.read()
    except OSError:
        return []
    blocks = re.split(r"\r?\n\s*\r?\n", raw.strip())
    out = []
    for b in blocks[-max_entries:]:
        lines = [ln.strip() for ln in b.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        # lines[0]=序号 lines[1]=时间轴 之后是正文
        body = lines[2:] if "-->" in (lines[1] if len(lines) > 1 else "") else lines[1:]
        if not body:
            continue
        ts = ""
        if len(lines) > 1 and "-->" in lines[1]:
            ts = lines[1].split("-->")[0].strip().split(",")[0]
        out.append({"ts": ts,
                    "text": body[0],
                    "original": body[1] if len(body) > 1 else ""})
    return out

#: 允许被界面改动的字段 —— 白名单，不多一条
PATCHABLE = {
    "defaults.asr", "defaults.mt", "defaults.router.allow_offline",
    "modes.loopback.asr", "modes.loopback.mt", "modes.loopback.asr_stream",
    "modes.cache.asr", "modes.cache.mt", "modes.cache.asr_stream",
}

_START = time.time()


# ══════════════════════════════════════════════════════════════════
#  引擎（翻译链路）进程管理
# ══════════════════════════════════════════════════════════════════
# 为什么管理端要管进程：**浮窗是原生窗口，只有链路跑起来才有**。
# 之前 admin.bat 只起这个 web 服务，界面上没有任何"启动"入口 ——
# 于是打开管理端看到的就是"什么都没有"，而链路其实压根没启动。
# 现在：界面上的按钮 → 这里的 start() → 子进程 live_subtitle_v2.py
#       → PyQt5 透明置顶浮窗出现在屏幕上。
#
# 为什么不用 shell 起（`start "" python ...`）：那样拿不到 pid、
# 收不到输出流、也停不掉。管理端必须能"看日志 + 点停止"，
# 否则出问题时用户只能看到一个不动的界面。
class Engine:
    """链路子进程 + 日志环形缓冲。

    日志用**环形缓冲**而不是整个文件：管理端要能立刻显示"卡在哪一步"
    （实测过：GPU 探测那步能卡几十秒，界面上必须看得见），
    但也不能让内存随运行时长无限涨。
    """

    LOG_MAX = 600

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.lines: list[str] = []
        self.lock = threading.Lock()
        self.started_at = 0.0
        self.stopped_at = 0.0
        self.exit_code: int | None = None
        self.mode = "loopback"
        self.argv: list[str] = []
        self.python = ""
        self.python_reason = ""
        # 主动停止（管理端点停止/重启）—— 看门狗见到这个标志不再自动拉起，
        # 否则引擎优雅退出码是 1，会被误判为"崩溃"而立刻重启，用户看到
        # 的现象就是"点停止没反应/停止失败"。
        self.intentional_stop = False
        self._reader: threading.Thread | None = None

    # ── 内部 ────────────────────────────────────────────────────
    def _push(self, line: str) -> None:
        with self.lock:
            self.lines.append(line.rstrip("\r\n"))
            if len(self.lines) > self.LOG_MAX:
                del self.lines[: len(self.lines) - self.LOG_MAX]

    def _pump(self, stream) -> None:
        """按**字节**读子进程输出，遇到换行就落一行。

        为什么不用 `for line in stream` —— 那是**按行阻塞**的：
        子进程如果写了一段不带换行（或只有 \\r 回车刷新，进度条都这样）
        的内容，`readline()` 会一直等换行符，于是管理端日志区**一直是空的**，
        界面上完全看不出它卡在哪、还是根本没启动。
        实测踩到：链路启动时日志迟迟不出现，看着像"点了没反应"。

        按块读 + 自行切行，就能做到"有多少显示多少"。
        """
        buf = b""
        try:
            while True:
                # **read1，不是 read**：`read(4096)` 会一直阻塞到攒满 4KB
                # 或进程退出，日志于是"成批迟到"——实测引擎自身 5 秒就绪，
                # 管理端却 42 秒才看到"转写:"，被误判成"启动慢"。
                # read1 有多少返回多少，配合下面按块切行才是真·实时。
                chunk = stream.read1(4096)
                if not chunk:
                    break
                buf += chunk
                # \r 也要当分隔符：tqdm 之类的进度刷新只用回车
                while True:
                    m = re.search(rb"[\r\n]", buf)
                    if not m:
                        break
                    head, buf = buf[:m.start()], buf[m.end():]
                    # \r\n 要把后面的 \n 也吃掉，避免多出一个空行
                    if m.group(0) == b"\r" and buf[:1] == b"\n":
                        buf = buf[1:]
                    self._push(head.decode("utf-8", "replace"))
                if len(buf) > 8192:
                    # 超长无换行的输出（例如一行几十 KB）：先落出来，
                    # 别把内存和界面都堵住
                    self._push(buf.decode("utf-8", "replace"))
                    buf = b""
            if buf:
                self._push(buf.decode("utf-8", "replace"))
        except Exception:
            pass
        finally:
            try:
                stream.close()
            except Exception:
                pass

    def _watch(self) -> None:
        p = self.proc
        if not p:
            return
        rc = p.wait()
        self.exit_code = rc
        self.stopped_at = time.time()
        self._push(f"—— 进程已退出（exit {rc}）——")

    def _pick_python(self) -> dict:
        if DLLS is None:
            return {"exe": sys.executable, "ok": False,
                    "reason": "dlls.py 不可用，回退当前解释器"}
        try:
            return DLLS.pick()
        except Exception as e:
            return {"exe": sys.executable, "ok": False,
                    "reason": f"解释器挑选失败：{e}"}

    # ── 对外 ────────────────────────────────────────────────────
    def alive(self) -> bool:
        return bool(self.proc and self.proc.poll() is None)

    def status(self) -> dict:
        py = self._pick_python()
        return {
            "running": self.alive(),
            "pid": (self.proc.pid if self.proc else None),
            "mode": self.mode,
            "state": self.mode,
            "uptime": (round(time.time() - self.started_at, 1)
                       if self.alive() and self.started_at else 0),
            "exit_code": self.exit_code,
            "python": self.python or py.get("exe", ""),
            "python_ok": bool(py.get("ok")),
            "python_reason": py.get("reason", ""),
            "argv": list(self.argv),
            "log": list(self.lines)[-120:],
            "gpu_hint": ("" if py.get("ok") else
                         "当前解释器缺 CUDA 库 → 识别会退 CPU/远端，"
                         "启动也会慢；见 babel_admin/dlls.py"),
        }

    def start(self, mode: str = "loopback", extra: list[str] | None = None,
              force: bool = False) -> tuple[bool, str]:
        # **锁只护住状态读写，不护住 Popen** —— 否则起进程那几百毫秒里
        # /api/engine 也会被挡住，界面轮询会看起来"卡了"。
        if self.alive():
            with self.lock:
                pid = self.proc.pid if self.proc else None
            return False, f"已经在跑（pid {pid}）"
        if mode not in R.MODES:
            return False, f"未知模式 {mode!r}"
        py = self._pick_python()
        exe = py.get("exe") or sys.executable
        # 子进程用 pythonw.exe（GUI 子系统）拉起：管理端本身通常无控制台，
        # 但 python.exe 是控制台程序，即使 CREATE_NO_WINDOW 也可能出现
        # "python / 黑色命令提示符"窗口。pythonw 从根上不分配控制台。
        launch_exe = exe
        if os.name == "nt":
            _pw = os.path.join(os.path.dirname(exe), "pythonw.exe")
            if os.path.isfile(_pw):
                launch_exe = _pw
        # **视频翻译（cache）走 watch 模块**：常驻监听 SMTC 播放器，
        # 自动匹配 pre_cache / 反查网址预翻译，浮窗按播放位置显示。
        # 直播翻译（loopback）才走 live_subtitle_v2。
        if mode == "cache":
            script = os.path.join(ROOT, "watch_player.py")
        else:
            script = os.path.join(ROOT, "live_subtitle_v2.py")
        if not os.path.isfile(script):
            return False, f"找不到 {script}"
        # `-u` 必须给：否则子进程输出走块缓冲，管理端看到的日志
        # 会成片延迟 —— 而"卡住了"和"还没输出"看起来一样。
        cmd = [launch_exe, "-X", "utf8", "-u", script]
        if mode == "cache":
            cmd.append("--auto")
        # 注册表默认值已经由 live_subtitle_v2._apply_registry 灌进去了，
        # 所以这里**不再重复传 --model**：传了就变成"界面写死一个模型"，
        # 又会和注册表分叉。--mode 只用来记状态与给界面看。
        _extra = list(extra or [])
        if mode == "cache":
            # 界面上的「不写 .srt」是直播引擎参数；watch 本来就不写，丢掉
            _extra = [x for x in _extra if x != "--no-srt"]
        cmd += _extra
        env = (DLLS.env_for_engine() if DLLS else dict(os.environ))
        if force:
            env["BABEL_FORCE"] = "1"
        with self.lock:
            self.lines = []
            self.exit_code = None
            self.mode = mode
            self.argv = cmd
            self.python = exe
            self.python_reason = py.get("reason", "")
            self.intentional_stop = False
        self._push(f"$ {' '.join(cmd)}")
        self._push(f"· 解释器：{exe}")
        self._push(f"· 选择理由：{self.python_reason}")
        flags = 0
        if os.name == "nt":
            # 新进程组：停止时能送 CTRL_BREAK（优雅退出，浮窗会自己关），
            # 而不是只能 TerminateProcess（硬杀 → 浮窗留残影）
            flags = (getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                 | getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self._push("· 正在拉起进程…")
        try:
            os.remove(STOP_FILE)
        except OSError:
            pass
        try:
            proc = subprocess.Popen(
                cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, creationflags=flags)
        except Exception as e:
            self._push(f"· 启动失败：{type(e).__name__} {e}")
            return False, f"启动失败：{type(e).__name__} {e}"
        with self.lock:
            self.proc = proc
            self.started_at = time.time()
            self.stopped_at = 0.0
        self._push(f"· 进程已拉起 pid={proc.pid}")
        self._reader = threading.Thread(target=self._pump,
                                       args=(proc.stdout,), daemon=True)
        self._reader.start()
        threading.Thread(target=self._watch, daemon=True).start()
        return True, f"已启动（pid {proc.pid}，模式 {mode}）"

    def stop(self, hard: bool = False, wait: float = 12.0) -> tuple[bool, str]:
        with self.lock:
            p = self.proc
            if not p or p.poll() is not None:
                return False, "没有在跑"
            # 先标记主动停止：看门狗随后看到进程退出不能再拉起
            self.intentional_stop = True
        if not hard:
            try:
                os.makedirs(os.path.dirname(STOP_FILE), exist_ok=True)
                with open(STOP_FILE, "w", encoding="utf-8") as _sf:
                    _sf.write(str(time.time()))
                self._push("· 已写停止哨兵，等引擎自己收尾")
            except Exception as _e:
                self._push(f"· 停止哨兵写入失败：{_e}")
        if os.name == "nt" and not hard:
            # 先优雅：CTRL_BREAK 会进到 _install_kill_switch 里 set(stop)，
            # 主循环走正常退出（关浮窗、收线程）。
            # **注意 SIGBREAK 在 Windows 上 `__import__("signal").SIGBREAK`
            # 取不到（只在 signal 模块通过 WinAPI 暴露，getattr 拿不到）**，
            # 实测报 `ValueError: Unsupported signal: 21`，于是每次都退化
            # 成硬杀 —— 浮窗会留一帧残影。所以这里显式从 signal 模块取。
            try:
                import signal as _sig
                _sb = getattr(_sig, "SIGBREAK", None)
                if _sb is None:
                    raise ValueError("本平台没有 SIGBREAK")
                p.send_signal(_sb)
                self._push("· 已发送停止信号（等它自己收尾…）")
            except Exception as e:
                self._push(f"· 停止信号发送失败（{type(e).__name__}: {e}）"
                           f"→ 改用 terminate")
                try:
                    p.terminate()
                except Exception as e2:
                    self._push(f"· terminate 也失败：{e2}")
        else:
            try:
                p.terminate()
                self._push("· 已发送 terminate")
            except Exception as e:
                self._push(f"· terminate 失败：{e}")
        t0 = time.time()
        while time.time() - t0 < wait:
            if p.poll() is not None:
                try:
                    os.remove(STOP_FILE)
                except OSError:
                    pass
                return True, f"已停止（exit {p.returncode}）"
            time.sleep(0.2)
        # 超时 → 硬杀。**必须硬杀**：否则浮窗进程留着，用户看到"关不掉"
        try:
            p.kill()
            p.wait(timeout=8)
            try:
                os.remove(STOP_FILE)
            except OSError:
                pass
            self._push("· 等待超时，已强制结束")
            return True, "已强制结束（优雅退出超时）"
        except Exception as e:
            return False, f"停止失败：{type(e).__name__} {e}"

    def wait_ready(self, timeout: float = 90.0) -> dict:
        """等引擎"起来"（浮窗出现或日志里出现关键字）。

        为什么要等：模型加载要几秒到几十秒，界面点完"启动"立刻返回
        "启动中"是对的，但**不能就此不管** —— 用户需要知道
        "还在加载" / "已经好了" / "退出了（附日志）"。
        """
        t0 = time.time()
        # 判定"起来了"的标记。**"转写:" 一定要在里面** —— 它是模型加载完、
        # 开始干活的那一行。注意：浮窗现在**在模型加载之前**就出现了
        # （启动并行化），所以 "浮窗位置/采集:" 不能再当就绪标记，否则
        # 界面会在模型还没加载完时就显示"就绪"。只认 "转写:"。
        marks = ("转写:", "listening", "watch --auto 启动",
                 "常驻字幕已启动", "watch 启动")
        while time.time() - t0 < timeout:
            if not self.alive():
                return {"state": "exited", "exit_code": self.exit_code,
                        "seconds": round(time.time() - t0, 1)}
            with self.lock:
                tail = "\n".join(self.lines[-200:])
            if any(m in tail for m in marks):
                return {"state": "ready", "seconds": round(time.time() - t0, 1)}
            time.sleep(0.5)
        return {"state": "loading", "seconds": round(time.time() - t0, 1)}


ENGINE = Engine()


def _read_index() -> bytes:
    try:
        with open(INDEX, "rb") as f:
            return f.read()
    except OSError:
        return ("<h1>找不到 babel_admin/index.html</h1>"
                "<p>界面文件缺失，请检查目录。</p>").encode("utf-8")


def _read_console() -> bytes:
    try:
        with open(CONSOLE, "rb") as f:
            return f.read()
    except OSError:
        return _read_index()          # 新界面缺失时退回经典后台


def _snapshot(data: dict | None = None) -> dict:
    """界面要的全部数据，**一次给完**（省往返，也避免界面拿到半更新状态）。

    统一走 `R.probe(src=R.PATH)`：注册表路径以模块当前值为准，
    这样测试把 `R.PATH` 指到临时副本时，读、校验、写三处用的是同一个文件。
    """
    d = data if data is not None else R.probe(src=R.PATH)
    w = R.web_payload(d)
    w["registry_path"] = R.PATH
    w["web_path"] = R.WEB_PATH
    w["patchable"] = sorted(PATCHABLE)
    w["pending_hardcoded"] = R.registry_pending_defaults()
    w["wired"] = R.registry_wired_defaults()
    w["modes_meta"] = {k: {"label": v, "args": R.MODE_ARGS.get(k, {})}
                       for k, v in R.MODES.items()}
    w["ts"] = time.time()
    return w


def _set_nested(obj: dict, dotted: str, value) -> None:
    parts = dotted.split(".")
    cur = obj
    for p in parts[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[p] = nxt
        cur = nxt
    cur[parts[-1]] = value


def _validate_value(dotted: str, value, data: dict, force: bool) -> tuple[bool, str]:
    """能不能这么改。返回 (ok, 原因)。"""
    if dotted not in PATCHABLE:
        return False, f"字段 {dotted!r} 不在可改白名单里（服务不是通用写入器）"
    if value is None:
        return True, "清空为该模式的默认跟随（null）"
    if not isinstance(value, str):
        return False, f"{dotted} 只接受字符串或 null，收到 {type(value).__name__}"
    m = R.get(value, data)
    if m is None:
        ids = [x["id"] for x in (data.get("asr") or []) + (data.get("mt") or [])]
        return False, f"注册表里没有模型 {value!r}；可选：{', '.join(sorted(ids))}"
    if m.get("available") is False and not force:
        return (False, f"{value} 本机当前不可用：{m.get('unavailable_reason') or '未知原因'}"
                       "（确实要用就带 force=true）")
    return True, "ok"


class Handler(BaseHTTPRequestHandler):
    server_version = "BabelAdmin/1.0"
    protocol_version = "HTTP/1.1"

    # ── 工具 ───────────────────────────────────────────────
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # 快照必须每次现算：缓存住就会出现"界面显示旧模型"
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n <= 0:
            return {}
        raw = self.rfile.read(n)
        try:
            obj = json.loads(raw.decode("utf-8"))
        except Exception as e:
            raise ValueError(f"请求体不是合法 JSON：{e}") from None
        return obj if isinstance(obj, dict) else {"value": obj}

    def log_message(self, fmt, *args):        # 默认会往 stderr 刷噪音
        pass

    # ── GET ────────────────────────────────────────────────
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html", "/console", "/console/"):
            return self._send(200, _read_console(), "text/html; charset=utf-8")
        if path in ("/admin", "/admin/", "/classic", "/classic/"):
            return self._send(200, _read_index(), "text/html; charset=utf-8")
        if path in ("/api/models", "/model_registry_web.json"):
            return self._json(_snapshot())
        if path == "/api/state":
            return self._json({"ts": time.time(),
                               "resolved": R.all_resolved(),
                               "defaults": R.load(R.PATH).get("defaults"),
                               "pending_hardcoded": R.registry_pending_defaults()})
        if path == "/api/engine":
            # GET：状态 + 最近日志（界面每 1~2 秒轮询它）
            st = ENGINE.status()
            st["registry_pending"] = R.registry_pending_defaults()
            return self._json(st)
        if path == "/api/check":
            import io
            import contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = R.check()
            return self._json({"ok": rc == 0, "log": buf.getvalue()}, 200 if rc == 0 else 500)
        if path == "/api/overlay":
            # 浮窗设置 + 实时状态（设置页的浮窗控制台用它）。
            # 直播（overlay_qt）与视频翻译（watch_overlay）各有一套文件；
            # 取"最近活跃/在线"的那个，附带 target 告诉前端调的是哪边。
            live_s = _read_json_file(OVERLAY_SETTINGS, {})
            live_st = _read_json_file(OVERLAY_STATE, {})
            watch_s = _read_json_file(WATCH_SETTINGS, {})
            watch_st = _read_json_file(WATCH_STATE, {})
            now = time.time()

            def _online(settings, state):
                ts = settings.get("_app_ts") or state.get("ts") or 0
                return isinstance(ts, (int, float)) and now - ts < 30

            live_on = _online(live_s, live_st)
            watch_on = _online(watch_s, watch_st)
            live_ts = live_s.get("_app_ts") or 0
            watch_ts = watch_s.get("_app_ts") or watch_st.get("ts") or 0
            # 新界面需要分别看两套浮窗（首页两张卡片 / 浮窗页目标切换）
            _q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            _want = (_q.get("target") or [""])[0]
            if _want == "watch":
                return self._json({"settings": watch_s, "state": watch_st,
                                   "online": watch_on, "target": "watch"})
            if _want == "loopback":
                return self._json({"settings": live_s, "state": live_st,
                                   "online": live_on, "target": "loopback"})
            use_watch = bool(watch_on and (not live_on or watch_ts >= live_ts))
            if use_watch:
                return self._json({"settings": watch_s, "state": watch_st,
                                   "online": watch_on,
                                   "target": "watch"})
            return self._json({"settings": live_s, "state": live_st,
                               "online": live_on,
                               "target": "loopback"})
        if path == "/api/live":
            # 快速同传页的实时字幕（解析 live.srt 尾部）
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            try:
                n = min(200, max(1, int(q.get("n", ["60"])[0])))
            except ValueError:
                n = 60
            entries = _srt_tail(LIVE_SRT, n)
            mt = None
            try:
                mt = os.path.getmtime(LIVE_SRT)
            except OSError:
                pass
            return self._json({"entries": entries, "mtime": mt,
                               "path": LIVE_SRT})
        if path == "/api/model-repo":
            # 模型仓库：搜索 + 推荐标注 + 下载进度（合并自 admin_qt 的模型页）
            if MM is None:
                return self._json({"ok": False,
                                   "error": "model_manager 不可用"}, 503)
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            query = (q.get("q") or [""])[0]
            group = (q.get("group") or [""])[0]
            hw = None
            if HW:
                try:
                    hw = HW.detect()
                except Exception:
                    hw = None
            items = MM.annotate_recommend(MM.search(query, group), hw)
            return self._json({"ok": True, "items": items,
                               "defaults": MM.current_defaults(),
                               "state": MM._load_state(), "hw": hw})
        if path == "/api/hardware":
            if HW is None:
                return self._json({"ok": False,
                                   "error": "hardware_detect 不可用"}, 503)
            return self._json({"ok": True, "report": HW.detect()})
        if path == "/api/admin-config":
            return self._json(_admin_cfg())
        if path == "/api/startup-stats":
            return self._json(_read_json_file(STARTUP_STATS, {}))
        if path == "/favicon.ico":
            # 浏览器/WebView 标签页图标：直接用桌面图标
            _ico = os.path.join(ROOT, "desktop", "babel.ico")
            try:
                with open(_ico, "rb") as _f:
                    return self._send(200, _f.read(), "image/x-icon")
            except OSError:
                return self._send(204, b"", "image/x-icon")
        if path.startswith("/assets/"):
            rel = path[len("/assets/"):].replace("..", "")
            fp = os.path.join(HERE, "assets", rel)
            if os.path.isfile(fp):
                ct = ("text/css; charset=utf-8" if fp.endswith(".css") else
                      "application/javascript; charset=utf-8" if fp.endswith(".js") else
                      "application/octet-stream")
                with open(fp, "rb") as f:
                    return self._send(200, f.read(), ct)
        return self._json({"error": "not found", "path": path}, 404)

    # ── POST ───────────────────────────────────────────────
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            req = self._body()
        except ValueError as e:
            return self._json({"ok": False, "error": str(e)}, 400)

        if path == "/api/export":
            p = R.export_web()
            return self._json({"ok": True, "path": p, "snapshot": _snapshot()})

        if path == "/api/select":
            # { "target": "defaults.mt" | "modes.loopback.asr", "value": "..." | null,
            #   "force": false }
            target = req.get("target")
            if not isinstance(target, str) or not target:
                return self._json({"ok": False, "error": "缺少 target"}, 400)
            force = bool(req.get("force"))
            value = req.get("value")
            data = R.probe(src=R.PATH)            # 用带实况的结构校验与写回
            ok, why = _validate_value(target, value, data, force)
            if not ok:
                return self._json({"ok": False, "error": why, "target": target}, 400)
            _set_nested(data, target, value)
            data["updated"] = time.strftime("%Y-%m-%d")
            R.save(data)
            R.export_web()
            return self._json({"ok": True, "changed": {target: value},
                               "why": why, "snapshot": _snapshot()})

        if path == "/api/set-mode":
            # { "mode": "cache", "asr": "...", "mt": "...", "params": {...},
            #   "force": false }
            mode = req.get("mode")
            if mode not in R.MODES:
                return self._json({"ok": False, "error": f"未知模式 {mode!r}"}, 400)
            data = R.probe(src=R.PATH)
            changes: dict[str, object] = {}
            for slot in ("asr", "mt", "asr_stream"):
                if slot not in req:
                    continue
                v = req[slot]
                target = f"modes.{mode}.{slot}"
                ok, why = _validate_value(target, v, data, bool(req.get("force")))
                if not ok:
                    return self._json({"ok": False, "error": why}, 400)
                _set_nested(data, target, v)
                changes[target] = v
            params = req.get("params")
            if isinstance(params, dict):
                cur = data["modes"][mode].setdefault("params", {})
                for k, v in params.items():
                    if k not in cur:             # 只改已有的键，不新增野键
                        continue
                    # **类型必须和现值一致。** 这些值最终会经
                    # ap.set_defaults() 直接变成 args.mt_batch 之类，
                    # 界面若发来 "8"（字符串）或 8.5，错误会跑到
                    # range()/切片那种地方才炸，离这里十万八千里。
                    old = cur[k]
                    is_bool = isinstance(old, bool)
                    is_int = isinstance(old, int) and not is_bool
                    is_float = isinstance(old, float) and not is_bool
                    if is_bool and not isinstance(v, bool):
                        return self._json({"ok": False, "error":
                                           f"params.{k} 是布尔（收到 {v!r}）"}, 400)
                    if is_int:
                        # 8.0 先归一成 8 再判类型 —— JSON 里整数常被写成 8.0
                        if isinstance(v, float) and v.is_integer():
                            v = int(v)
                        if isinstance(v, bool) or not isinstance(v, int):
                            return self._json({"ok": False, "error":
                                               f"params.{k} 必须是整数（收到 {v!r}）"}, 400)
                    elif is_float and (isinstance(v, bool)
                                       or not isinstance(v, (int, float))):
                        return self._json({"ok": False, "error":
                                           f"params.{k} 必须是数字（收到 {v!r}）"}, 400)
                    cur[k] = v
                    changes[f"modes.{mode}.params.{k}"] = v
            if not changes:
                return self._json({"ok": False, "error": "没有任何可改字段"}, 400)
            data["updated"] = time.strftime("%Y-%m-%d")
            R.save(data)
            R.export_web()
            return self._json({"ok": True, "changed": changes,
                               "snapshot": _snapshot()})

        if path == "/api/engine":
            # POST：{ "action": "start"|"stop"|"restart", "mode": "loopback",
            #         "wait": true, "extra": ["--no-srt"] }
            act = str(req.get("action") or "start").lower()
            mode = str(req.get("mode") or "loopback")
            if act in ("start", "restart"):
                if act == "restart":
                    ENGINE.stop(hard=bool(req.get("hard")))
                    time.sleep(0.5)
                ok, why = ENGINE.start(mode, extra=req.get("extra") or [])
                out: dict = {"ok": ok, "message": why}
                if ok and req.get("wait", True):
                    # 等它真的起来（浮窗出现）再回答 —— 界面上"已启动"
                    # 这四个字必须是**真的**，否则又变成"点了没反应"
                    out["ready"] = ENGINE.wait_ready(
                        float(req.get("timeout") or 90.0))
                out["engine"] = ENGINE.status()
                return self._json(out, 200 if ok else 409)
            if act == "stop":
                ok, why = ENGINE.stop(hard=bool(req.get("hard")))
                return self._json({"ok": ok, "message": why,
                                   "engine": ENGINE.status()},
                                  200 if ok else 409)
            return self._json({"ok": False, "error": f"未知动作 {act!r}"}, 400)

        if path == "/api/overlay":
            # { "changes": { "font_size": 28, ... } } 或 { "changes": {"_action": "recenter"} }
            changes = req.get("changes")
            ok, why = _validate_overlay(changes if isinstance(changes, dict) else {})
            if not ok:
                return self._json({"ok": False, "error": why}, 400)
            try:
                _write_overlay_cmd(changes)      # 直播 + watch 两套都发
            except Exception as e:
                return self._json({"ok": False,
                                   "error": f"写入浮窗命令失败：{e}"}, 500)
            return self._json({"ok": True, "sent": sorted(changes.keys()),
                               "targets": ["loopback", "watch"]})

        if path == "/api/model-repo":
            # { "action": "download"|"cancel"|"default"|"uninstall",
            #   "id": "...", "slot": "asr"|"mt" }
            if MM is None:
                return self._json({"ok": False,
                                   "error": "model_manager 不可用"}, 503)
            act = str(req.get("action") or "")
            mid = str(req.get("id") or "")
            if not mid:
                return self._json({"ok": False, "error": "缺少模型 id"}, 400)
            if act == "download":
                st = MM._load_state().get(mid) or {}
                if st.get("state") == "downloading":
                    return self._json({"ok": False, "error": "正在下载中"}, 409)
                if HW:
                    try:
                        free_gb = HW.detect().get("disk", {}).get("free_gb") or 999
                        meta = next((x for x in MM.search() if x["id"] == mid), None)
                        if meta and meta.get("size_mb") and \
                                meta["size_mb"] / 1024 > free_gb:
                            return self._json({"ok": False,
                                               "error": f"磁盘不够：需要 {meta['size_mb']}MB，"
                                                        f"只剩 {free_gb}GB"}, 400)
                    except Exception:
                        pass
                MM.download(mid, on_done=lambda *a, **kw: None)
                return self._json({"ok": True, "message": f"已开始下载 {mid}"})
            if act == "cancel":
                return self._json({"ok": MM.cancel(mid)})
            if act == "default":
                slot = str(req.get("slot") or "")
                if slot not in ("asr", "mt"):
                    return self._json({"ok": False,
                                       "error": "slot 只能是 asr / mt"}, 400)
                ok, msg = MM.set_default(slot, mid)
                return self._json({"ok": ok, "message": msg},
                                  200 if ok else 400)
            if act == "uninstall":
                ok, msg = MM.uninstall(mid)
                return self._json({"ok": ok, "message": msg},
                                  200 if ok else 400)
            return self._json({"ok": False, "error": f"未知动作 {act!r}"}, 400)

        if path == "/api/hardware":
            # POST = 强制重新检测（GET 用 30s 缓存）
            if HW is None:
                return self._json({"ok": False,
                                   "error": "hardware_detect 不可用"}, 503)
            return self._json({"ok": True, "report": HW.detect(force=True)})

        if path == "/api/admin-config":
            d = _admin_cfg()
            for k in ("prewarm", "watchdog"):
                if k in req:
                    if not isinstance(req[k], bool):
                        return self._json({"ok": False,
                                           "error": f"{k} 必须是布尔"}, 400)
                    d[k] = req[k]
            try:
                _save_admin_cfg(d)
            except Exception as e:
                return self._json({"ok": False, "error": f"写配置失败：{e}"}, 500)
            return self._json({"ok": True, "config": d})

        if path == "/api/prewarm":
            # 后台线程预热，结果落到引擎日志里（界面日志区可见）
            def _job():
                msg = prewarm()
                ENGINE._push(f"· 手动预热完成：{msg}")
            threading.Thread(target=_job, daemon=True).start()
            return self._json({"ok": True,
                               "message": "预热已在后台开始，结果见运行日志"})

        return self._json({"error": "not found", "path": path}, 404)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="巴别塔计划 · 后台管理端（本地）")
    ap.add_argument("--host", default="127.0.0.1", help="只绑本机，别改成 0.0.0.0")
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    a = ap.parse_args(argv)

    if not os.path.isfile(INDEX):
        print(f"！界面文件不存在：{INDEX}")
        return 1
    try:
        R.export_web()                        # 起服务时先把快照刷成最新的
    except Exception as e:
        print(f"！快照导出失败（界面仍可打开，模型表可能偏旧）：{e}")

    httpd = ThreadingHTTPServer((a.host, a.port), Handler)
    start_background()               # 看门狗 + 预热（服务在，守护就在）
    url = f"http://{a.host}:{a.port}/"
    print("=" * 60)
    print("巴别塔计划 · 后台管理端")
    print(f"  界面   {url}")
    print(f"  模型表 {url}api/models")
    print(f"  注册表 {R.PATH}")
    print(f"  两个模块：直播翻译(loopback) / 视频翻译(cache)")
    for mid, r in R.all_resolved().items():
        print(f"    {r['label']:<8} 识别 {r['asr']:<32} 翻译 {r['mt']}")
    print("  Ctrl+C 退出")
    print("=" * 60)
    if not a.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
