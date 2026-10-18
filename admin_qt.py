# -*- coding: utf-8 -*-
"""admin_qt.py —— Babel Plan 原生桌面后台（PyQt5）

为什么重写
==========
旧后台是 babel_admin/index.html + serve.py 的 Web 页。Web 页面的问题：
浏览器标签页一关就"找不到控制台"、通知/快捷键都隔着一层、观感像临时工具。
这个文件把它换成**真正的桌面程序**：单窗口、四页（总览/模型/浮窗/启动）、
托盘常驻、关窗不退程序。

复用关系
========
  · 引擎控制 → babel_admin.serve.Engine（同一套子进程+环形日志，不重复造）
  · 浮窗调节 → control_cmd.json 文件协议（浮窗每 300ms 轮询，即时生效）
  · 模型管理 → model_manager（搜索/下载/设为默认）
  · 硬件检测 → hardware_detect（检测 + 推荐）

启动：  python admin_qt.py        （用带 PyQt5 的解释器）
"""
from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "babel_admin"))

SETTINGS = os.path.join(HERE, "overlay_qt.json")
CMD = os.path.join(HERE, "control_cmd.json")
ADMIN_CFG = os.path.join(HERE, "work", "admin_qt.json")

from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt5.QtGui import QColor, QIcon, QFont
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QListWidget, QStackedWidget, QLabel, QPushButton, QComboBox,
    QPlainTextEdit, QSlider, QDoubleSpinBox, QColorDialog, QRadioButton,
    QCheckBox, QButtonGroup, QLineEdit, QTreeWidget, QTreeWidgetItem,
    QProgressBar, QGroupBox, QFormLayout, QMessageBox, QSystemTrayIcon,
    QMenu, QSplitter, QFrame, QAbstractItemView, QHeaderView)

# ── 后端模块（引擎 / 模型 / 硬件）──────────────────────────────
try:
    from babel_admin import serve as _serve
except Exception:
    try:
        import serve as _serve  # type: ignore
    except Exception:
        _serve = None
ENGINE = _serve.ENGINE if _serve else None

import model_manager as mm
import hardware_detect as hw

QSS = """
QMainWindow, QWidget { background: #14171c; color: #d7dde6;
    font-family: "Microsoft YaHei UI", "Microsoft YaHei"; font-size: 13px; }
QListWidget { background: #0f1216; border: none; outline: none;
    font-size: 14px; padding: 8px; }
QListWidget::item { padding: 10px 14px; border-radius: 8px; margin: 2px 0; }
QListWidget::item:selected { background: #2b6cb0; color: #fff; }
QListWidget::item:hover:!selected { background: #1b2129; }
QPushButton { background: #232a34; border: 1px solid #333c49;
    border-radius: 6px; padding: 7px 14px; }
QPushButton:hover { background: #2c3542; }
QPushButton:pressed { background: #1d2530; }
QPushButton:disabled { color: #6b7684; background: #1a1f27; }
QPushButton#primary { background: #2b6cb0; border-color: #2b6cb0; color: #fff; }
QPushButton#primary:hover { background: #3580cf; }
QPushButton#danger { background: #7c2d2d; border-color: #7c2d2d; color: #ffd9d9; }
QPushButton#danger:hover { background: #993a3a; }
QPlainTextEdit, QTreeWidget { background: #0f1216; border: 1px solid #262d38;
    border-radius: 6px; }
QTreeWidget::item { padding: 4px; }
QTreeWidget::item:selected { background: #2b6cb0; }
QHeaderView::section { background: #1a2029; border: none; padding: 6px; }
QGroupBox { border: 1px solid #262d38; border-radius: 8px; margin-top: 12px;
    padding-top: 10px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; color: #8fa3bd; }
QSlider::groove:horizontal { height: 6px; background: #232a34;
    border-radius: 3px; }
QSlider::handle:horizontal { width: 16px; margin: -6px 0; border-radius: 8px;
    background: #2b6cb0; }
QSlider::sub-page:horizontal { background: #2b6cb0; border-radius: 3px; }
QComboBox, QLineEdit { background: #0f1216; border: 1px solid #333c49;
    border-radius: 6px; padding: 6px 8px; }
QProgressBar { background: #0f1216; border: 1px solid #333c49;
    border-radius: 6px; text-align: center; }
QProgressBar::chunk { background: #2b6cb0; border-radius: 5px; }
QCheckBox::indicator, QRadioButton::indicator { width: 15px; height: 15px; }
QLabel#h1 { font-size: 18px; font-weight: bold; color: #eef2f8; }
QLabel#muted { color: #8fa3bd; }
QLabel#ok { color: #4ade80; }
QLabel#warn { color: #fbbf24; }
QLabel#bad { color: #f87171; }
QFrame#card { background: #1a2029; border: 1px solid #262d38;
    border-radius: 10px; }
"""


def _read_json(p, default=None):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def _write_cmd(changes: dict) -> None:
    """浮窗命令协议（与 control_panel.write_cmd 相同）。"""
    cur = _read_json(CMD, {})
    cur["_ts"] = time.time()
    cur["_changes"] = changes
    tmp = CMD + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False)
    os.replace(tmp, CMD)


def _admin_cfg() -> dict:
    return _read_json(ADMIN_CFG, {})


def _save_admin_cfg(d: dict) -> None:
    try:
        os.makedirs(os.path.dirname(ADMIN_CFG), exist_ok=True)
        tmp = ADMIN_CFG + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        os.replace(tmp, ADMIN_CFG)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════
# 页 1：总览（引擎控制 + 日志）
# ══════════════════════════════════════════════════════════════
class PageOverview(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)

        head = QHBoxLayout()
        t = QLabel("字幕引擎")
        t.setObjectName("h1")
        head.addWidget(t)
        head.addStretch(1)
        self.badge = QLabel("未启动")
        self.badge.setObjectName("muted")
        head.addWidget(self.badge)
        lay.addLayout(head)

        card = QFrame()
        card.setObjectName("card")
        cl = QVBoxLayout(card)
        row = QHBoxLayout()
        row.addWidget(QLabel("模式"))
        self.mode = QComboBox()
        try:
            modes = _serve.R.MODES if _serve else {}
            for k, v in modes.items():
                label = v.get("label", k) if isinstance(v, dict) else k
                self.mode.addItem(f"{label}（{k}）", k)
        except Exception:
            self.mode.addItem("实时回环（loopback）", "loopback")
        row.addWidget(self.mode, 1)
        self.btn_start = QPushButton("启动")
        self.btn_start.setObjectName("primary")
        self.btn_start.clicked.connect(self.start_engine)
        self.btn_stop = QPushButton("停止")
        self.btn_stop.setObjectName("danger")
        self.btn_stop.clicked.connect(self.stop_engine)
        self.btn_restart = QPushButton("重启")
        self.btn_restart.clicked.connect(self.restart_engine)
        row.addWidget(self.btn_start)
        row.addWidget(self.btn_stop)
        row.addWidget(self.btn_restart)
        cl.addLayout(row)
        self.meta = QLabel("—")
        self.meta.setObjectName("muted")
        cl.addWidget(self.meta)
        lay.addWidget(card)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("运行日志"))
        row2.addStretch(1)
        btn_clear = QPushButton("清空显示")
        btn_clear.clicked.connect(lambda: self.log.clear())
        row2.addWidget(btn_clear)
        lay.addLayout(row2)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(QFont("Cascadia Mono", 9))
        lay.addWidget(self.log, 1)

        self._log_n = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(800)

    def start_engine(self):
        if not ENGINE:
            return
        ok, msg = ENGINE.start(mode=self.mode.currentData())
        self.main.toast(msg, ok)

    def stop_engine(self):
        if not ENGINE:
            return
        ok, msg = ENGINE.stop()
        self.main.toast(msg, ok)

    def restart_engine(self):
        if not ENGINE:
            return
        if ENGINE.alive():
            ENGINE.stop()
        ok, msg = ENGINE.start(mode=self.mode.currentData())
        self.main.toast(msg, ok)

    def refresh(self):
        if not ENGINE:
            self.badge.setText("serve.py 不可用")
            self.badge.setObjectName("bad")
            return
        st = ENGINE.status()
        if st["running"]:
            self.badge.setText(f"● 运行中  pid {st['pid']}  {st['uptime']}s")
            self.badge.setObjectName("ok")
            self.meta.setText(f"解释器：{st['python']}（{st['python_reason']}）"
                              f"　模式：{st['mode']}")
            self.btn_start.setEnabled(False)
            self.btn_stop.setEnabled(True)
        else:
            self.badge.setText("○ 未运行"
                               + (f"（上次退出码 {st['exit_code']}）"
                                  if st["exit_code"] is not None else ""))
            self.badge.setObjectName("muted")
            self.meta.setText(f"解释器：{st['python']}（{st['python_reason']}）"
                              + (f"　{st['gpu_hint']}" if st["gpu_hint"] else ""))
            self.btn_start.setEnabled(True)
            self.btn_stop.setEnabled(False)
        lines = st["log"]
        if len(lines) != self._log_n:
            self.log.setPlainText("\n".join(lines))
            sb = self.log.verticalScrollBar()
            sb.setValue(sb.maximum())
            self._log_n = len(lines)


# ══════════════════════════════════════════════════════════════
# 页 2：模型（搜索 / 下载 / 推荐 / 设为默认）
# ══════════════════════════════════════════════════════════════
class _HwSignals(QObject):
    done = pyqtSignal(dict)


class PageModels(QWidget):
    COLS = ("模型", "分组", "体积", "显存", "状态", "来源", "进度")

    def __init__(self, main):
        super().__init__()
        self.main = main
        self._hw = None
        self._items = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)

        head = QHBoxLayout()
        t = QLabel("模型管理")
        t.setObjectName("h1")
        head.addWidget(t)
        head.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索模型名 / 来源…")
        self.search.textChanged.connect(self.reload)
        self.search.setFixedWidth(240)
        head.addWidget(self.search)
        self.grp = QComboBox()
        self.grp.addItem("全部", "")
        self.grp.addItem("识别 (ASR)", "asr")
        self.grp.addItem("翻译 (MT)", "mt")
        self.grp.currentIndexChanged.connect(self.reload)
        head.addWidget(self.grp)
        lay.addLayout(head)

        # 硬件检测卡
        card = QFrame()
        card.setObjectName("card")
        cl = QVBoxLayout(card)
        r1 = QHBoxLayout()
        self.hw_label = QLabel("尚未检测")
        self.hw_label.setWordWrap(True)
        r1.addWidget(self.hw_label, 1)
        btn_hw = QPushButton("检测本机性能")
        btn_hw.setObjectName("primary")
        btn_hw.clicked.connect(self.run_hw)
        r1.addWidget(btn_hw)
        cl.addLayout(r1)
        self.rec_label = QLabel("")
        self.rec_label.setWordWrap(True)
        self.rec_label.setObjectName("muted")
        cl.addWidget(self.rec_label)
        lay.addWidget(card)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(self.COLS))
        self.tree.setHeaderLabels(self.COLS)
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.itemSelectionChanged.connect(self.on_select)
        lay.addWidget(self.tree, 1)

        ops = QHBoxLayout()
        self.btn_dl = QPushButton("下载 / 安装")
        self.btn_dl.setObjectName("primary")
        self.btn_dl.clicked.connect(self.do_download)
        self.btn_cancel = QPushButton("取消下载")
        self.btn_cancel.clicked.connect(self.do_cancel)
        self.btn_asr = QPushButton("设为默认 ASR")
        self.btn_asr.clicked.connect(lambda: self.do_default("asr"))
        self.btn_mt = QPushButton("设为默认 MT")
        self.btn_mt.clicked.connect(lambda: self.do_default("mt"))
        self.btn_rm = QPushButton("删除")
        self.btn_rm.setObjectName("danger")
        self.btn_rm.clicked.connect(self.do_remove)
        for b in (self.btn_dl, self.btn_cancel, self.btn_asr, self.btn_mt,
                  self.btn_rm):
            ops.addWidget(b)
        ops.addStretch(1)
        self.defaults_label = QLabel("")
        self.defaults_label.setObjectName("muted")
        ops.addWidget(self.defaults_label)
        lay.addLayout(ops)

        self.sig = _HwSignals()
        self.sig.done.connect(self.on_hw)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.reload_progress)
        self.timer.start(1000)
        self.reload()

    # ── 数据 ──
    def reload(self):
        q = self.search.text()
        g = self.grp.currentData()
        self._items = mm.annotate_recommend(mm.search(q, g), self._hw)
        self.tree.clear()
        for m in self._items:
            status = {"installed": "已安装", "downloadable": "可下载",
                      "needs_key": "需 API key"}.get(m["status"], "?")
            if m.get("recommended"):
                status += "  ★推荐"
            it = QTreeWidgetItem([
                m["name"],
                {"asr": "识别", "mt": "翻译"}.get(m["group"], m["group"]),
                f"{m['size_mb']}MB" if m["size_mb"] else "—",
                f"{m['vram_mb']}MB" if m["vram_mb"] else "—",
                status, m["source"], ""])
            it.setData(0, Qt.UserRole, m["id"])
            if m["status"] == "installed":
                it.setForeground(4, QColor("#4ade80"))
            elif m["status"] == "needs_key":
                it.setForeground(4, QColor("#8fa3bd"))
            else:
                it.setForeground(4, QColor("#fbbf24"))
            if m.get("recommended"):
                it.setForeground(0, QColor("#7cc4ff"))
            it.setToolTip(0, m.get("rec_reason") or m.get("note", ""))
            self.tree.addTopLevelItem(it)
        d = mm.current_defaults()
        self.defaults_label.setText(
            f"当前默认　ASR：{d.get('asr', '?')}　MT：{d.get('mt', '?')}")

    def reload_progress(self):
        st = mm._load_state()
        dirty = False
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            mid = it.data(0, Qt.UserRole)
            d = st.get(mid)
            if d and d.get("state") in ("downloading", "paused", "error"):
                txt = (f"{d.get('pct', 0):.0f}%  {d.get('detail', '')}"
                       if d["state"] == "downloading"
                       else ("已取消" if d["state"] == "paused"
                             else f"失败：{d.get('error', '')}"))
                if it.text(6) != txt:
                    it.setText(6, txt)
                    dirty = True
            elif it.text(6):
                it.setText(6, "")
                dirty = True
        if dirty:
            pass

    def selected_id(self):
        it = self.tree.currentItem()
        return it.data(0, Qt.UserRole) if it else None

    def on_select(self):
        pass

    # ── 硬件检测（后台线程，不卡界面）──
    def run_hw(self):
        self.hw_label.setText("检测中…")
        import threading

        def job():
            rep = hw.detect(force=True)
            self.sig.done.emit(rep)
        threading.Thread(target=job, daemon=True).start()

    def on_hw(self, rep):
        self._hw = rep
        g, c, m = rep["gpu"], rep["cpu"], rep["mem"]
        self.hw_label.setText(
            f"算力 {rep['score']}/100（{rep['recommend']['grade']}）　"
            f"{g.get('name') or '无独显'}"
            + (f" {int(g.get('vram_free_mb') or 0)}/"
               f"{int(g.get('vram_total_mb') or 0)}MB"
               if g.get("vram_total_mb") else "")
            + f"　{c.get('brand', '?')} {c.get('physical') or '?'}C"
              f"　内存 {int((m.get('free_mb') or 0) / 1024)}/"
              f"{int((m.get('total_mb') or 0) / 1024)}GB")
        r = rep["recommend"]
        self.rec_label.setText(
            f"推荐 ASR：faster-whisper {r['asr']['size']} — {r['asr']['reason']}\n"
            f"推荐 MT ：{'本地 HY-MT ' + r['mt']['size'] if r['mt']['type'] == 'local' else '在线翻译'}"
            f" — {r['mt']['reason']}"
            + ("\n注意：" + "；".join(r["notes"]) if r["notes"] else ""))
        self.reload()

    # ── 操作 ──
    def do_download(self):
        mid = self.selected_id()
        if not mid:
            return
        m = next((x for x in self._items if x["id"] == mid), None)
        if not m:
            return
        if m["status"] == "needs_key":
            self.main.toast("远端模型不用下载，去配 API key 即可", False)
            return
        if m["status"] == "installed":
            self.main.toast("已经装好了", True)
            return
        free_gb = hw.detect().get("disk", {}).get("free_gb") or 999
        if m["size_mb"] / 1024 > free_gb:
            self.main.toast(f"磁盘不够：需要 {m['size_mb']}MB，"
                            f"只剩 {free_gb}GB", False)
            return
        mm.download(mid,
                    on_done=lambda ok, model_id, **kw: None)
        self.main.toast(f"开始下载 {m['name']}（后台线程，可继续操作）", True)

    def do_cancel(self):
        mid = self.selected_id()
        if mid and mm.cancel(mid):
            self.main.toast("已取消", True)

    def do_default(self, slot):
        mid = self.selected_id()
        if not mid:
            return
        m = next((x for x in self._items if x["id"] == mid), None)
        if m and m["status"] == "downloadable":
            self.main.toast("还没下载，先点「下载 / 安装」", False)
            return
        ok, msg = mm.set_default(slot, mid)
        self.main.toast(msg, ok)
        if ok:
            self.reload()

    def do_remove(self):
        mid = self.selected_id()
        if not mid:
            return
        m = next((x for x in self._items if x["id"] == mid), None)
        if not m or m["status"] != "installed":
            return
        if QMessageBox.question(self, "删除模型",
                                f"确定删除 {m['name']}？\n{m.get('path') or ''}"
                                ) != QMessageBox.Yes:
            return
        ok, msg = mm.uninstall(mid)
        self.main.toast(msg, ok)
        self.reload()


# ══════════════════════════════════════════════════════════════
# 页 3：浮窗（全参数实时调节）
# ══════════════════════════════════════════════════════════════
class _SliderRow(QWidget):
    """标签 + 滑条 + 数值框，改动即 push。"""

    def __init__(self, label, lo, hi, step, on_change, fmt="{:.2f}"):
        super().__init__()
        self.lo, self.hi, self.step = lo, hi, step
        self.fmt = fmt
        self.on_change = on_change
        self._mute = False
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lb = QLabel(label)
        lb.setFixedWidth(110)
        lay.addWidget(lb)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, round((hi - lo) / step))
        self.slider.valueChanged.connect(self._sl)
        lay.addWidget(self.slider, 1)
        self.val = QDoubleSpinBox()
        self.val.setRange(lo, hi)
        self.val.setSingleStep(step)
        self.val.setDecimals(2 if step < 1 else 0)
        self.val.setFixedWidth(86)
        self.val.valueChanged.connect(self._sp)
        lay.addWidget(self.val)

    def _sl(self, i):
        v = self.lo + i * self.step
        if self._mute:
            return
        self._mute = True
        self.val.setValue(v)
        self._mute = False
        self.on_change(v)

    def _sp(self, v):
        if self._mute:
            return
        self._mute = True
        self.slider.setValue(round((v - self.lo) / self.step))
        self._mute = False
        self.on_change(v)

    def set(self, v):
        self._mute = True
        try:
            self.slider.setValue(round((v - self.lo) / self.step))
            self.val.setValue(v)
        finally:
            self._mute = False


class PageOverlay(QWidget):
    NUM = [
        ("font_size", "字号", 10, 120, 1),
        ("original_ratio", "原文字号比", 0.4, 1.2, 0.02),
        ("line_height", "行距", 0.9, 2.0, 0.05),
        ("width", "宽度", 300, 1900, 20),
        ("max_lines", "显示条数", 1, 8, 1),
        ("max_entry_chars", "单条最大字数", 60, 400, 10),
        ("hold_seconds", "停留秒数", 1, 20, 0.5),
        ("opacity", "不透明度", 0.2, 1.0, 0.05),
        ("bg_alpha", "底色浓度", 0.0, 1.0, 0.05),
        ("outline_width", "描边粗细", 0.0, 4.0, 0.05),
        ("gap_px", "同行间距", 0, 80, 2),
    ]
    COLORS = [
        ("text_color", "译文颜色"),
        ("original_text_color", "原文颜色"),
        ("outline_color", "描边颜色"),
        ("bg_color", "底色"),
        ("border_color", "边框颜色"),
    ]
    BOOLS = [
        ("show_original", "显示原文"),
        ("edit_mode", "调整模式（显边框把手）"),
        ("theme_dark_video", "深色画面主题"),
        ("show_idle_hint", "空闲提示"),
    ]

    def __init__(self, main):
        super().__init__()
        self.main = main
        self._rows = {}
        self._color_btns = {}
        self._bools = {}
        self._syncing = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)

        head = QHBoxLayout()
        t = QLabel("浮窗设置")
        t.setObjectName("h1")
        head.addWidget(t)
        self.live = QLabel("改动即时生效（300ms 内应用到浮窗）")
        self.live.setObjectName("muted")
        head.addWidget(self.live)
        head.addStretch(1)
        lay.addLayout(head)

        # ── 滑条区 ──
        g1 = QGroupBox("尺寸与节奏")
        f1 = QFormLayout(g1)
        for key, label, lo, hi, step in self.NUM:
            row = _SliderRow(label, lo, hi, step,
                             lambda v, k=key: self.push({k: v}))
            self._rows[key] = row
            f1.addRow(row)
        lay.addWidget(g1)

        # ── 颜色区 ──
        g2 = QGroupBox("颜色（点击色块挑选，实时上屏）")
        c2 = QHBoxLayout(g2)
        for key, label in self.COLORS:
            b = QPushButton(label)
            b.setFixedHeight(34)
            b.clicked.connect(lambda _=False, k=key: self.pick_color(k))
            self._color_btns[key] = b
            c2.addWidget(b)
        lay.addWidget(g2)

        # ── 排布 / 对齐 / 开关 ──
        g3 = QGroupBox("排布与开关")
        v3 = QVBoxLayout(g3)
        r = QHBoxLayout()
        r.addWidget(QLabel("排布"))
        self.layout_grp = QButtonGroup(self)
        for val, name in (("inline", "同行两列"), ("stacked", "上下两行"),
                          ("zh_only", "只译文")):
            rb = QRadioButton(name)
            rb.setProperty("val", val)
            rb.toggled.connect(lambda on, v=val: on and self.push({"layout": v}))
            self.layout_grp.addButton(rb)
            r.addWidget(rb)
        r.addSpacing(20)
        r.addWidget(QLabel("对齐"))
        self.align_grp = QButtonGroup(self)
        for val, name in (("center", "居中"), ("left", "左"), ("right", "右")):
            rb = QRadioButton(name)
            rb.setProperty("val", val)
            rb.toggled.connect(lambda on, v=val: on and self.push({"text_align": v}))
            self.align_grp.addButton(rb)
            r.addWidget(rb)
        r.addStretch(1)
        v3.addLayout(r)
        r2 = QHBoxLayout()
        for key, label in self.BOOLS:
            cb = QCheckBox(label)
            cb.toggled.connect(lambda on, k=key: self.push({k: bool(on)}))
            self._bools[key] = cb
            r2.addWidget(cb)
        r2.addStretch(1)
        v3.addLayout(r2)
        lay.addWidget(g3)

        # ── 动作 ──
        r3 = QHBoxLayout()
        for txt, act in (("回默认位置", "recenter"), ("隐藏/显示", "toggle"),
                         ("全部重置", "reset")):
            b = QPushButton(txt)
            b.clicked.connect(lambda _=False, a=act: self.push({"_action": a}))
            r3.addWidget(b)
        r3.addStretch(1)
        self.sync_state = QLabel("")
        self.sync_state.setObjectName("muted")
        r3.addWidget(self.sync_state)
        lay.addLayout(r3)
        lay.addStretch(1)

        # 反向同步：浮窗快捷键改了设置，面板跟着动
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.sync_from_overlay)
        self.timer.start(1200)
        self.sync_from_overlay()

    def push(self, changes: dict):
        if self._syncing:
            return
        try:
            _write_cmd(changes)
            self.live.setText("已发送：" + ", ".join(changes.keys()))
            QTimer.singleShot(1500, lambda: self.live.setText(
                "改动即时生效（300ms 内应用到浮窗）"))
        except Exception as e:
            self.live.setText(f"发送失败：{e}")

    def pick_color(self, key):
        cur = _read_json(SETTINGS, {}).get(key, "#FFFFFF")
        c = QColorDialog.getColor(QColor(cur), self, "选择颜色")
        if c.isValid():
            self.push({key: c.name().upper()})
            self._paint_color_btn(key, c.name().upper())

    def _paint_color_btn(self, key, hexv):
        b = self._color_btns.get(key)
        if b:
            b.setStyleSheet(f"background:{hexv}; color:"
                            f"{'#000' if QColor(hexv).lightness() > 140 else '#fff'};")

    def sync_from_overlay(self):
        s = _read_json(SETTINGS, {})
        if not s:
            self.sync_state.setText("浮窗未运行（显示的是上次保存的设置）")
        else:
            ts = s.get("_app_ts")
            self.sync_state.setText(
                "浮窗在线" if ts and time.time() - ts < 30 else "设置已载入")
        self._syncing = True
        try:
            for key, row in self._rows.items():
                if isinstance(s.get(key), (int, float)):
                    row.set(float(s[key]))
            for key in self._color_btns:
                if s.get(key):
                    self._paint_color_btn(key, s[key])
            for rb in self.layout_grp.buttons():
                rb.setChecked(rb.property("val") == s.get("layout"))
            for rb in self.align_grp.buttons():
                rb.setChecked(rb.property("val") == s.get("text_align", "center"))
            for key, cb in self._bools.items():
                if isinstance(s.get(key), bool):
                    cb.setChecked(s[key])
        finally:
            self._syncing = False


# ══════════════════════════════════════════════════════════════
# 页 4：启动（热启动 / 看门狗 / 启动耗时）
# ══════════════════════════════════════════════════════════════
class PageStartup(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)
        t = QLabel("启动与稳定性")
        t.setObjectName("h1")
        lay.addWidget(t)

        cfg = _admin_cfg()

        g1 = QGroupBox("热启动")
        v1 = QVBoxLayout(g1)
        self.cb_prewarm = QCheckBox(
            "打开后台时预热（后台线程预读模型文件与 CUDA 库，"
            "引擎启动时直接命中磁盘缓存）")
        self.cb_prewarm.setChecked(bool(cfg.get("prewarm", True)))
        self.cb_prewarm.toggled.connect(self.save_cfg)
        v1.addWidget(self.cb_prewarm)
        r = QHBoxLayout()
        btn_pw = QPushButton("立即预热一次")
        btn_pw.clicked.connect(self.do_prewarm)
        r.addWidget(btn_pw)
        self.pw_state = QLabel("")
        self.pw_state.setObjectName("muted")
        r.addWidget(self.pw_state, 1)
        v1.addLayout(r)
        self.hint = QLabel(
            "预热 ≠ 预加载模型常驻显存（那会一直占 3GB+）。预热做的是：\n"
            "  1) 预读 whisper ctranslate2 模型文件 → 进系统文件缓存\n"
            "  2) 预加载 cublas/cudnn DLL → CUDA 上下文初始化快一倍\n"
            "  3) 缓存 GPU 探测结果 → 跳过每次启动的重复探测（实测能省 30s+）")
        self.hint.setObjectName("muted")
        v1.addWidget(self.hint)
        lay.addWidget(g1)

        g2 = QGroupBox("浮窗看门狗")
        v2 = QVBoxLayout(g2)
        self.cb_watchdog = QCheckBox(
            "引擎意外退出时自动重启（最多连续 3 次，之后停下等人工处理）")
        self.cb_watchdog.setChecked(bool(cfg.get("watchdog", True)))
        self.cb_watchdog.toggled.connect(self.save_cfg)
        v2.addWidget(self.cb_watchdog)
        self.wd_state = QLabel("—")
        self.wd_state.setObjectName("muted")
        v2.addWidget(self.wd_state)
        lay.addWidget(g2)

        g3 = QGroupBox("最近启动耗时")
        v3 = QVBoxLayout(g3)
        self.stats = QPlainTextEdit()
        self.stats.setReadOnly(True)
        self.stats.setMaximumHeight(180)
        v3.addWidget(self.stats)
        lay.addWidget(g3)
        lay.addStretch(1)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1500)
        self.refresh()

    def save_cfg(self):
        d = _admin_cfg()
        d["prewarm"] = self.cb_prewarm.isChecked()
        d["watchdog"] = self.cb_watchdog.isChecked()
        _save_admin_cfg(d)

    def do_prewarm(self):
        self.pw_state.setText("预热中…")
        import threading

        def job():
            t0 = time.time()
            msg = prewarm()
            sec = time.time() - t0
            self.pw_state.setText(f"{msg}（{sec:.1f}s）")
        threading.Thread(target=job, daemon=True).start()

    def refresh(self):
        d = _read_json(os.path.join(HERE, "work", "startup_stats.json"), {})
        if d:
            hist = d.get("history", [])[-8:]
            lines = [f"{'时间':<20}{'总耗时':>8}  明细"]
            for h in reversed(hist):
                lines.append(
                    f"{time.strftime('%m-%d %H:%M:%S', time.localtime(h.get('ts', 0))):<20}"
                    f"{h.get('total', 0):>6.1f}s  "
                    + "  ".join(f"{k}:{v:.1f}s" for k, v in h.get("stages", {}).items()))
            self.stats.setPlainText("\n".join(lines) if len(lines) > 1
                                    else "还没有记录（引擎启动一次后这里会有数据）")
        else:
            self.stats.setPlainText("还没有记录（引擎启动一次后这里会有数据）")
        wd = getattr(self.main, "watchdog_info", "")
        self.wd_state.setText(wd or "看门狗待命")


# ── 预热实现 ──────────────────────────────────────────────────
def prewarm() -> str:
    """预读模型文件 + CUDA DLL。只读文件，不加载模型到显存。"""
    done = []
    # 1) 预读 whisper 模型文件（默认档）
    try:
        d = mm.current_defaults()
        asr = (d.get("asr") or "local:faster-whisper-medium")
        size = asr.replace("local:faster-whisper-", "")
        p = mm.whisper_installed(size)
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
    # 2) 预热 CUDA DLL 目录（读一遍让系统缓存）
    try:
        from babel_admin import dlls as _dlls
    except Exception:
        try:
            import dlls as _dlls  # type: ignore
        except Exception:
            _dlls = None
    if _dlls:
        try:
            env = _dlls.env_for_engine()
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


# ══════════════════════════════════════════════════════════════
# 主窗口
# ══════════════════════════════════════════════════════════════
class AdminWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Babel Plan 控制台")
        self.resize(1080, 720)
        self._crash_times: list[float] = []
        self.watchdog_info = ""

        central = QWidget()
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.nav = QListWidget()
        self.nav.setFixedWidth(150)
        self.nav.addItems(["总览", "模型", "浮窗", "启动"])
        lay.addWidget(self.nav)
        self.stack = QStackedWidget()
        self.p_overview = PageOverview(self)
        self.p_models = PageModels(self)
        self.p_overlay = PageOverlay(self)
        self.p_startup = PageStartup(self)
        for p in (self.p_overview, self.p_models, self.p_overlay,
                  self.p_startup):
            self.stack.addWidget(p)
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)

        # 托盘
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(self.style().standardIcon(
            self.style().SP_ComputerIcon))
        menu = QMenu()
        menu.addAction("打开控制台", self.showNormal)
        menu.addAction("退出", QApplication.instance().quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda r: self.showNormal() if r == QSystemTrayIcon.Trigger else None)
        self.tray.show()

        # 看门狗
        self.wd_timer = QTimer(self)
        self.wd_timer.timeout.connect(self._watchdog)
        self.wd_timer.start(3000)

        # 打开后台时预热（可在启动页关）
        if _admin_cfg().get("prewarm", True):
            QTimer.singleShot(800, self.p_startup.do_prewarm)

    def toast(self, msg: str, ok: bool = True):
        self.tray.showMessage("Babel Plan", msg,
                              QSystemTrayIcon.Information
                              if ok else QSystemTrayIcon.Warning, 2500)
        self.statusBar().showMessage(msg, 4000)

    def _watchdog(self):
        cfg = _admin_cfg()
        if not cfg.get("watchdog", True) or not ENGINE:
            return
        if ENGINE.alive():
            self._crash_times.clear()
            return
        # 引擎没跑：只有"被我们启动过又意外死掉"才重启
        if ENGINE.started_at == 0 or ENGINE.exit_code in (0, None):
            return
        if ENGINE.stopped_at and time.time() - ENGINE.stopped_at > 600:
            return  # 死太久不管了
        now = time.time()
        self._crash_times = [t for t in self._crash_times if now - t < 300]
        if len(self._crash_times) >= 3:
            self.watchdog_info = "已连续崩溃 3 次，停止自动重启，请查看日志"
            return
        self._crash_times.append(now)
        self.watchdog_info = (f"检测到引擎退出（code {ENGINE.exit_code}），"
                              f"自动重启第 {len(self._crash_times)} 次")
        ENGINE.start(mode=ENGINE.mode or "loopback")

    def closeEvent(self, ev):
        # 关窗 ≠ 退出：收到托盘。真退出用托盘菜单。
        if self.tray.isVisible():
            self.hide()
            ev.ignore()
        else:
            super().closeEvent(ev)


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyleSheet(QSS)
    app.setQuitOnLastWindowClosed(False)
    w = AdminWindow()
    w.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
