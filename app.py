# -*- coding: utf-8 -*-
"""app.py —— 巴别塔计划 · 桌面壳（单入口）

为什么有这个文件
================
以前的入口是三个：start.bat 起引擎、admin.bat 起 Web 后台、admin_qt.bat
起 Qt 后台。Qt 后台解决的问题（浏览器标签页一关就找不到控制台、观感像
临时工具）现在用**壳**解决：Web 界面不变，但装进原生窗口里。

    app.py
      ├─ babel_admin.serve（HTTP 服务，线程内启动，只绑 127.0.0.1）
      │     ├─ 引擎子进程管理 / 看门狗 / 预热（全在服务层）
      │     └─ index.html（全部界面：工作台/模型/仓库/浮窗/记录/设置）
      ├─ pywebview 窗口（Edge WebView2，Win10/11 自带）
      └─ pystray 托盘（关窗 → 收到托盘，不退出；真退出走托盘菜单）

依赖：pip install pywebview pystray pillow
启动：python app.py        （或双击 app.bat）
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "babel_admin"))

HOST = "127.0.0.1"
PORT = 8770
URL = f"http://{HOST}:{PORT}/"


def _server_alive() -> bool:
    try:
        with urllib.request.urlopen(URL + "api/engine", timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False


def _start_server() -> None:
    """线程内起 HTTP 服务；端口已被占用（已有实例在跑）就直接复用。"""
    if _server_alive():
        print(f"· 已有服务在 {URL}，直接接入（不重复启动）")
        return
    from babel_admin import serve
    from http.server import ThreadingHTTPServer
    try:
        serve.R.export_web()
    except Exception as e:
        print(f"！快照导出失败（界面仍可打开）：{e}")
    httpd = ThreadingHTTPServer((HOST, PORT), serve.Handler)
    serve.start_background()           # 看门狗 + 预热
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    # 等服务真的起来再开窗，否则窗口先白屏
    for _ in range(50):
        if _server_alive():
            break
        time.sleep(0.1)
    print(f"· 服务已启动 {URL}")


def _tray_image():
    """托盘图标：绿底圆 + 白色声波（不依赖外部资源文件）。"""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, 60, 60), fill=(22, 163, 74, 255))
    for i, h in enumerate((14, 26, 36, 26, 14)):      # 五根声波竖条
        x = 20 + i * 6
        d.rectangle((x, 32 - h // 2, x + 3, 32 + h // 2), fill=(255, 255, 255, 255))
    return img


def main() -> int:
    try:
        import webview
    except ImportError:
        print("！缺 pywebview：pip install pywebview pystray pillow")
        return 1

    _start_server()

    window = webview.create_window(
        "巴别塔计划 · 控制台", URL,
        width=1240, height=820, min_size=(960, 640),
        text_select=True)

    # ── 托盘：关窗不退程序（行为与旧 Qt 后台一致）────────────────
    tray = None
    try:
        import pystray

        def show_win(*_):
            try:
                window.show()
                window.restore()
            except Exception:
                pass

        def quit_app(*_):
            try:
                window.destroy()
            except Exception:
                pass

        tray = pystray.Icon(
            "babel-plan", _tray_image(), "巴别塔计划",
            menu=pystray.Menu(
                pystray.MenuItem("打开控制台", show_win, default=True),
                pystray.MenuItem("退出", quit_app)))
        tray.run_detached()
    except Exception as e:
        print(f"！托盘不可用（关窗即退出）：{e}")

    def on_closing():
        if tray is not None:
            window.hide()
            return False                    # 取消关闭，收到托盘
        return True

    window.events.closing += on_closing

    webview.start()
    # 窗口真正销毁后（托盘菜单「退出」）：服务线程是 daemon，随进程退出；
    # 引擎子进程不归这里管 —— 它由服务层的看门狗看着，下次打开还能接上。
    if tray is not None:
        try:
            tray.stop()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
