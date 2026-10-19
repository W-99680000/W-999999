# -*- coding: utf-8 -*-
"""app.py —— 巴别塔计划 · 桌面壳（单入口）

为什么是这个样子
================
以前的入口是三个：start.bat 起引擎、admin.bat 起 Web 后台、admin_qt.bat
起 Qt 后台。现在消费者版要求"双击图标就开、不出现黑色命令行窗口"，
所以桌面壳必须自己解决四件事（都在本文件里）：

  ① **没有控制台也能活**：消费者版由 desktop/BabelPlan.vbs 用
     pythonw.exe 启动，进程没有控制台，`sys.stdout` / `sys.stderr`
     是 None —— 此时任何 print 都会 AttributeError。→ _setup_logging()
     把它们换成"写日志文件的流"，同时尽量保留控制台回显（调试时用）。
  ② **日志能查**：`%LOCALAPPDATA%\\BabelPlan\\app.log`，超过 2MB 轮转
     （app.log.1 … app.log.3）。"双击没反应"时先看日志，不靠猜。
  ③ **不能开出第二个托盘 / 第二个窗口**：命名互斥量
     `Local\\BabelPlan.Desktop`。第二个进程写一行日志后 exit 0。
  ④ **退出要干净**：关窗只是收到托盘；真退出走托盘菜单，退出时关掉
     自己起的 HTTP 服务、停掉**本进程拉起的**引擎子进程、落日志。

原有能力不变：pywebview（Edge WebView2）窗口 + pystray 托盘 +
线程内起 babel_admin.serve HTTP（127.0.0.1:8770）。
**若 8770 已有服务（例如 admin.bat 开的），直接复用，不重复启动。**

依赖：pip install pywebview pystray pillow

启动方式
========
    python app.py                 开发调试（带控制台，日志同时落盘）
    python app.py --tray          启动后直接收进托盘，不弹窗
    python app.py --keep-engine   退出时保留本进程拉起的引擎
    wscript desktop\\BabelPlan.vbs     消费者双击（无黑窗，见 docs/DESKTOP.md）
"""
from __future__ import annotations
import model_home  # noqa: E402,F401  # 先把模型目录钉到 D 盘（务必在 HF/faster-whisper 之前）

import argparse
import atexit
import ctypes
import os
import sys
import threading
import time
import traceback
import urllib.request
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "babel_admin"))

HOST = "127.0.0.1"
PORT = 8770
URL = f"http://{HOST}:{PORT}/"

#: 窗口标题与托盘提示用同一个名字（消费者看到的是一个产品）
APP_NAME = "巴别塔计划 · 直播翻译与视频翻译"

#: 命名互斥量：`Local\` 前缀表示只在当前登录会话内生效（多用户不互相挡）
MUTEX_NAME = "Local\\BabelPlan.Desktop"

_ERROR_ALREADY_EXISTS = 183

#: 日志目录 / 文件（%LOCALAPPDATA% 缺省时退回用户目录）
LOG_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "BabelPlan")
LOG_PATH = os.path.join(LOG_DIR, "app.log")
LOG_MAX_BYTES = 2 * 1024 * 1024      # 2MB 轮转，避免日志无限涨
LOG_KEEP = 3                        # 留 app.log.1 .. app.log.3


# ══════════════════════════════════════════════════════════════════
#  日志：在没有控制台的进程里替代 stdout / stderr
# ══════════════════════════════════════════════════════════════════
class _LogSink:
    """共享的日志文件句柄（stdout/stderr 两个流写同一个文件）。"""

    def __init__(self, path: str) -> None:
        self.path = path
        # buffering=1：按行缓冲；下面每次 write 仍然显式 flush，
        # 崩溃前的最后一行日志不能丢 —— 那正是排查时要看的。
        self._fh = open(path, "a", encoding="utf-8", errors="replace",
                        buffering=1)
        self._lock = threading.RLock()
        self.broken = False

    def write(self, text: str) -> None:
        if not text or self.broken:
            return
        with self._lock:
            try:
                self._fh.write(text)
                self._fh.flush()
            except (OSError, ValueError):
                # 磁盘满 / 文件被占用：不再反复报错，程序继续跑
                self.broken = True

    def close(self) -> None:
        with self._lock:
            try:
                self._fh.close()
            except OSError:
                pass


class _Tee:
    """把写入同时送到日志文件和原来的控制台（如果有）。

    形状要装得像 `sys.stdout`：print / logging / argparse 都会读
    `encoding`、`errors`，偶尔会问 `isatty()`。
    """

    encoding = "utf-8"
    errors = "replace"

    def __init__(self, sink: _LogSink | None, echo=None) -> None:
        self._sink = sink
        self._echo = echo

    def write(self, text):
        if not isinstance(text, str):
            text = str(text)
        if self._sink is not None:
            self._sink.write(text)
        if self._echo is not None:
            try:
                self._echo.write(text)
                self._echo.flush()
            except Exception:
                pass
        return len(text)

    def flush(self) -> None:
        if self._echo is not None:
            try:
                self._echo.flush()
            except Exception:
                pass

    def isatty(self) -> bool:
        return False

    def writable(self) -> bool:
        return True

    def fileno(self):
        # 不能把日志文件句柄冒充成"标准输出"给子进程继承：
        # 子进程的输出统一走 subprocess.PIPE（见 babel_admin/serve.py）。
        raise OSError("日志流没有可继承的文件描述符")


_LOG_SINK: _LogSink | None = None
_LOG_READY = False
_EXIT_LOGGED = False


def _rotate_log() -> None:
    """启动时轮转：app.log → .1 → .2 → .3（最老的丢掉）。"""
    try:
        if not os.path.isfile(LOG_PATH):
            return
        if os.path.getsize(LOG_PATH) < LOG_MAX_BYTES:
            return
        oldest = f"{LOG_PATH}.{LOG_KEEP}"
        if os.path.exists(oldest):
            os.remove(oldest)
        for i in range(LOG_KEEP - 1, 0, -1):
            src, dst = f"{LOG_PATH}.{i}", f"{LOG_PATH}.{i + 1}"
            if os.path.exists(src):
                os.replace(src, dst)
        os.replace(LOG_PATH, LOG_PATH + ".1")
    except OSError:
        # 轮转失败不该影响启动，继续往旧文件追加
        pass


def _stream_ok(stream) -> bool:
    return stream is not None and hasattr(stream, "write")


def _setup_logging() -> None:
    """把 stdout/stderr 换成 _Tee（日志文件 + 可选控制台）。幂等。"""
    global _LOG_SINK, _LOG_READY
    if _LOG_READY:
        return
    old_out = sys.stdout if _stream_ok(sys.stdout) else None
    old_err = sys.stderr if _stream_ok(sys.stderr) else None
    sink = None
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        _rotate_log()
        sink = _LogSink(LOG_PATH)
    except OSError as e:
        # 日志都写不了（权限/磁盘），也要让程序能开：
        # 消费者看不到黑窗，但至少进程别死在这一步。
        if old_err is not None:
            try:
                old_err.write(f"! 无法创建日志 {LOG_PATH}: {e}\n")
            except Exception:
                pass
    _LOG_SINK = sink
    sys.stdout = _Tee(sink, old_out)
    sys.stderr = _Tee(sink, old_err)
    _LOG_READY = True


def log(msg: str) -> None:
    """带时间戳写一行日志（同时回显到控制台，如果有）。"""
    try:
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n"
        if sys.stdout is None:          # 理论上 _setup_logging 已兜住
            return
        sys.stdout.write(line)
        sys.stdout.flush()
    except Exception:
        pass


def _log_exit(reason: str = "进程结束") -> None:
    global _EXIT_LOGGED
    if _EXIT_LOGGED:
        return
    _EXIT_LOGGED = True
    log(f"· {reason}（pid={os.getpid()}）")


def _fatal(title: str, msg: str) -> None:
    """致命错误：写日志 +（无控制台时）弹一个消息框，别让双击静默失败。"""
    log(f"！{title}：{msg}")
    if os.name != "nt":
        return
    try:
        text = f"{msg}\n\n日志：{LOG_PATH}"
        ctypes.windll.user32.MessageBoxW(None, text, f"{APP_NAME} - {title}",
                                         0x10)     # MB_ICONERROR
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════
#  单实例：命名互斥量
# ══════════════════════════════════════════════════════════════════
_MUTEX_HANDLE = None       # 句柄要留到进程结束，不能提前释放


def _acquire_single_instance() -> tuple[bool, str]:
    """返回 (是否拿到单实例, 说明)。

    Windows 上 CreateMutexW 在"名字已存在"时也会成功返回句柄，
    但 GetLastError() == ERROR_ALREADY_EXISTS —— 这就是判断依据。
    拿不到（极少见）时选择**继续启动**：宁可有小概率双开，
    也不要把用户挡在一个"打不开"的门外。
    """
    global _MUTEX_HANDLE
    if os.name != "nt":
        return True, "非 Windows，跳过互斥量"
    try:
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL,
                                     wintypes.LPCWSTR)
        k32.CreateMutexW.restype = wintypes.HANDLE
        k32.CloseHandle.argtypes = (wintypes.HANDLE,)
        k32.CloseHandle.restype = wintypes.BOOL
        handle = k32.CreateMutexW(None, False, MUTEX_NAME)
        err = ctypes.get_last_error()
        if not handle:
            return True, f"CreateMutexW 失败（WinError {err}），继续启动"
        if err == _ERROR_ALREADY_EXISTS:
            k32.CloseHandle(handle)
            return False, f"互斥量 {MUTEX_NAME} 已存在（已有实例在跑）"
        _MUTEX_HANDLE = handle
        return True, f"已持有互斥量 {MUTEX_NAME}"
    except Exception as e:
        return True, f"互斥量不可用（{type(e).__name__}: {e}），继续启动"


# ══════════════════════════════════════════════════════════════════
#  HTTP 服务（线程内启动；已有服务就复用）
# ══════════════════════════════════════════════════════════════════
_SERVER = {"httpd": None, "owned": False, "serve": None}


class _QuietHTTPServer(ThreadingHTTPServer):
    """本地服务用：客户端断开（刷新/关窗）不要往日志里刷 traceback。

    BaseHTTPRequestHandler 在浏览器刷新的一瞬间被 reset 是**常态**，
    默认实现会把整段 ConnectionResetError 打到 stderr —— 消费者日志
    会被这些噪音淹没，真正要看的一行反而找不到。
    """

    daemon_threads = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError,
                            BrokenPipeError, TimeoutError)):
            return
        log(f"！HTTP 请求处理出错：{type(exc).__name__}: {exc}")


def _server_alive() -> bool:
    try:
        with urllib.request.urlopen(URL + "api/engine", timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False


def _start_server() -> bool:
    """确保 8770 有可用服务：已经有就复用，没有就本进程起一个。"""
    if _server_alive():
        log(f"· 8770 已有服务在 {URL}，直接复用（不重复启动）")
        return True

    try:
        from babel_admin import serve
    except Exception:
        log("！导入 babel_admin.serve 失败：\n" + traceback.format_exc())
        _fatal("后台服务不可用",
               "babel_admin/serve.py 导入失败，界面无法工作。\n"
               "请确认 babel_admin 目录完整，或查看日志。")
        return False
    _SERVER["serve"] = serve
    log("· 8770 没有可用服务，本进程启动一个")

    # 模型快照导出会探测本机模型/设备，冷启动时可能要几秒到几十秒。
    # **不能阻塞窗口**：先起服务、开窗，导出放后台线程；页面打开后
    # /api/models 会实时探测，快照只是 file:// 兜底。
    def _export_snapshot():
        try:
            serve.R.export_web()
            log("· 模型快照已就绪（后台，model_registry_web.json）")
        except Exception as e:
            log(f"！后台快照导出失败（界面仍可打开，模型表可能偏旧）："
                f"{type(e).__name__}: {e}")
    threading.Thread(target=_export_snapshot, daemon=True,
                     name="babel-export").start()
    log("· 模型快照后台刷新中（不阻塞窗口）")

    try:
        httpd = _QuietHTTPServer((HOST, PORT), serve.Handler)
    except OSError as e:
        log(f"！端口 {PORT} 无法绑定：{type(e).__name__}: {e}")
        # 端口检查与 bind 之间有竞态：复查一次，能连上就复用
        if _server_alive():
            log(f"· 复查发现 {URL} 可用，改为复用")
            return True
        _fatal("本地服务启动失败",
               f"127.0.0.1:{PORT} 被占用且不是巴别塔计划的服务。\n"
               "请先结束占用该端口的程序，或重启电脑后再试。")
        return False

    _SERVER["httpd"] = httpd
    _SERVER["owned"] = True
    serve.start_background()            # 看门狗 + 预热
    threading.Thread(target=httpd.serve_forever, daemon=True,
                     name="babel-http").start()
    # 等服务真的起来再开窗，否则窗口先白屏
    for _ in range(50):
        if _server_alive():
            break
        time.sleep(0.1)
    if _server_alive():
        log(f"· 服务已启动 {URL}")
    else:
        log(f"！服务线程已起，但 {URL}api/engine 5 秒内未通过健康检查")
    return True


def _shutdown_server() -> None:
    httpd = _SERVER.get("httpd")
    if httpd is None:
        return
    try:
        httpd.shutdown()
        log("· HTTP 服务已停止接受请求")
    except Exception as e:
        log(f"！停止 HTTP 服务出错：{type(e).__name__}: {e}")
    try:
        httpd.server_close()
    except Exception:
        pass
    _SERVER["httpd"] = None


# ══════════════════════════════════════════════════════════════════
#  引擎子进程：只停"本进程拉起的"，用户已有的不碰
# ══════════════════════════════════════════════════════════════════
def _stop_engine(keep: bool) -> None:
    serve = _SERVER.get("serve")
    if serve is None or not _SERVER.get("owned"):
        log("· 服务不是本进程起的，不动任何引擎子进程")
        return
    eng = getattr(serve, "ENGINE", None)
    if eng is None or not eng.alive():
        log("· 本进程没有在跑的引擎子进程")
        return
    pid = getattr(getattr(eng, "proc", None), "pid", "?")
    if keep:
        log(f"· 按用户选择保留引擎子进程 pid={pid}（下次打开会接上）")
        return
    log(f"· 退出前停止本进程拉起的引擎子进程 pid={pid}")
    try:
        _ok, msg = eng.stop(hard=False, wait=6.0)
        log(f"· 引擎停止（先优雅）：{msg}")
    except Exception as e:
        log(f"！停止引擎出错：{type(e).__name__}: {e}")
    if eng.alive():
        try:
            _ok, msg = eng.stop(hard=True, wait=5.0)
            log(f"· 引擎停止（强制）：{msg}")
        except Exception as e:
            log(f"！强制停止引擎出错：{type(e).__name__}: {e}")


# ══════════════════════════════════════════════════════════════════
#  图标 / 托盘
# ══════════════════════════════════════════════════════════════════
def _set_window_icon(window) -> bool:
    """给窗口/任务栏设置 babel.ico。

    pywebview（Edge WebView2）默认用 pythonw.exe 的图标 —— 任务栏上一个
    Python 图标/空白，看起来像"没有启动图标"。这里用 Win32 WM_SETICON
    把 desktop/babel.ico 设成窗口大/小图标，并设置 AppUserModelID 让
    任务栏按应用分组。
    """
    if os.name != "nt":
        return False
    ico = os.path.join(HERE, "desktop", "babel.ico")
    if not os.path.isfile(ico):
        return False
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "BabelPlan.Desktop")
    except Exception:
        pass
    user32 = ctypes.windll.user32
    try:
        user32.LoadImageW.restype = ctypes.c_void_p
        user32.LoadImageW.argtypes = [
            ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint,
            ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        user32.SendMessageW.restype = ctypes.c_void_p
        user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                        ctypes.c_void_p, ctypes.c_void_p]
        user32.FindWindowW.restype = ctypes.c_void_p
        user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
    except Exception:
        pass
    hwnd = 0
    try:
        native = getattr(window, "native", None)
        handle = getattr(native, "Handle", None)
        if handle is not None:
            hwnd = int(handle.ToInt64() if hasattr(handle, "ToInt64")
                       else handle)
    except Exception:
        hwnd = 0
    if not hwnd:
        try:
            hwnd = int(user32.FindWindowW(None, APP_NAME) or 0)
        except Exception:
            hwnd = 0
    if not hwnd:
        return False
    # 再兜一层：pywebview 的 Form 如果没吃上 icon，直接改 WinForms Icon
    try:
        from System.Drawing import Icon as _Icon
        native = getattr(window, "native", None)
        if native is not None and hasattr(native, "Icon"):
            native.Icon = _Icon(ico)
    except Exception:
        pass
    WM_SETICON = 0x0080
    IMAGE_ICON = 1
    LR_LOADFROMFILE = 0x0010
    big = user32.LoadImageW(None, ico, IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
    small = user32.LoadImageW(None, ico, IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
    if big:
        user32.SendMessageW(hwnd, WM_SETICON, 1, big)    # ICON_BIG
    if small:
        user32.SendMessageW(hwnd, WM_SETICON, 0, small)  # ICON_SMALL
    return bool(big or small)


def _tray_image(size: int = 64):
    """托盘图标：绿底圆 + 白色声波（不依赖外部资源文件）。

    坐标以 64×64 为基准等比缩放。`desktop/make_icon.py` **直接复用**
    本函数生成 babel.ico，保证托盘 / 快捷方式 / 窗口是同一张图
    （改画法只改这一处）。
    """
    from PIL import Image, ImageDraw
    size = max(8, int(size))
    k = size / 64.0

    def S(v: float) -> int:
        return int(round(v * k))

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((S(4), S(4), S(60), S(60)), fill=(22, 163, 74, 255))
    for i, h in enumerate((14, 26, 36, 26, 14)):      # 五根声波竖条
        x = 20 + i * 6
        d.rectangle((S(x), S(32 - h // 2), S(x + 3), S(32 + h // 2)),
                    fill=(255, 255, 255, 255))
    return img


_QUIT = threading.Event()
_KEEP_ENGINE = False


def _request_quit(window, reason: str) -> None:
    if _QUIT.is_set():
        return
    _QUIT.set()
    log(f"· 收到退出请求：{reason}")
    try:
        window.destroy()
    except Exception as e:
        log(f"！销毁窗口失败：{type(e).__name__}: {e}")


def _make_tray(window):
    """建托盘。失败返回 None（此时关窗即退出）。"""
    global _KEEP_ENGINE
    if os.environ.get("BABEL_NO_TRAY", "") == "1":
        log("· BABEL_NO_TRAY=1：跳过托盘（关窗即退出，调试/自检用）")
        return None
    try:
        import pystray
    except Exception as e:
        log(f"！缺 pystray，托盘不可用（关窗即退出）：{e}")
        return None

    def show_win(*_):
        try:
            window.show()
            window.restore()
        except Exception as e:
            log(f"！显示窗口失败：{type(e).__name__}: {e}")

    def open_logs(*_):
        try:
            os.startfile(LOG_DIR)       # noqa: S606 - 本机目录，非外部输入
        except Exception as e:
            log(f"！打开日志目录失败：{type(e).__name__}: {e}")

    def quit_app(*_):
        global _KEEP_ENGINE
        _KEEP_ENGINE = False
        _request_quit(window, "托盘菜单：退出（停止引擎）")

    def quit_keep(*_):
        global _KEEP_ENGINE
        _KEEP_ENGINE = True
        _request_quit(window, "托盘菜单：退出（保留引擎）")

    try:
        menu = pystray.Menu(
            pystray.MenuItem("打开控制台", show_win, default=True),
            pystray.MenuItem("打开日志目录", open_logs),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出（停止引擎）", quit_app),
            pystray.MenuItem("退出（保留引擎）", quit_keep),
        )
        tray = pystray.Icon("babel-plan", _tray_image(64), APP_NAME, menu=menu)
        tray.run_detached()
        log("· 托盘已就绪（关闭窗口 = 收到托盘；退出走托盘菜单）")
        return tray
    except Exception as e:
        log(f"！托盘初始化失败（关窗即退出）：{type(e).__name__}: {e}")
        return None


# ══════════════════════════════════════════════════════════════════
#  启动参数 / 环境变量
# ══════════════════════════════════════════════════════════════════
def _parse_args(argv=None):
    ap = argparse.ArgumentParser(
        prog="app.py", description=f"{APP_NAME} - 桌面壳")
    ap.add_argument("--tray", action="store_true",
                    help="启动后直接收进托盘，不弹窗口")
    ap.add_argument("--keep-engine", action="store_true",
                    help="退出时保留本进程拉起的引擎子进程")
    ap.add_argument("--debug", action="store_true",
                    help="开发调试（控制台输出照常，日志同时落盘）")
    return ap.parse_args(argv)


def _silence_child_consoles() -> bool:
    """消费者模式（pythonw、没有控制台）下，给所有子进程加 CREATE_NO_WINDOW。

    为什么必须做：Windows 会给"没有控制台的父进程"所启动的**控制台**
    子进程新分配一个控制台（conhost）—— 即使 stdout/stderr 都重定向到
    管道也一样。实测：babel_admin/dlls.py 探测解释器时，
    `subprocess.run(python.exe -c ...)` 会闪一个黑色控制台窗口。
    管理端界面每 1~2 秒轮询 /api/engine，第一次轮询就会触发这次探测。

    在 app.py 一处兜住，比要求每个调用点都记得传 creationflags 可靠。
    只在本进程确实没有控制台时启用；开发调试（有控制台）保持原样，
    子进程会继承控制台，不需要也不应该加这个 flag。

    CREATE_NO_WINDOW (0x08000000) 与 serve.py 用的
    CREATE_NEW_PROCESS_GROUP (0x00000200) 可以并存，重复 OR 无副作用。
    """
    if os.name != "nt":
        return False
    try:
        if ctypes.windll.kernel32.GetConsoleWindow():
            return False
    except Exception:
        return False
    import subprocess
    create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    original = subprocess.Popen
    if getattr(original, "_babel_no_window", False):
        return True

    class _NoWindowPopen(original):       # type: ignore[misc, valid-type]
        _babel_no_window = True

        def __init__(self, *args, **kwargs):
            flags = kwargs.get("creationflags", 0) or 0
            kwargs["creationflags"] = flags | create_no_window
            super().__init__(*args, **kwargs)

    _NoWindowPopen.__name__ = "Popen"
    subprocess.Popen = _NoWindowPopen     # type: ignore[assignment]
    return True


def _apply_babel_py() -> None:
    """`BABEL_PY`：引擎解释器覆盖入口。

    真正的挑选逻辑在 babel_admin/dlls.py，它读 `BABEL_PYTHON`；这里把
    消费者只需要记的 `BABEL_PY` 映射过去，让"exe 壳 + 本机 Python 引擎"
    这条路线有个明确入口（引擎仍然用本机 Python，不打包进 exe）。
    """
    raw = (os.environ.get("BABEL_PY") or "").strip().strip('"')
    if not raw:
        return
    if not os.path.isfile(raw):
        log(f"！BABEL_PY 指向的文件不存在：{raw}（忽略，按默认规则挑解释器）")
        return
    exe = raw
    if os.path.basename(raw).lower() == "pythonw.exe":
        cand = os.path.join(os.path.dirname(raw), "python.exe")
        if os.path.isfile(cand):
            exe = cand
    os.environ["BABEL_PYTHON"] = exe
    log(f"· 引擎解释器覆盖：BABEL_PY={exe}（映射为 BABEL_PYTHON）")


# ══════════════════════════════════════════════════════════════════
#  主流程
# ══════════════════════════════════════════════════════════════════
def main(argv=None) -> int:
    _setup_logging()
    atexit.register(_log_exit)
    try:                                     # 后台保存的 API key / HF token
        import key_store as _ak
        _ak.apply_env()
    except Exception:
        pass
    args = _parse_args(argv)

    log("=" * 64)
    log(f"{APP_NAME} 启动")
    log(f"  时间     {time.strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"  pid      {os.getpid()}")
    log(f"  python   {sys.version.split()[0]}  ({sys.executable})")
    log(f"  项目目录 {HERE}")
    log(f"  日志     {LOG_PATH}")
    log(f"  模式     {'--tray（收进托盘）' if args.tray else '正常开窗'}"
        f"{' / 退出时保留引擎' if args.keep_engine else ''}")

    if _silence_child_consoles():
        log("· 无控制台模式：所有子进程统一加 CREATE_NO_WINDOW（不闪黑窗）")

    _apply_babel_py()

    ok, why = _acquire_single_instance()
    log(f"· 单实例：{why}")
    if not ok:
        log("· 已有实例在运行，第二个实例直接退出（exit 0）")
        return 0

    try:
        import webview
    except Exception:
        log("！导入 pywebview 失败：\n" + traceback.format_exc())
        _fatal("缺少 pywebview",
               "桌面壳需要 pywebview（Edge WebView2）。\n"
               "安装：pip install pywebview pystray pillow")
        return 1

    if not _start_server():
        return 1

    # 任务栏按应用分组（必须在创建窗口之前设置）
    if os.name == "nt":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "BabelPlan.Desktop")
        except Exception:
            pass
    _ico = os.path.join(HERE, "desktop", "babel.ico")
    try:
        window = webview.create_window(
            APP_NAME, URL,
            width=1240, height=820, min_size=(960, 640),
            text_select=True,
            hidden=bool(args.tray),     # --tray：建窗就是隐藏的，不闪一下
        )
        log("· 窗口已创建（pywebview / Edge WebView2）")
    except Exception:
        log("！创建窗口失败：\n" + traceback.format_exc())
        _fatal("窗口创建失败",
               "无法创建桌面窗口。\n请确认 WebView2 运行时已安装"
               "（Win10/11 一般自带）。")
        _shutdown_server()
        return 1

    tray = _make_tray(window)

    def on_closing():
        # pywebview：返回 False = 取消关闭。关窗 = 收到托盘；
        # 托盘"退出"会先 _QUIT.set()，这时放行，窗口真正销毁。
        if _QUIT.is_set():
            return True
        if tray is not None:
            log("· 关闭窗口 → 收到托盘（程序继续运行）")
            try:
                window.hide()
            except Exception:
                pass
            return False
        log("· 没有托盘，关闭窗口即退出")
        return True

    window.events.closing += on_closing

    def after_start():
        # --tray 时窗口是隐藏建的：托盘没建成就要主动显示，
        # 否则用户会以为"双击没反应"。
        if args.tray and tray is None:
            try:
                window.show()
                log("· 托盘不可用，已直接显示窗口")
            except Exception:
                pass
        elif not args.tray:
            # 兜底再显示一次：启动器如果把 STARTUPINFO.wShowWindow 设成
            # SW_HIDE（例如 wscript.Run 的窗口样式传 0），Windows 会忽略
            # WinForms 的**第一次** ShowWindow —— 窗口"开了但看不见"。
            # 第二次 show 一定生效。实测踩过：日志一切正常，任务栏没图标。
            try:
                window.show()
            except Exception as e:
                log(f"！显示窗口失败：{type(e).__name__}: {e}")
        # 窗口/任务栏图标（pywebview 默认会显示 pythonw 的图标）
        try:
            if _set_window_icon(window):
                log("· 窗口图标已设置（babel.ico）")
            else:
                log("· ！窗口图标设置失败（不影响使用）")
        except Exception as e:
            log(f"· ！窗口图标设置异常：{type(e).__name__}: {e}")
        log("· 界面已就绪")

    try:
        # pywebview 的 winforms 后端支持 icon=：会给 Form 设 Icon
        # （任务栏/标题栏从根上就是 babel.ico，而不是 pythonw 的图标）
        _start_icon = _ico if os.path.isfile(_ico) else None
        try:
            webview.start(after_start, icon=_start_icon)
        except TypeError:
            webview.start(after_start)
    except Exception:
        log("！界面启动失败：\n" + traceback.format_exc())
        _fatal("界面启动失败",
               "WebView2 窗口没能启动。\n"
               "请确认已安装 Edge WebView2 运行时，详见日志。")
        _shutdown_server()
        return 1

    # ── 窗口真正销毁后：收尾 ──────────────────────────────────
    log("· 窗口已销毁，开始退出")
    if tray is not None:
        try:
            tray.stop()
            log("· 托盘图标已移除")
        except Exception as e:
            log(f"！移除托盘失败：{type(e).__name__}: {e}")
    _stop_engine(keep=bool(args.keep_engine) or _KEEP_ENGINE)
    _shutdown_server()
    log("· 退出完成（exit 0）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
