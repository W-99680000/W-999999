# -*- coding: utf-8 -*-
"""control_panel.py —— 字幕控制面板（前端）

为什么单独一个进程/文件
========================
主程序是 PyQt5（浮窗必须透明置顶），控制面板用 tkinter 更简单也不必
与浮窗抢 Qt 事件循环。两者通过**一个 JSON 文件**通信：

    overlay_qt.json      浮窗的设置（主程序本来就在读写它）
    control_cmd.json     面板写的"待应用"命令（主程序轮询）

协议很简单：面板把改动写进 control_cmd.json 并带上时间戳；主程序每
300ms 看一眼，发现时间戳变了就把里面的键合并进自己的设置并重绘。
反方向：主程序把当前设置写回 overlay_qt.json，面板定时读它刷新显示。
**没有 socket、没有端口、没有鉴权问题** —— 对本地单用户工具足够。

设计目标（操作者原话）
  · "功能键都集成在命令集里面能不能做一个前端把功能键都体现出来"
    → 每个快捷键都在面板上有对应控件，并显示当前值
  · "并可以挂在后台"
    → 面板是独立进程，关掉它不影响字幕；字幕照常跑
"""
from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS = os.path.join(HERE, "overlay_qt.json")
CMD = os.path.join(HERE, "control_cmd.json")

# 面板能改的键（与 overlay_qt.default_settings 对齐）
NUM_KEYS = [
    ("font_size", "字号", 10, 120, 1),
    ("opacity", "不透明度", 0.20, 1.00, 0.05),
    ("width", "宽度", 300, 1900, 20),
    ("max_lines", "显示条数", 1, 8, 1),
    ("hold_seconds", "每条停留(秒)", 1.0, 20.0, 0.5),
    ("line_height", "行距", 0.9, 2.0, 0.05),
    ("bg_alpha", "底色浓度", 0.0, 1.0, 0.05),
    ("outline_width", "描边粗细", 0.0, 4.0, 0.05),
    ("original_ratio", "原文字号比", 0.4, 1.2, 0.02),
]
CHOICE_KEYS = [
    ("layout", "排布", ["inline", "stacked", "zh_only"],
     {"inline": "同行(两列)", "stacked": "上下两行", "zh_only": "只译文"}),
    ("text_align", "对齐", ["center", "left", "right"],
     {"center": "居中", "left": "左对齐", "right": "右对齐"}),
]
BOOL_KEYS = [
    ("show_original", "显示原文"),
    ("edit_mode", "调整模式(显边框)"),
    ("theme_dark_video", "深色画面主题"),
]

# 快捷键说明（面板上逐条列出，点按钮就等于按那个键）
HOTKEYS = [
    ("拖顶部横条", "移动字幕（双击回默认位置）"),
    ("滚轮", "字号 +/-"),
    ("Shift+滚轮", "显示条数 +/-"),
    ("Ctrl+滚轮", "不透明度 +/-"),
    ("Alt+滚轮", "宽度 +/-"),
    ("方向键", "微调位置 12/10px"),
    ("[  ]", "字号 -/+"),
    ("-  =", "不透明度 -/+"),
    ("J", "对齐：居中 → 左 → 右"),
    ("L", "排布：同行 → 上下 → 只译文"),
    ("M", "调整模式（显示边框与把手）"),
    ("T", "深浅底主题"),
    ("C", "回默认位置"),
    ("H", "隐藏 / 显示"),
    ("R", "全部重置"),
    ("Esc", "退出"),
]


def read_json(p, default=None):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def write_cmd(changes):
    """把改动写给主程序。带时间戳，主程序据此判断"有新命令"。"""
    cur = read_json(CMD, {})
    cur["_ts"] = time.time()
    cur["_changes"] = changes
    tmp = CMD + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False)
    os.replace(tmp, CMD)


def main():
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception as e:
        print(f"没有 tkinter，无法启动控制面板：{e}")
        return 1

    root = tk.Tk()
    root.title("字幕控制面板")
    root.geometry("560x760")
    root.minsize(480, 560)
    root.attributes("-topmost", True)

    hdr = tk.Frame(root)
    hdr.pack(fill="x", padx=10, pady=(10, 4))
    tk.Label(hdr, text="字幕控制面板", font=("Microsoft YaHei UI", 14, "bold")
             ).pack(side="left")
    status = tk.Label(hdr, text="", fg="#2a7", font=("Microsoft YaHei UI", 9))
    status.pack(side="right")

    nb = ttk.Notebook(root)
    nb.pack(fill="both", expand=True, padx=10, pady=6)

    # ── 页 1：外观调节 ─────────────────────────────────────────
    p1 = tk.Frame(nb)
    nb.add(p1, text="  外观  ")
    canvas = tk.Canvas(p1, highlightthickness=0)
    sb = ttk.Scrollbar(p1, orient="vertical", command=canvas.yview)
    inner = tk.Frame(canvas)
    inner.bind("<Configure>",
               lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.configure(yscrollcommand=sb.set)
    canvas.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")

    scales = {}
    vars_bool = {}
    vars_choice = {}
    lbl_val = {}
    live = {"v": {}}          # 面板当前认为的设置值

    def push(**changes):
        write_cmd(changes)
        status.config(text="已发送 " + ", ".join(changes.keys()))
        root.after(1200, lambda: status.config(text=""))

    def make_scale(key, label, lo, hi, step, unit=""):
        row = tk.Frame(inner)
        row.pack(fill="x", padx=8, pady=3)
        tk.Label(row, text=label, width=14, anchor="w",
                 font=("Microsoft YaHei UI", 10)).pack(side="left")
        v = tk.DoubleVar(value=0)
        vals = tk.Label(row, width=8, anchor="e",
                        font=("Consolas", 10, "bold"), fg="#06c")
        vals.pack(side="right")

        def on_move(_=None):
            x = v.get()
            if step >= 1:
                x = round(x)
            else:
                x = round(x, 2)
            vals.config(text=f"{x}{unit}")
            push(**{key: x})

        s = tk.Scale(row, from_=lo, to=hi, resolution=step, orient="horizontal",
                     variable=v, showvalue=False, command=on_move,
                     length=260, highlightthickness=0)
        s.pack(side="right", padx=(0, 6))
        scales[key] = s
        lbl_val[key] = vals

    for key, label, lo, hi, step in NUM_KEYS:
        make_scale(key, label, lo, hi, step)

    tk.Frame(inner, height=8).pack()
    for key, label, opts, names in CHOICE_KEYS:
        row = tk.Frame(inner)
        row.pack(fill="x", padx=8, pady=4)
        tk.Label(row, text=label, width=14, anchor="w",
                 font=("Microsoft YaHei UI", 10)).pack(side="left")
        v = tk.StringVar(value=opts[0])
        vars_choice[key] = v
        for o in opts:
            tk.Radiobutton(row, text=names.get(o, o), value=o, variable=v,
                           font=("Microsoft YaHei UI", 9),
                           command=lambda k=key, vv=v: push(**{k: vv.get()})
                           ).pack(side="left")
        tk.Frame(inner, height=2).pack()

    tk.Frame(inner, height=8).pack()
    for key, label in BOOL_KEYS:
        v = tk.BooleanVar(value=False)
        vars_bool[key] = v
        tk.Checkbutton(inner, text=label, variable=v,
                       font=("Microsoft YaHei UI", 10),
                       command=lambda k=key, vv=v: push(**{k: vv.get()})
                       ).pack(anchor="w", padx=8, pady=2)

    tk.Frame(inner, height=10).pack()
    btns = tk.Frame(inner)
    btns.pack(fill="x", padx=8, pady=6)
    for txt, fn in (
        ("回默认位置", lambda: push(_action="recenter")),
        ("重置全部", lambda: push(_action="reset")),
        ("隐藏/显示", lambda: push(_action="toggle")),
        ("刷新一次", lambda: None),
    ):
        tk.Button(btns, text=txt, font=("Microsoft YaHei UI", 10),
                  command=fn, width=11).pack(side="left", padx=3)

    # ── 页 2：快捷键一览 ──────────────────────────────────────
    p2 = tk.Frame(nb)
    nb.add(p2, text="  快捷键  ")
    tk.Label(p2, text="字幕浮窗上的按键（点按钮=按该键；浮窗需可接收键盘）",
             font=("Microsoft YaHei UI", 9), fg="#666").pack(anchor="w", padx=10,
                                                             pady=(8, 4))
    grid = tk.Frame(p2)
    grid.pack(fill="both", expand=True, padx=10)
    for i, (k, desc) in enumerate(HOTKEYS):
        tk.Label(grid, text=k, width=14, anchor="w",
                 font=("Consolas", 10, "bold"), fg="#036").grid(
            row=i // 2, column=(i % 2) * 2, sticky="w", pady=3)
        tk.Label(grid, text=desc, anchor="w",
                 font=("Microsoft YaHei UI", 9)).grid(
            row=i // 2, column=(i % 2) * 2 + 1, sticky="w", padx=(0, 18))

    # ── 页 3：说明 ────────────────────────────────────────────
    p3 = tk.Frame(nb)
    nb.add(p3, text="  说明  ")
    txt = tk.Text(p3, wrap="word", font=("Microsoft YaHei UI", 10),
                  relief="flat", padx=10, pady=8)
    txt.pack(fill="both", expand=True)
    txt.insert("1.0",
               "这个面板是**独立进程**，关掉它字幕照常运行（挂在后台即可）。\n\n"
               "改动是即时的：面板把设置写进 control_cmd.json，字幕程序每\n"
               "300ms 读一次并立刻重绘，不需要重启。\n\n"
               "反方向：字幕程序把当前设置写进 overlay_qt.json，面板定时读\n"
               "回来刷新显示 —— 所以你在浮窗上按快捷键改了字号，面板上的\n"
               "滑块也会跟着动。\n\n"
               "排布说明：\n"
               "  同行(两列) —— 译文与原文并排，各占一列、各自居中。\n"
               "                最省高度（一行就是一行）。\n"
               "  上下两行   —— 译文一行、原文一行。\n"
               "  只译文     —— 不显示原文。\n\n"
               "对齐：默认居中。译文与原文长度不同时，只有各自居中才\n"
               "在视觉上对齐。\n\n"
               "移动字幕：拖浮窗**顶部那条横条**（鼠标移上去会变亮），\n"
               "双击它回默认位置。\n")
    txt.config(state="disabled")

    # ── 定时同步：读主程序写回的设置 ───────────────────────────
    def sync_from_app():
        s = read_json(SETTINGS, {})
        if s and s != live["v"]:
            live["v"] = dict(s)
            for key, sc in scales.items():
                if key in s:
                    try:
                        sc.set(float(s[key]))
                    except Exception:
                        pass
            for key, var in vars_bool.items():
                if key in s:
                    var.set(bool(s[key]))
            for key, var in vars_choice.items():
                if key in s:
                    var.set(str(s[key]))
            st = s.get("_app_ts")
            status.config(text="已同步")
            root.after(900, lambda: status.config(text=""))
        root.after(1000, sync_from_app)

    sync_from_app()
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
