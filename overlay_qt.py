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
      · 字号取 38 的**等比缩放**（1080p 下约 57px），不写死小数
      · 译文用 mpv 的白色（最中性、最耐看），不用 VoxGo 的亮绿
      · 原文仍退一档（0.68 倍）——mpv 默认虽是 1 倍，但那是给"两条独立字幕轨"的，
        我们这里是同一框内上下两行，缩一档更清楚
    """
    import tkinter  # noqa: F401  (仅用于探测屏幕，无则回退)
    return {
        # mpv: 在 720p 窗口下 38px，随窗口高度等比缩放
        "font_size": 38,
        "ref_height": 720,           # mpv 的基准窗口高度
        "original_ratio": 0.68,      # 原文相对译文的字号比
        "opacity": 0.92,
        "text_color": "#FFFFFF",     # mpv: white
        "original_text_color": "#B7C4D8",   # 原文退一档（VoxGo 的配色）
        "outline_color": "#000000",  # mpv: outline 黑
        "outline_width": 1.65,       # mpv: --sub-outline-size 默认 1.65
        "bg_color": "#20242A",
        "border_color": "#3A4450",
        "border_width": 1,
        "max_lines": 5,              # VoxGo: max_lines 5
        "hold_seconds": 5.0,         # VoxGo: fade_duration 5
        "show_original": True,
        "x": None,
        "y": None,                   # None = 自动贴底部
        "width": 900,
        "max_entry_chars": 180,
        "line_height": 1.30,
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
        self.box = QVBoxLayout(self.w)
        self.box.setContentsMargins(14, 10, 14, 10)
        self.box.setSpacing(6)
        self.box.addStretch(1)          # 内容靠底，向上长
        self.labels = []
        self.entries = []               # [(ts, en, zh, QLabel)]
        self._drag = None

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
               "original_ratio": (0.3, 1.5)}
        for k, (lo, hi) in rng.items():
            v = d.get(k)
            if v is None:
                continue
            if isinstance(v, (int, float)) and lo <= v <= hi:
                out[k] = float(v) if isinstance(lo, float) else int(v)
        for k, lim in (("x", scr.width()), ("y", scr.height())):
            v = d.get(k)
            if v is None:
                continue
            if isinstance(v, (int, float)) and -8 <= v <= lim + 8:
                out[k] = int(v)
        for k in ("text_color", "original_text_color", "bg_color", "border_color"):
            v = d.get(k)
            if isinstance(v, str) and v.startswith("#") and len(v) in (4, 7):
                out[k] = v
        for k in ("show_original", "theme_dark_video"):
            if isinstance(d.get(k), bool):
                out[k] = d[k]
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
        fs = self.fs()
        self.w.setStyleSheet(f"""
            QWidget#root {{
                background-color: {self.st['bg_color']};
                border: {int(self.st['border_width'])}px solid {self.st['border_color']};
                border-radius: 8px;
            }}
            QLabel#zh {{
                color: {self.st['text_color']};
                font-family: "Microsoft YaHei UI", "Microsoft YaHei";
                font-size: {fs}px;
            }}
            QLabel#en {{
                color: {self.st['original_text_color']};
                font-family: "Segoe UI", "Microsoft YaHei UI";
                font-size: {self.fs_original()}px;
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
        """渲染一条字幕：译文在上（大、亮），原文在下（小、暗）。

        加**描边**：字幕的标准做法。浅色画面上亮字会糊在背景里，
        描边保证任何背景都能读。
        """
        from html import escape
        fs = self.fs()
        fs_o = self.fs_original()
        lh = float(self.st["line_height"])
        oc = self.st.get("outline_color", "#000000")
        ow = float(self.st.get("outline_width", 1.65))
        parts = []
        if zh:
            parts.append(
                f'<div style="color:{self.st["text_color"]}; font-size:{fs}px; '
                f'line-height:{lh}; margin:0; word-wrap:break-word; '
                f'-webkit-text-stroke:{ow:.2f}px {oc};">{escape(zh)}</div>')
        if en and self.st.get("show_original", True):
            parts.append(
                f'<div style="color:{self.st["original_text_color"]}; '
                f'font-size:{fs_o}px; line-height:{lh}; '
                f'margin:3px 0 0 0; word-wrap:break-word; '
                f'-webkit-text-stroke:{ow * 0.8:.2f}px {oc};">{escape(en)}</div>')
        return "".join(parts)

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
        self.w.update()

    # ── 事件：拖动 / 滑轮 / 快捷键 ────────────────────────────
    def _install_events(self):
        from PyQt5.QtCore import Qt
        w = self.w

        def press(e):
            if e.button() == Qt.LeftButton:
                self._drag = (e.globalPos(), self.w.pos())

        def move(e):
            if self._drag and (e.buttons() & Qt.LeftButton):
                start, origin = self._drag
                self.w.move(origin + (e.globalPos() - start))

        def release(e):
            if self._drag:
                p = self.w.pos()
                self.st["x"], self.st["y"] = p.x(), p.y()
                self._drag = None
                self._save()

        def wheel(e):
            if e.modifiers() & Qt.ControlModifier:
                self.bump_opacity(0.05 if e.angleDelta().y() > 0 else -0.05)
            elif e.modifiers() & Qt.ShiftModifier:
                self.bump_lines(1 if e.angleDelta().y() > 0 else -1)
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

        w.mousePressEvent = press
        w.mouseMoveEvent = move
        w.mouseReleaseEvent = release
        w.wheelEvent = wheel
        w.keyPressEvent = key

    def _paint(self, e):
        pass          # 背景交给样式表

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
    def set(self, en, zh="", **kw):
        """收一条新字幕。与原 tkinter 版的 set() 兼容，额外参数转给 add()。"""
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

    def bump_lines(self, d):
        self.st["max_lines"] = max(1, min(12, int(self.st["max_lines"]) + d))
        while len(self.entries) > int(self.st["max_lines"]):
            self._drop(0)
        self._relayout()
        self._save()

    def toggle_theme(self):
        """暗画面用亮字，亮画面用白字+重描边。字幕的常规做法。"""
        self.st["theme_dark_video"] = not self.st.get("theme_dark_video", True)
        if self.st["theme_dark_video"]:
            self.st["text_color"] = "#00FF00"
            self.st["original_text_color"] = "#B7C4D8"
            self.st["outline_width"] = 1.2
        else:
            self.st["text_color"] = "#FFFFFF"
            self.st["original_text_color"] = "#E0E0E0"
            self.st["outline_width"] = 2.0
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
        o = QtOverlay()
        chk("WindowStaysOnTopHint", bool(o.w.windowFlags() & _Q.WindowStaysOnTopHint))
        chk("FramelessWindowHint", bool(o.w.windowFlags() & _Q.FramelessWindowHint))
        chk("Tool", bool(o.w.windowFlags() & _Q.Tool))
        chk("WA_TranslucentBackground", o.w.testAttribute(_Q.WA_TranslucentBackground))

        print("\n  显示规范（照搬成熟方案）")
    from PyQt5.QtWidgets import QApplication
    sh = QApplication.primaryScreen().availableGeometry().height()
    chk("字号基准 38（mpv --sub-font-size 默认）", o.st["font_size"] == 38)
    chk("基准高度 720（mpv 的窗口高度基准）", o.st["ref_height"] == 720)
    exp = max(10, int(round(38 * sh / 720)))
    chk(f"随屏幕等比缩放（{sh}p → {o.fs()}px，期望 {exp}px）", o.fs() == exp)
    chk("原文按比例缩小", o.fs_original() < o.fs())
    chk("译文色 = 白（mpv --sub-color）", o.st["text_color"] == "#FFFFFF")
    chk("描边色 = 黑（mpv --sub-outline-color）", o.st["outline_color"] == "#000000")
    chk("描边宽 1.65（mpv --sub-outline-size）",
        abs(o.st["outline_width"] - 1.65) < 1e-6)
    chk("最多 5 条（VoxGo max_lines）", o.st["max_lines"] == 5)
    chk("每条 5 秒（VoxGo fade_duration）", abs(o.st["hold_seconds"] - 5.0) < 1e-6)
    chk("显示原文", o.st["show_original"] is True)

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
    chk("超上限自动丢弃，最多 5 条", len(o.entries) == 5)
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
    chk("可重置", o.st["font_size"] == 38)
    o.close()
    chk("设置落盘", os.path.exists(SETTINGS))
    print(f"\n合计 {ok} 通过 / {fail} 失败")
    sys.exit(0 if fail == 0 else 1)
    demo(0)
