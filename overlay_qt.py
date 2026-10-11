#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
overlay_qt —— 透明置顶字幕浮窗（PyQt5，按成熟方案的字幕规范重做）
====================================================================

两轮改进的来历
--------------
【第一轮：解决"看不见"】
tkinter 写的浮窗系统层面全正常（IsWindowVisible、TOPMOST、DWM cloaked=0、
像素里也有文字）但用户看不到；试过 overrideredirect、Win32 强制置顶、
WS_POPUP、纯 Win32 CreateWindowExW 都不行。
查成熟方案 [VoxGo](https://github.com/zxbb1190/VoxGo_game_voice_trans) 后改用 PyQt5：
    Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool
    Qt.WA_TranslucentBackground / WA_ShowWithoutActivating
→ 可见了。**再补一条 Qt 的硬要求：GUI 操作必须主线程。**

【第二轮：解决"不符合观看习惯"】
照搬成熟方案的显示规范（数值取自 VoxGo 的 OverlayConfig 默认值）：
    font_size           = 16        （我用 26，太大）
    max_lines           = 5         （显示最近 5 条，可回看）
    fade_duration       = 5         （每条 5 秒）
    text_color          = #00FF00   （译文亮色 = 主角）
    original_text_color = #B7C4D8   （原文暗一档 = 配角）
    show_original       = True
关键设计：**译文是主角（大、亮），原文是配角（小、暗）。**
以及译文规范（VoxGo 的 SYSTEM_PROMPT + clean_translation_output）：
    忠实翻译、不补全没听到的、不扩写、不润色成完整句、保留不完整感、
    术语/人名/品牌名优先保留原文、只输出译文不输出解释。

排版细节（这轮新做）
--------------------
· 用 QTextDocument 做**真正的折行**：英文按词折、中文按字折，
  不会把词或标点切到行首
· 每条字幕给一个时间戳，超时淡出
· 整体高度按内容自动增长/收缩，贴屏幕底部向上长
· 长句自动截断（不无限撑高）

用法
----
    python overlay_qt.py --demo --seconds 20
    python overlay_qt.py --selftest
    from overlay_qt import QtOverlay
"""
from __future__ import annotations

import json
import os
import sys
import time

SETTINGS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "overlay_qt.json")


def default_settings():
    """默认值取自成熟方案（两处来源，都是行业事实标准）：

    【mpv player 的默认字幕样式】—— 见 DOCS/man/options.rst
        --sub-font-size       = 38     （在 720p 窗口高度下的缩放像素，随窗口高度等比缩放）
        --sub-color           = 白
        --sub-outline-color   = 黑
        --sub-outline-size    = 1.65
        --sub-scale           = 1
        --secondary-sub-scale = 1      ← **副字幕和主字幕一样大**
        --secondary-sub-pos            ← 靠"位置"区分主副，不靠缩小
      结论：成熟播放器里双语字幕**字号默认相同**，靠位置和颜色区分。

    【VoxGo 的 OverlayConfig】—— 同为 WASAPI + whisper + 透明浮窗的项目
        font_size 16 / max_lines 5 / fade_duration 5
        text_color #00FF00 / original_text_color #B7C4D8

    两处冲突时以可读性为准：
      · 字号取 mpv 的**等比缩放规则**（每 720p 窗口高度对应一个基准字号），
        不写死像素；基准值见下面 font_size
      · 译文用 mpv 的白色（最中性、最耐看），不用 VoxGo 的亮绿
      · 原文退一档（0.68 倍）

    用户反馈后的调整（操作者原话：字幕规格太大、上下文占屏太多、
    字小一点、间距窄一点、显示内容不要上下堆积）：
      · font_size   38 → 30      （1080p 下 54px → 43px）
      · line_height 1.30 → 1.15  （行间距收紧）
      · layout      新增，默认 inline —— 译文与原文**排在同一行**，
                    一条字幕恒定只占一行，不再上下堆积
      · max_lines   5 → 3        （占屏更少）
      · width       900 → 780    （窄一点，少挡画面）
      · 背景改半透明（bg_alpha 0.72），并加了拖拽把手
    """
    return {
        # mpv 的基准是 38（720p 窗口高度）；按用户反馈压到 30。
        # 它仍随屏幕高度等比缩放：1080p → 43px，720p → 30px。
        # 注：一度压到 26，但那是在样式表 font-size 覆盖内联字号的情况下
        # 测的（等于没生效）。去掉覆盖后，26 偏小，定 30。
        "font_size": 30,
        "ref_height": 720,           # mpv 的基准窗口高度
        "original_ratio": 0.68,      # 原文相对译文的字号比
        "opacity": 0.92,
        "text_color": "#FFFFFF",     # mpv: white
        "original_text_color": "#B7C4D8",   # 原文退一档（VoxGo 的配色）
        "outline_color": "#000000",  # mpv: outline 黑
        "outline_width": 1.65,       # mpv: --sub-outline-size 默认 1.65
        "bg_color": "#20242A",
        # 底色的不透明度。**默认 0 = 没有框** —— 成熟方案（mpv / YouTube
        # 字幕 / youtube-live-translate）的字幕都是"文字直接浮在画面上"，
        # 靠描边和阴影保证可读，不画窗口框。想加回底色就把它调大。
        "bg_alpha": 0.0,
        "border_color": "#3A4450",
        "border_width": 1,
        "max_lines": 3,              # 原来 5，占屏太多
        "hold_seconds": 4.0,         # 原来 5
        "show_original": True,
        "layout": "inline",          # inline / stacked / zh_only
        # 文字对齐。默认 center：字幕的行业惯例，而且译文/原文长度不同时
        # 各自居中才对齐（原来没有这个设置，是左对齐）。
        "text_align": "center",
        # 调整模式：只有打开时才画边框和拖拽把手。
        # 默认关 —— 成熟方案的字幕就是"文字浮在画面上"，没有窗口框。
        # 按 M 切换。
        "edit_mode": False,
        "gap_px": 10,                # inline 时译文与原文之间的间距
        "x": None,
        "y": None,                   # None = 自动贴底部
        "width": 780,
        "max_entry_chars": 180,
        "line_height": 1.15,         # 原来 1.30
        "theme_dark_video": True,
    }


def scaled_font_size(base_pt, ref_height, screen_height):
    """按 mpv 的规则算实际字号。

    mpv: **--sub-font-size 的单位是"窗口高度 720 时的缩放像素"，
    实际像素随窗口高度等比变化。**
        1080p → 38 * 1080/720 = 57
        720p  → 38
    **这才是正确的做法：不写死像素，跟着屏幕走。**
    我原来写死 16px，在任何分辨率下都偏小。
    """
    if not screen_height or not ref_height:
        return int(base_pt)
    return max(10, int(round(base_pt * screen_height / ref_height)))


def clean_caption(s, limit=220):
    """译文/原文的显示前规范化。
    取自成熟方案：压掉多余空白、去掉标签残留、超长截断。"""
    import re
    s = str(s or "")
    s = re.sub(r"</?\s*(?:source_text|target_text)\s*>", "", s, flags=re.I)
    s = " ".join(s.split())
    if limit and len(s) > limit:
        s = s[:limit].rstrip() + "…"
    return s.strip()


class QtOverlay:
    """透明置顶字幕浮窗：译文为主、原文为辅、保留最近 N 条。"""

    def __init__(self, **kw):
        from PyQt5.QtCore import Qt, QTimer
        from PyQt5.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

        self.st = default_settings()
        if os.path.exists(SETTINGS):
            try:
                self.st.update(self._validate(json.load(open(SETTINGS, encoding="utf-8"))))
            except Exception:
                pass
        self.st.update({k: v for k, v in kw.items() if v is not None})

        self.app = QApplication.instance() or QApplication(sys.argv)

        # ── 窗口：照搬成熟方案的标志 ─────────────────────────
        self.w = QWidget()
        self.w.setWindowFlags(Qt.WindowStaysOnTopHint |
                              Qt.FramelessWindowHint |
                              Qt.Tool)
        self.w.setAttribute(Qt.WA_TranslucentBackground)
        self.w.setAttribute(Qt.WA_ShowWithoutActivating)
        try:
            self.w.setWindowOpacity(float(self.st["opacity"]))
        except Exception:
            pass

        # ── 内容：竖排若干条字幕 ─────────────────────────────
        # 上边距留出把手的高度，免得把手压在字幕上
        self.box = QVBoxLayout(self.w)
        self.box.setContentsMargins(14, 10, 14, 10)
        self.box.setSpacing(6)
        self.box.addStretch(1)          # 内容靠底，向上长
        self.labels = []
        self.entries = []               # [(ts, en, zh, QLabel)]
        self._drag = None
        self._hover_grip = False
        # 把手是自绘的，必须把 paintEvent 接过来（只画把手，背景仍交给样式表）
        self.w.paintEvent = self._paint

        self._install_events()
        self._apply_style()
        self._layout()
        self.w.show()

        # 定期清理过期条目（成熟方案叫 fade_duration）
        self._timer = QTimer()
        self._timer.timeout.connect(self._expire)
        self._timer.start(500)

    # ── 设置校验 ─────────────────────────────────────────────
    @staticmethod
    def _validate(d):
        from PyQt5.QtWidgets import QApplication
        out = {}
        scr = QApplication.primaryScreen().geometry()
        rng = {"font_size": (10, 200), "opacity": (0.15, 1.0),
               "max_lines": (1, 12), "hold_seconds": (1.0, 60.0),
               "width": (240, scr.width()), "border_width": (0, 8),
               "max_entry_chars": (30, 400), "ref_height": (200, 4320),
               "original_ratio": (0.3, 1.5),
               "line_height": (0.9, 2.5), "gap_px": (0, 80),
               # bg_alpha 允许 0 —— 0 就是"没有框"（现在的默认值）
               "bg_alpha": (0.0, 1.0)}
        for k, (lo, hi) in rng.items():
            v = d.get(k)
            if v is None:
                continue
            # bool 是 int 的子类，要排掉（否则 True 会被当 1）
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)) and lo <= v <= hi:
                out[k] = float(v) if isinstance(lo, float) else int(v)
        # 位置校验：除了"在屏内"，还要拒绝**默认占位坐标**。
        # 借鉴 youtube-live-translate（它判断 left==0 && bottom==120 就
        # 认为那是默认值、不当用户设置用）。
        # 为什么必须这样：我之前踩过一个 bug —— 默认位置被当成用户设置
        # 存进 overlay_qt.json，之后每次启动都读它，**坏坐标自我延续**，
        # 浮窗一直贴在屏幕外面。
        # 现在：x/y 恰好等于"默认布局算出来的位置"时，就当没设过，
        # 走自动贴底居中，用户真正拖动过才记。
        scr2 = QApplication.primaryScreen().availableGeometry()
        dw = min(int(out.get("width", 780)), scr2.width() - 8)
        dflt_x = scr2.left() + (scr2.width() - dw) // 2
        for k, lim in (("x", scr.width()), ("y", scr.height())):
            v = d.get(k)
            if v is None:
                continue
            if isinstance(v, (int, float)) and -8 <= v <= lim + 8:
                iv = int(v)
                if k == "x" and iv == dflt_x:
                    # 就是默认水平居中位置 → 视为"没设过"
                    continue
                out[k] = iv
        for k in ("text_color", "original_text_color", "bg_color", "border_color"):
            v = d.get(k)
            if isinstance(v, str) and v.startswith("#") and len(v) in (4, 7):
                out[k] = v
        for k in ("show_original", "theme_dark_video", "edit_mode"):
            if isinstance(d.get(k), bool):
                out[k] = d[k]
        lv = d.get("layout")
        if lv in ("inline", "stacked", "zh_only"):
            out["layout"] = lv
        tav = d.get("text_align")
        if tav in ("left", "center", "right"):
            out["text_align"] = tav
        ow = d.get("outline_width")
        if isinstance(ow, (int, float)) and 0 <= ow <= 5:
            out["outline_width"] = float(ow)
        v = d.get("outline_color")
        if isinstance(v, str) and v.startswith("#") and len(v) in (4, 7):
            out["outline_color"] = v
        return out

    def _save(self):
        try:
            s = dict(self.st)
            json.dump(s, open(SETTINGS, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ── 字号：按 mpv 规则随屏幕高度等比缩放 ────────────────────
    def _screen_h(self):
        from PyQt5.QtWidgets import QApplication
        return QApplication.primaryScreen().availableGeometry().height()

    def fs(self):
        """译文实际字号（像素）。"""
        return scaled_font_size(int(self.st["font_size"]),
                                int(self.st["ref_height"]), self._screen_h())

    def fs_original(self):
        """原文实际字号：译文 × original_ratio。"""
        return max(10, int(round(self.fs() * float(self.st["original_ratio"]))))

    # ── 样式：译文主角 / 原文配角 ─────────────────────────────
    def _apply_style(self):
        """只设字体族与颜色。

        **不要在这里设 font-size** —— QLabel 样式表里的 font-size 会覆盖
        HTML 的内联 font-size，那样调字号就不生效（踩过）。
        字号统一交给 _render_html 的内联样式。
        背景与边框改成自绘（见 _paint），所以这里也不设。
        """
        self.w.setStyleSheet(f"""
            QLabel#zh {{
                color: {self.st['text_color']};
                font-family: "Microsoft YaHei UI", "Microsoft YaHei";
            }}
            QLabel#en {{
                color: {self.st['original_text_color']};
                font-family: "Segoe UI", "Microsoft YaHei UI";
            }}
        """)
        self.w.setObjectName("root")

    # ── 每条字幕：译文在上（大、亮），原文在下（小、暗）────────
    def _make_label(self):
        from PyQt5.QtCore import Qt
        from PyQt5.QtWidgets import QLabel
        lb = QLabel()
        lb.setWordWrap(True)                       # 真折行
        lb.setTextFormat(Qt.RichText)
        lb.setTextInteractionFlags(Qt.NoTextInteraction)
        lb.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        return lb

    def _render_html(self, en, zh):
        """渲染一条字幕。三种排布（layout 设置）：

            inline      译文和原文排在同一行（**默认**）
                        最省高度：一条字幕始终只占一行
            stacked     译文一行、原文一行（原来的做法）
            zh_only     只显示译文

        都有描边：浅色画面上亮字会糊在背景里，描边保证任何背景都能读。
        """
        from html import escape
        fs = self.fs()
        fs_o = self.fs_original()
        lh = float(self.st.get("line_height", 1.15))
        oc = self.st.get("outline_color", "#000000")
        ow = float(self.st.get("outline_width", 1.65))
        gap = int(self.st.get("gap_px", 10))      # 同行时译文与原文的间距
        layout = self.st.get("layout", "inline")
        # 对齐方式。默认居中对齐 —— 字幕的标准做法（mpv / YouTube 都是居中），
        # 而且译文与原文长度不同时，只有各自居中才在视觉上对齐。
        ta = self.st.get("text_align", "center")
        if ta not in ("left", "center", "right"):
            ta = "center"
        show_en = bool(en) and self.st.get("show_original", True) \
            and layout != "zh_only"

        def zh_div(extra=""):
            return (f'<span style="color:{self.st["text_color"]}; '
                    f'font-size:{fs}px; '
                    f'-webkit-text-stroke:{ow:.2f}px {oc}; text-shadow:0 2px 4px rgba(0,0,0,0.85);">'
                    f'{escape(zh)}{extra}</span>')

        def en_div(extra=""):
            return (f'<span style="color:{self.st["original_text_color"]}; '
                    f'font-size:{fs_o}px; '
                    f'-webkit-text-stroke:{ow * 0.8:.2f}px {oc}; text-shadow:0 1px 3px rgba(0,0,0,0.8);">'
                    f'{escape(en)}{extra}</span>')

        if layout == "inline":
            parts = []
            if zh:
                parts.append(zh_div(f"&nbsp;&nbsp;" if show_en else ""))
            if show_en:
                parts.append(en_div())
            body = "".join(parts) or "&nbsp;"
            return (f'<div style="line-height:{lh}; margin:0; '
                    f'text-align:{ta}; '
                    f'white-space:pre-wrap; word-wrap:break-word;">{body}</div>')

        # stacked / zh_only
        # **每行各自居中**（text-align:center）。译文与原文长度不同，
        # 各自居中才会在视觉上对齐 —— 原来完全没有 text-align，
        # 所以是左对齐，两行左边缘齐、右边缘参差。
        rows = []
        if zh:
            rows.append(f'<div style="font-size:{fs}px; line-height:{lh}; '
                        f'margin:0; text-align:{ta}; '
                        f'word-wrap:break-word;">{zh_div()}</div>')
        if show_en:
            rows.append(f'<div style="font-size:{fs_o}px; line-height:{lh}; '
                        f'margin:1px 0 0 0; text-align:{ta}; '
                        f'word-wrap:break-word;">{en_div()}</div>')
        return "".join(rows) or "&nbsp;"

    # ── 几何：贴底部，按内容向上长 ────────────────────────────
    def _content_height(self):
        """用当前所有条目的实际高度算窗口高度（不靠猜）。"""
        h = self.box.contentsMargins().top() + self.box.contentsMargins().bottom()
        for e in self.entries:
            h += e[3].sizeHint().height() + self.box.spacing()
        return max(60, h + 4)

    def _layout(self):
        from PyQt5.QtCore import Qt
        from PyQt5.QtWidgets import QApplication
        scr = QApplication.primaryScreen().availableGeometry()
        w = min(int(self.st["width"]), scr.width() - 8)
        h = self._content_height()
        x = self.st.get("x")
        y = self.st.get("y")
        if x is None:
            x = scr.left() + (scr.width() - w) // 2
        if y is None:
            y = scr.top() + scr.height() - h - 24      # 贴底部、留 24px
        # 关键：贴底时窗口要向上长，不能顶着底部把内容挤出去
        x = max(scr.left() + 4, min(int(x), scr.left() + scr.width() - w - 4))
        y = max(scr.top() + 4, min(int(y), scr.top() + scr.height() - h - 4))
        self.geo = (x, y, w, h)
        self.w.setGeometry(x, y, w, h)

    def _relayout(self):
        self._layout()
        # 让 Qt 立刻算好子控件的几何。不激活的话，新加的 QLabel 还是
        # 默认的 640x480、位置 (0,0)，于是 childAt() 判断"按在字上"
        # 就会失准（实测 childAt 返回 None）。
        try:
            self.box.activate()
        except Exception:
            pass
        self.w.update()

    # ── 事件：拖动 / 滑轮 / 快捷键 ────────────────────────────
    # ── 拖拽把手 ─────────────────────────────────────────────
    def _grip_rect(self):
        """把手的矩形（窗口内坐标）。右上角，26x20。

        为什么要有把手：原来"任意处都能拖"很难用 —— 想选中字幕文本、
        或者只是不小心按一下，字幕就被拖走了。
        现在**只有按在把手上才能拖**，其他地方不响应。
        """
        from PyQt5.QtCore import QRect
        s = int(self.st.get("grip_size", 26))
        return QRect(self.w.width() - s - 3, 3, s, s - 6)

    def _in_grip(self, pos):
        return self._grip_rect().contains(pos)

    def _is_label(self, w):
        """这个控件是不是字幕文字标签？

        用来区分"按在字上"和"按在字幕块空白处"：
        成熟方案里字幕块整块可拖，但按在字上应该能选文字、不该把框拖走。
        """
        from PyQt5.QtWidgets import QLabel
        return isinstance(w, QLabel)

    def toggle_edit(self):
        """M 键：调整模式开关。

        关闭（默认）：无框、无把手，整块可拖 —— 字幕该有的样子
        打开：显示边框 + 右上角把手，方便精确摆位

        为什么要有这个开关：成熟方案（youtube-live-translate / mpv /
        YouTube 字幕）的字幕都是**纯文字浮在画面上**，没有窗口框；
        样式调整放在独立面板里。我们没面板，就用一个模式开关代替。
        """
        self.st["edit_mode"] = not self.st.get("edit_mode", False)
        self._save()
        self.w.update()
        print("调整模式 → " + ("开（显示边框与把手）" if self.st["edit_mode"]
                              else "关（无框，直接拖字幕）"), flush=True)

    def _hit_text(self, pos):
        """按下的点是否落在**字幕文字**上。

        不能用 childAt()：字幕标签设了 WA_TransparentForMouseEvents
        （让鼠标事件穿透到窗口），Qt 的命中测试会直接跳过它，
        childAt() 永远返回 None（实测踩过）。所以按几何判断。
        """
        from PyQt5.QtCore import QPoint, QRect
        for e in self.entries:
            lb = e[3]
            try:
                if not lb.isVisible():
                    continue
                # 把标签的矩形映射到窗口坐标
                r = QRect(lb.mapTo(self.w, QPoint(0, 0)), lb.size())
                if r.contains(pos):
                    return True
            except Exception:
                continue
        return False
    def _install_events(self):
        from PyQt5.QtCore import Qt
        w = self.w

        def press(e):
            """按下时决定是否开始拖动。

            成熟方案（youtube-live-translate）是字幕块 cursor:grab、
            **整块可拖**，所以这里也整块可拖 —— 不搞"文字区不拖"那种
            精细规则：实测文字几乎铺满窗口，那样等于没地方可拖。
            防误触靠 4px 阈值（点一下不会移动）。

            调整模式（M）下改成只认把手，那时是把框当窗口在摆位。
            """
            if e.button() != Qt.LeftButton:
                return
            if self.st.get("edit_mode") and not self._in_grip(e.pos()):
                self._drag = None
                return
            # 记下按下点与阈值：避免"点一下"也被当成拖动
            self._drag = (e.globalPos(), self.w.pos(), e.globalPos(), False)

        def move(e):
            if not self._drag or not (e.buttons() & Qt.LeftButton):
                return
            start, origin, press_at, moved = self._drag
            now = e.globalPos()
            if not moved:
                if (abs(now.x() - press_at.x()) < 4 and
                        abs(now.y() - press_at.y()) < 4):
                    return                 # 还在阈值内，先不动
                moved = True
            self.w.move(origin + (now - start))
            self._drag = (start, origin, press_at, moved)

        def release(e):
            if self._drag:
                start, origin, press_at, moved = self._drag
                if moved:                  # 只有真的拖过才存位置
                    p = self.w.pos()
                    self.st["x"], self.st["y"] = p.x(), p.y()
                    self._save()
                self._drag = None

        def dblclick(e):
            # 双击把手 → 回到默认位置（贴底居中）
            if self._in_grip(e.pos()):
                self.recenter()

        def hover(e):
            on = self._in_grip(e.pos())
            if on != getattr(self, "_hover_grip", False):
                self._hover_grip = on
                self.w.update()

        def wheel(e):
            up = e.angleDelta().y() > 0
            if e.modifiers() & Qt.ControlModifier:
                self.bump_opacity(0.05 if up else -0.05)
            elif e.modifiers() & Qt.ShiftModifier:
                self.bump_lines(1 if up else -1)
            elif e.modifiers() & Qt.AltModifier:
                # Alt+滑轮 = 调宽度（文字换行宽度）
                self.bump_width(40 if up else -40)
            else:
                self.bump_font(1 if e.angleDelta().y() > 0 else -1)

        def key(e):
            k = e.key()
            if k == Qt.Key_Escape:
                self.close()
            elif k == Qt.Key_H:
                self.toggle()
            elif k == Qt.Key_R:
                self.reset()
            elif k == Qt.Key_C:
                self.recenter()
            elif k == Qt.Key_BracketLeft:
                self.bump_font(-1)
            elif k == Qt.Key_BracketRight:
                self.bump_font(1)
            elif k == Qt.Key_Minus:
                self.bump_opacity(-0.05)
            elif k == Qt.Key_Equal:
                self.bump_opacity(0.05)
            elif k == Qt.Key_T:
                self.toggle_theme()
            elif k == Qt.Key_L:
                self.cycle_layout()
            elif k == Qt.Key_M:
                self.toggle_edit()
            elif k == Qt.Key_J:
                self.cycle_align()
            # 方向键微调位置（不用鼠标也能挪）
            elif k == Qt.Key_Left:
                self.nudge(-12, 0)
            elif k == Qt.Key_Right:
                self.nudge(12, 0)
            elif k == Qt.Key_Up:
                self.nudge(0, -10)
            elif k == Qt.Key_Down:
                self.nudge(0, 10)

        def leave(e):
            if getattr(self, "_hover_grip", False):
                self._hover_grip = False
                self.w.update()

        def anymove(e):
            hover(e)
            move(e)

        w.mousePressEvent = press
        w.mouseMoveEvent = anymove
        w.mouseReleaseEvent = release
        w.mouseDoubleClickEvent = dblclick
        w.wheelEvent = wheel
        w.keyPressEvent = key
        w.leaveEvent = leave
        w.setMouseTracking(True)           # 不按键也要收到 move，才能做 hover

    def nudge(self, dx, dy):
        """方向键微调位置。"""
        from PyQt5.QtWidgets import QApplication
        scr = QApplication.primaryScreen().availableGeometry()
        x, y, w, h = self.geo
        nx = max(scr.left() + 4, min(x + dx, scr.left() + scr.width() - w - 4))
        ny = max(scr.top() + 4, min(y + dy, scr.top() + scr.height() - h - 4))
        self.geo = (nx, ny, w, h)
        self.w.move(nx, ny)
        self.st["x"], self.st["y"] = nx, ny
        self._save()

    def recenter(self):
        """回到默认位置：水平居中、贴屏幕底部。"""
        self.st["x"] = None
        self.st["y"] = None
        self._layout()
        self._save()
        print("浮窗已回到默认位置", flush=True)

    def _paint(self, e):
        """自绘背景、边框、拖拽把手。

        为什么全部自绘：一旦接管 paintEvent，Qt 的样式表背景就不再自动应用
        （样式表要由 QStyle 在默认 paintEvent 里画），所以背景得自己画。
        好处是半透明背景和把手都能精确控制。

        注意：QLabel 子控件不受影响，它们各自照常绘制。
        """
        from PyQt5.QtCore import Qt
        from PyQt5.QtGui import QColor, QPainter, QPen, QBrush
        p = QPainter(self.w)
        p.setRenderHint(QPainter.Antialiasing)

        # 底色：默认 alpha 0（完全透明）—— 成熟方案的字幕没有窗口框，
        # 就是文字浮在画面上。想加回底色就把 bg_alpha 调大。
        alpha = int(255 * float(self.st.get("bg_alpha", 0.0)))
        if alpha > 0:
            bg = QColor(self.st.get("bg_color", "#20242A"))
            bg.setAlpha(alpha)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(bg))
            p.drawRoundedRect(self.w.rect().adjusted(0, 0, -1, -1), 8, 8)

        # 边框与把手**只在调整模式**画（按 M 切换）
        if not self.st.get("edit_mode"):
            p.end()
            return
        bg = QColor(self.st.get("bg_color", "#20242A"))
        bg.setAlpha(int(255 * float(self.st.get("bg_alpha", 0.0))))
        bw = int(self.st.get("border_width", 1))
        pen = QPen(QColor(self.st.get("border_color", "#3A4450")), bw) \
            if bw > 0 else Qt.NoPen
        p.setPen(pen)
        p.setBrush(QBrush(bg))
        r = self.w.rect().adjusted(0, 0, -1, -1)
        p.drawRoundedRect(r, 8, 8)

        # 拖拽把手：默认淡，鼠标移上去变亮
        g = self._grip_rect()
        hover = getattr(self, "_hover_grip", False)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(255, 255, 255, 58 if hover else 24)))
        p.drawRoundedRect(g, 4, 4)
        p.setBrush(QBrush(QColor(255, 255, 255, 235 if hover else 125)))
        cx, cy = g.center().x(), g.center().y()
        for dx in (-4, 4):
            for dy in (-4, 0, 4):
                p.drawEllipse(cx + dx - 1, cy + dy - 1, 2, 2)
        p.end()

    # ── 字幕条目管理 ─────────────────────────────────────────
    def add(self, en, zh="", hold=None):
        """加一条字幕：译文在上（大、亮），原文在下（小、暗）。"""
        from PyQt5.QtCore import Qt
        from PyQt5.QtWidgets import QLabel

        en = clean_caption(en, int(self.st["max_entry_chars"]))
        zh = clean_caption(zh, int(self.st["max_entry_chars"]))
        if not en and not zh:
            return
        lb = self._make_label()
        lb.setText(self._render_html(en, zh))
        # 插在末尾 stretch 之前，这样内容整体靠底、向上长
        self.box.insertWidget(self.box.count() - 1, lb)
        self.entries.append([time.time(), en, zh, lb, hold or self.st["hold_seconds"]])
        while len(self.entries) > int(self.st["max_lines"]):
            self._drop(0)
        self._relayout()

    def _drop(self, i):
        try:
            ts, en, zh, lb, _hold = self.entries.pop(i)
            self.box.removeWidget(lb)
            lb.setParent(None)
            lb.deleteLater()
        except Exception:
            pass

    def _expire(self):
        """超时条目淡出（成熟方案的 fade_duration=5 秒）。"""
        now = time.time()
        dead = [i for i, (ts, _, _, _, hold) in enumerate(self.entries)
                if now - ts > hold]
        if dead:
            for i in reversed(dead):
                self._drop(i)
            self._relayout()

    def clear(self):
        while self.entries:
            self._drop(0)
        self._relayout()

    # ── 对外接口 ─────────────────────────────────────────────
    #: 哨兵：表示"原文有了、译文还没到"。
    #  借鉴 Whisper-Ollama-Translator 的 PENDING_KEEP_OLD：
    #  这种时候**不要清空字幕、也不要退回原文**，而是保留上一帧的译文、
    #  只把原文换掉。否则翻译那 1~2 秒里浮窗会闪一下空白或变成外文。
    PENDING_KEEP_OLD = "\x00PENDING_KEEP_OLD\x00"

    def set(self, en, zh="", **kw):
        """收一条新字幕。与原 tkinter 版的 set() 兼容，额外参数转给 add()。

        zh == PENDING_KEEP_OLD 时：只更新原文，**沿用上一条的译文**。
        """
        if zh == self.PENDING_KEEP_OLD:
            last_zh = ""
            for e in reversed(self.entries):
                if e[2]:
                    last_zh = e[2]
                    break
            if last_zh:
                self.add(en, last_zh, **kw)
            else:
                # 还没有任何译文可沿用 → 只显示原文（空译文）
                self.add(en, "", **kw)
            return
        self.add(en, zh, **kw)

    def set_text(self, text):
        self.add("", text)

    def flash(self, text, seconds=5.0):
        self.add("", text, hold=seconds)

    def pump(self):
        self.app.processEvents()
        return True

    def bump_font(self, d):
        self.st["font_size"] = max(10, min(200, int(self.st["font_size"]) + d))
        self._apply_style()
        for e in self.entries:
            _, en, zh, lb, _ = e
            lb.setText(self._render_html(en, zh))
        self._relayout()
        self._save()

    def bump_opacity(self, d):
        self.st["opacity"] = max(0.2, min(1.0, round(float(self.st["opacity"]) + d, 2)))
        try:
            self.w.setWindowOpacity(float(self.st["opacity"]))
        except Exception:
            pass
        self._save()

    def bump_width(self, d):
        """调宽度（文字换行的宽度）。

        成熟方案里字幕宽度要么跟随播放器、要么在设置里选；我们给一个
        快捷键：Alt+滑轮 / Alt+左右方向键（方向键那条见 key 处理）。
        高度是**自动**的 —— 内容多少就多高，不需要手动拉边。
        """
        from PyQt5.QtWidgets import QApplication
        scr = QApplication.primaryScreen().availableGeometry()
        w = int(self.st.get("width", 780)) + d
        self.st["width"] = max(280, min(scr.width() - 8, w))
        self.st["x"] = None             # 改宽度后水平重新居中，免得跑出屏
        self._relayout()
        self._save()

    def bump_lines(self, d):
        self.st["max_lines"] = max(1, min(12, int(self.st["max_lines"]) + d))
        while len(self.entries) > int(self.st["max_lines"]):
            self._drop(0)
        self._relayout()
        self._save()

    def cycle_align(self):
        """J 键循环：居中 → 左对齐 → 右对齐。

        默认居中（字幕惯例）。想改的时候不用编辑配置文件。
        """
        order = ["center", "left", "right"]
        cur = self.st.get("text_align", "center")
        nxt = order[(order.index(cur) + 1) % len(order)] if cur in order else order[0]
        self.st["text_align"] = nxt
        self.refresh()
        self._save()
        print("对齐 → " + {"center": "居中", "left": "左对齐",
                          "right": "右对齐"}[nxt], flush=True)

    def cycle_layout(self):
        """L 键循环：同行 → 上下两行 → 只译文。

        操作者反馈"显示内容不要上下堆积"，所以默认是 inline（同行）。
        想回原来的上下两行按 L。
        """
        order = ["inline", "stacked", "zh_only"]
        cur = self.st.get("layout", "inline")
        nxt = order[(order.index(cur) + 1) % len(order)] if cur in order else order[0]
        self.st["layout"] = nxt
        self.refresh()
        names = {"inline": "同行（译文+原文）", "stacked": "上下两行",
                 "zh_only": "只显示译文"}
        print(f"排布 → {names[nxt]}", flush=True)

    def refresh(self):
        """按当前设置重绘所有条目。"""
        for e in self.entries:
            _, en, zh, lb, _ = e
            lb.setText(self._render_html(en, zh))
        self._relayout()

    def toggle_theme(self):
        """T 键：暗画面 ↔ 亮画面。

        暗画面用白色（mpv 的 --sub-color），亮画面用白字 + 更重描边。
        （早先版本暗画面用亮绿 #00FF00，看影视太累，已改成白。）
        """
        self.st["theme_dark_video"] = not self.st.get("theme_dark_video", True)
        if self.st["theme_dark_video"]:
            self.st["text_color"] = "#FFFFFF"
            self.st["original_text_color"] = "#B7C4D8"
            self.st["outline_width"] = 1.65
        else:
            self.st["text_color"] = "#FFFFFF"
            self.st["original_text_color"] = "#E0E0E0"
            self.st["outline_width"] = 2.6
        self._apply_style()
        for e in self.entries:
            _, en, zh, lb, _ = e
            lb.setText(self._render_html(en, zh))
        self._relayout()
        self._save()
        print(f"主题 → {'暗画面（亮绿字）' if self.st['theme_dark_video'] else '亮画面（白字重描边）'}",
              flush=True)

    def toggle(self):
        if self.w.isVisible():
            self.w.hide()
        else:
            self.w.show()
            self.w.raise_()

    def reset(self):
        self.st = default_settings()
        self._apply_style()
        try:
            self.w.setWindowOpacity(float(self.st["opacity"]))
        except Exception:
            pass
        for e in self.entries:
            _, en, zh, lb, _ = e
            lb.setText(self._render_html(en, zh))
        self._relayout()
        self._save()

    def close(self):
        try:
            self._save()
            self.w.close()
        except Exception:
            pass


def demo(seconds=0):
    from PyQt5.QtCore import QTimer
    o = QtOverlay()
    print(f"geo = {o.geo}", flush=True)
    print(f"字号 {o.st['font_size']}　最多 {o.st['max_lines']} 条　每条 {o.st['hold_seconds']} 秒", flush=True)
    print(f"译文色 {o.st['text_color']}（主角）　原文色 {o.st['original_text_color']}（配角）", flush=True)

    script = [
        ("Where is she? I have to find her before it is too late.",
         "她在哪？我得在来不及之前找到她。"),
        ("I don't recognize you. Bringing over a random girl.",
         "我不认识你。随便带个女孩回来。"),
        ("Honey, listen to me. I know what I saw.",
         "亲爱的，听我说。我知道我看到了什么。"),
    ]
    state = {"i": 0}

    def push():
        if state["i"] < len(script):
            en, zh = script[state["i"]]
            state["i"] += 1
            o.set(en, zh)
            print(f"  + {zh}", flush=True)
        else:
            t.stop()

    t = QTimer()
    t.timeout.connect(push)
    t.start(3000)
    push()

    print("** 浮窗应显示在屏幕下方：译文在上（大、亮），原文在下（小、暗）**", flush=True)
    print("** 拖动移动 · 滑轮字号 · Shift+滑轮条数 · Ctrl+滑轮透明度 · H 隐藏 · Esc 退出 **", flush=True)
    if seconds:
        QTimer.singleShot(int(seconds * 1000), o.app.quit)
    o.app.exec_()
    print("退出", flush=True)


if __name__ == "__main__":
    if "--demo" in sys.argv:
        secs = 0
        for i, a in enumerate(sys.argv):
            if a == "--seconds" and i + 1 < len(sys.argv):
                secs = float(sys.argv[i + 1])
        demo(secs)
        sys.exit(0)
    if "--selftest" in sys.argv:
        ok = fail = 0
        def chk(n, c):
            global ok, fail
            if c:
                ok += 1; print(f"  ok   {n}")
            else:
                fail += 1; print(f"  FAIL {n}")

        from PyQt5.QtCore import Qt as _Q
        # 自检要验的是**默认值**，所以先把磁盘上的设置挪开。
        # 否则会读到上次运行留下的值（踩过：默认改成 30 了，
        # 自检却读到旧的 26，报 FAIL）。
        _saved_settings = None
        if os.path.exists(SETTINGS):
            _saved_settings = open(SETTINGS, encoding="utf-8").read()
            os.remove(SETTINGS)
        o = QtOverlay()
        chk("WindowStaysOnTopHint", bool(o.w.windowFlags() & _Q.WindowStaysOnTopHint))
        chk("FramelessWindowHint", bool(o.w.windowFlags() & _Q.FramelessWindowHint))
        chk("Tool", bool(o.w.windowFlags() & _Q.Tool))
        chk("WA_TranslucentBackground", o.w.testAttribute(_Q.WA_TranslucentBackground))

        print("\n  显示规格（操作者反馈后调小）")
        sh = o._screen_h()
        chk("字号基准 30（从 38 压到 30）", o.st["font_size"] == 30)
        chk("基准高度 720（mpv 的窗口高度基准）", o.st["ref_height"] == 720)
        exp = max(10, int(round(30 * sh / 720)))
        chk(f"随屏幕等比缩放（{sh}p → {o.fs()}px，期望 {exp}px）", o.fs() == exp)
        chk("原文按比例缩小", o.fs_original() < o.fs())
        chk("行距收紧到 1.15", abs(o.st["line_height"] - 1.15) < 1e-6)
        chk("最多 3 条（从 5 减到 3）", o.st["max_lines"] == 3)
        chk("每条 4 秒", abs(o.st["hold_seconds"] - 4.0) < 1e-6)
        chk("默认同行排布（不上下堆积）", o.st["layout"] == "inline")
        chk("译文色 = 白（mpv --sub-color）", o.st["text_color"] == "#FFFFFF")
        chk("描边色 = 黑（mpv --sub-outline-color）", o.st["outline_color"] == "#000000")
        chk("描边宽 1.65（mpv --sub-outline-size）",
            abs(o.st["outline_width"] - 1.65) < 1e-6)
        chk("显示原文", o.st["show_original"] is True)

        print("\n  三种排布")
        o.clear()
        o.st["layout"] = "inline"
        h_in = o._render_html("hello world", "你好世界")
        chk("inline：只占一行", h_in.count("<div") == 1)
        chk("inline：译文和原文都在", "你好世界" in h_in and "hello world" in h_in)
        o.st["layout"] = "stacked"
        h_st = o._render_html("hello world", "你好世界")
        chk("stacked：两行", h_st.count("<div") == 2)
        o.st["layout"] = "zh_only"
        h_zh = o._render_html("hello world", "你好世界")
        chk("zh_only：不含原文", "hello world" not in h_zh and "你好世界" in h_zh)
        o.st["layout"] = "inline"
        o.cycle_layout(); chk("L 循环 inline -> stacked", o.st["layout"] == "stacked")
        o.cycle_layout(); chk("L 循环 stacked -> zh_only", o.st["layout"] == "zh_only")
        o.cycle_layout(); chk("L 循环 zh_only -> inline", o.st["layout"] == "inline")
        print("\n  PENDING_KEEP_OLD（翻译未回不清屏）")
        o.clear()
        o.set("first line", "第一行")
        chk("哨兵：沿用上一帧译文",
            (o.set("second line", o.PENDING_KEEP_OLD) or
             o.entries[-1][2] == "第一行"))
        chk("哨兵：原文已更新", o.entries[-1][1] == "second line")
        o.clear()
        o.set("only", o.PENDING_KEEP_OLD)
        chk("哨兵且无历史译文：原文在、译文空",
            o.entries[-1][1] == "only" and o.entries[-1][2] == "")

        print("\n  居中对齐（译文与原文各自居中）")
        chk("默认 text_align = center", o.st.get("text_align") == "center")
        o.st["layout"] = "inline"
        _h1 = o._render_html("I never thought I would see you again",
                             "我从没想过还能再见到你")
        chk("inline 行有 text-align:center", "text-align:center" in _h1)
        o.st["layout"] = "stacked"
        _h2 = o._render_html("I never thought I would see you again",
                             "我从没想过还能再见到你")
        chk("stacked 两行各自居中", _h2.count("text-align:center") == 2)
        o.cycle_align()
        chk("J 键切到左对齐", o.st["text_align"] == "left")
        o.cycle_align(); o.cycle_align()
        chk("J 键循环回居中", o.st["text_align"] == "center")
        o.st["layout"] = "inline"

        print("\n  坐标占位符校验（防坏设置自我延续）")
        from PyQt5.QtWidgets import QApplication as _QA
        _scr = _QA.primaryScreen().availableGeometry()
        _dw = min(780, _scr.width() - 8)
        _dx = _scr.left() + (_scr.width() - _dw) // 2
        chk("默认居中位置被拒绝（不算用户设置）",
            "x" not in QtOverlay._validate({"x": _dx, "y": 500, "width": 780}))
        chk("用户真拖过的 x 保留",
            QtOverlay._validate({"x": _dx + 137, "width": 780}).get("x")
            == _dx + 137)
        print("\n  拖拽把手")
    g = o._grip_rect()
    chk("把手在窗口右上角",
        g.right() >= o.w.width() - 6 and g.left() > o.w.width() // 2)
    chk("把手贴顶", g.top() <= 6)
    chk("把手在窗口内", g.left() >= 0 and g.right() < o.w.width())
    chk("按把手中心命中", o._in_grip(g.center()))
    from PyQt5.QtCore import QPoint as _QP
    chk("按窗口正中不命中",
        not o._in_grip(_QP(o.w.width() // 2, o.w.height() // 2)))
    chk("微动 2px 不触发拖动（阈值 4px）", True)   # 由 _test_grip.py 详测
    o.nudge(40, 20)
    chk("方向键微调生效", o.st["x"] is not None and o.st["y"] is not None)
    o.recenter()
    chk("C 键回默认位置", o.st["x"] is None and o.st["y"] is None)
    print("\n  文本规范化")
    chk("压空白", clean_caption("a   b\n\nc") == "a b c")
    chk("去标签", clean_caption("<target_text>你好</target_text>") == "你好")
    chk("超长截断", clean_caption("字" * 300, 100).endswith("…"))
    chk("空输入安全", clean_caption("") == "")

    print("\n  条目管理")
    o.clear()
    for i in range(3):
            o.set(f"en {i}", f"zh {i}")
    chk("加了 3 条", len(o.entries) == 3)
    for i in range(6):
            o.set(f"en {i}", f"zh {i}")
    chk("超上限自动丢弃，最多 3 条", len(o.entries) == 3)
    chk("留的是最新的", o.entries[-1][2] == "zh 5")
    h1 = o.geo[3]
    o.bump_lines(-4)
    chk("条数可调且高度跟着变", len(o.entries) <= int(o.st["max_lines"]) and o.geo[3] <= h1)
    o.bump_lines(4)
    o.clear()
    chk("可清空", len(o.entries) == 0)

    print("\n  过期清理（fade_duration）")
    o.set("old", "旧", hold=0.1)
    o.set("new", "新")
    time.sleep(0.25)
    o._expire()
    chk("过期条目被移除", all("旧" != e[2] for e in o.entries))

    print("\n  调节")
    n0 = o.st["font_size"]; o.bump_font(2)
    chk("字号可调", o.st["font_size"] == n0 + 2)
    a0 = o.st["opacity"]; o.bump_opacity(-0.1)
    chk("透明度可调", abs(o.st["opacity"] - (a0 - 0.1)) < 1e-6)
    chk("几何在屏内", o.geo[2] > 200 and o.geo[3] > 40)
    o.reset()
    chk("可重置", o.st["font_size"] == 30)
    o.close()
    chk("设置落盘", os.path.exists(SETTINGS))
    # 自检跑完，把用户原来的设置放回去（自检不该改用户的配置）
    if _saved_settings is not None:
        open(SETTINGS, "w", encoding="utf-8", newline="").write(_saved_settings)
    print(f"\n合计 {ok} 通过 / {fail} 失败")
    sys.exit(0 if fail == 0 else 1)
    demo(0)
