# -*- coding: utf-8 -*-
"""desktop/app_launcher.py —— 带图标的 BabelPlan.exe 启动器。

为什么不直接打包 app.py：app.py 依赖项目目录里的模型/缓存/管理端文件，
onefile 打包后路径会变。这里只做一个“带图标的启动键”：
exe 自身带 babel.ico，启动后转交给现有 BabelPlan.vbs（它负责找 pythonw
并拉起 app.py），然后退出。这样任务栏/资源管理器里看到的是 BabelPlan
自己的图标，而不是 wscript/pythonw 的通用图标。
"""
from __future__ import annotations

import os
import subprocess
import sys

CREATE_NO_WINDOW = 0x08000000


def _root() -> str:
    here = (os.path.dirname(os.path.abspath(sys.executable))
            if getattr(sys, "frozen", False)
            else os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    # exe 在 desktop/ 下时，项目根是上一级
    for cand in (here, os.path.dirname(here)):
        if os.path.isfile(os.path.join(cand, "desktop", "BabelPlan.vbs")):
            return cand
    return here


def main() -> int:
    root = _root()
    vbs = os.path.join(root, "desktop", "BabelPlan.vbs")
    if not os.path.isfile(vbs):
        return 2
    try:
        subprocess.Popen(
            ["wscript.exe", vbs],
            cwd=root,
            creationflags=CREATE_NO_WINDOW,
            close_fds=True,
        )
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
