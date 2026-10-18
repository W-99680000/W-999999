# -*- coding: utf-8 -*-
"""desktop/make_icon.py —— 生成 desktop/babel.ico

为什么有这个脚本
================
托盘图标原来是在 app.py 里用 PIL 现画的（绿底圆 + 白声波），
快捷方式和 exe 需要一张 **.ico 文件**。如果这里另画一套，改颜色时
两处就会漂移 —— 所以本脚本**直接导入 app.py 的 `_tray_image()`**，
把同一张图渲染成多尺寸 ICO：

    16 / 24 / 32 / 48 / 64 / 128 / 256

用法
====
    python desktop/make_icon.py
    # 可选：--out 指定输出路径
    # 可选：--preview 另存一张 256px PNG，方便肉眼看

生成物是二进制，进仓库；需要改图标时改 app.py 的画法后重跑本脚本。
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

#: 与 app.py 托盘共用同一张图（单一真相）
from app import _tray_image  # noqa: E402

#: Windows 图标常用尺寸（16 任务栏 / 32 桌面 / 48 资源管理器 / 256 大图标）
SIZES = [16, 24, 32, 48, 64, 128, 256]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成巴别塔计划图标 babel.ico")
    ap.add_argument("--out", default=os.path.join(HERE, "babel.ico"),
                    help="输出 .ico 路径（默认 desktop/babel.ico）")
    ap.add_argument("--preview", action="store_true",
                    help="另存一张 256px PNG 预览")
    args = ap.parse_args(argv)

    try:
        from PIL import Image  # noqa: F401  (确认运行时依赖在)
    except ImportError:
        print("！缺 Pillow：pip install pillow")
        return 1

    # 以 256 为母图渲染（ICO 里的大图标用它下采样），再等比生成各尺寸
    base = _tray_image(256)
    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    base.save(out, format="ICO", sizes=[(s, s) for s in SIZES])

    size = os.path.getsize(out)
    print(f"已生成 {out}（{size} 字节，{len(SIZES)} 个尺寸："
          f"{', '.join(str(s) for s in SIZES)}）")

    if args.preview:
        png = os.path.splitext(out)[0] + ".png"
        base.save(png, format="PNG")
        print(f"预览  {png}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
