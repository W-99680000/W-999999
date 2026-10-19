#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
watch_overlay —— 点播路线的透明置顶字幕浮窗（overlay_qt 的独立复刻）
====================================================================

来历
----
这是 `overlay_qt.py` 的**逐行复刻**，供 watch 路线（watch.bat →
watch_player.py）使用。为什么要复刻而不是直接 import 回环那份：

  1. 回环（start.bat → live_subtitle_v2.py）已经稳定，不能动它一个字节；
     直接 import 就把两条路线的人工绑在一起，改 watch 有连带风险。
  2. 浮窗会把自己的样式 _save() 回写设置文件。两边共用 overlay_qt.json
     会互相覆写（一边调好的字号/位置被另一边启动时冲掉），
     表现就是"浮窗不听话 / 挂了"。

所以这里用**独立的三份文件**：
    watch_overlay.json        设置（首次从 overlay_qt.json 继承一次）
    watch_overlay_state.json  实时状态（外部观察浮窗有没有真的显示）
    watch_cmd.json            控制面板命令（不与回环的 control_cmd.json 混）
除此之外的显示逻辑与 overlay_qt 完全一致。

透明置顶字幕浮窗（PyQt5，按成熟方案的字幕规范重做）

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
    python watch_overlay.py --demo --seconds 20
    python watch_overlay.py --selftest
    from watch_overlay import QtOverlay
"""
from __future__ import annotations
import model_home  # noqa: E402,F401  # 先把模型目录钉到 D 盘（务必在 HF/faster-whisper 之前）

import json
import os
import sys
import time


_HERE = os.path.dirname(os.path.abspath(__file__))

# ── watch 专属设置文件（与回环的 overlay_qt.json **完全分离**）────────
# 为什么必须分离：浮窗每次调字号/位置/条数都会 _save() 回写设置文件。
# 回环（start.bat → live_subtitle_v2.py）和点播（watch.bat →
# watch_player.py）如果共用同一个 overlay_qt.json，两边就会互相覆写：
# 一边调好的样式，另一边一启动就被改回去；拖坏的坐标还会互相延续。
# 观感上就像"浮窗不听话 / 挂了"。所以这份复刻用独立文件，互不干扰。
SETTINGS = os.path.join(_HERE, "watch_overlay.json")
# 首次运行：从回环的设置**继承一次**（只读），保证外观与回环一致；
# 之后 watch 有自己的文件，再也不碰回环那份。
_LEGACY_SETTINGS = os.path.join(_HERE, "overlay_qt.json")
if not os.path.exists(SETTINGS) and os.path.exists(_LEGACY_SETTINGS):
    try:
        with open(_LEGACY_SETTINGS, encoding="utf-8") as _f:
            _seed = _f.read()
        with open(SETTINGS, "w", encoding="utf-8", newline="") as _f:
            _f.write(_seed)
    except Exception:
        pass
# 浮窗**实时状态**（给外部观察用）。
#
# 为什么需要：操作者报过几次"字幕消失"，而我只能靠读日志推断 ——
# 日志能证明"字幕产出了"，但**证明不了"屏幕上显示了"**。
# 我试过用截屏 + 像素判据来判断，失败了两次：
#   ① "亮像素占比"把背后浏览器窗口的亮色也算成字幕（实测 25%）
#   ② 按设置文件里的坐标裁剪 —— 那是 **Qt 逻辑坐标**，截屏是物理像素，
#      系统缩放 125% 时两者差 20%（实测 288,734 960x77 vs 360,831 1200x165）
# 而且浮窗会**重叠在别的窗口上**，任何"找字幕带"的算法都会混入背景内容。
#
# 所以改成让浮窗自己报告：每次画面变化就写这个文件。
# 格式：{"ts": 时间戳, "kind": "subtitle"/"idle"/"empty",
#        "zh": 译文, "en": 原文, "n": 当前条数}
STATE = os.path.join(_HERE, "watch_overlay_state.json")
# 控制面板往这里写命令，浮窗轮询它（见 control_panel.py 的协议说明）。
# watch 用独立文件：control_cmd.json 是给回环那份浮窗用的，
# 共用会让面板的一次调整同时改到两个浮窗。
CMD_PATH = os.path.join(_HERE, "watch_cmd.json")
# 兼容旧后台：旧版浮窗控制面板只写 control_cmd.json（直播浮窗的）。
# watch 也看这份，取两边 _ts 最新的命令。
CMD_FALLBACK = os.path.join(_HERE, "control_cmd.json")


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
        # 1080p 下 30 → 45px，比主流字幕偏大（参考项目主力 18~26px）。
        # 操作者已两次要求调小，且长句在该字号下要 4~6 行、每条 300px。
        # 24 → 1080p 下 36px，长句降到 2~3 行。
        "font_size": 24,
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
        # **字幕留到下一句替换它，不再自己消失。**
        # 原来是 4 秒（更早是 5 秒），后果实测过：
        #   一条字幕 4 秒后被 _expire 删掉 → entries 空了 →
        #   补上 idle_hint "● 正在听……"。而那条提示是
        #   #B7C4D8 暗淡灰 + bg_alpha=0，在视频上**几乎看不见**。
        #   于是观感就是"字幕消失了"—— 而操作者报过好几次。
        # 更糟的是间隔：实测 100 秒只出 7 条字幕（平均 14 秒一条，
        # 合并翻译攒批会拉长间隔），4 秒的保持时间根本盖不住。
        # 播放器字幕（mpv / YouTube）都是"留到下一句替换"，
        # 这才是观看习惯。默认给 600 秒，等于"不过期"；
        # 想恢复旧行为把 hold_seconds 调小即可（范围 1~3600）。
        "hold_seconds": 600.0,
        # **每条字幕最多显示几行**（不是"最多几条"，那是 max_lines）。
        # 为什么需要：max_lines 管保留几条、max_entry_chars 管字数，
        # 两者都管不住"一条长句折成 5 行" —— 实测 3 条 × 5 行把窗口
        # 撑到 525px（近半屏），位置也从贴底被推到屏幕中部。
        "max_lines_per_entry": 4,
        "show_original": True,
        # **默认上下两行：中文在上、原语在下**（操作者明确要的排法）。
        # 我之前默认 inline（左右两列）是为了省高度，但那是我的偏好 ——
        # 实测对长句并不省，而且不是他要的。改为 stacked。
        #   stacked   中文一行、原语一行（默认）
        #   inline    左右两列（短句时更省高度）
        #   zh_only   只显示中文
        "layout": "stacked",
        # 文字对齐。默认 center：字幕的行业惯例，而且译文/原文长度不同时
        # 各自居中才对齐（原来没有这个设置，是左对齐）。
        "text_align": "center",
        # 调整模式：只有打开时才画边框和拖拽把手。
        # 默认关 —— 成熟方案的字幕就是"文字浮在画面上"，没有窗口框。
        # 按 M 切换。
        "edit_mode": False,
        "gap_px": 10,                # inline 时译文与原文之间的间距
        # 没有字幕时显示一行"正在听"提示。**必须有默认值** ——
        # 只靠 .get(k, True) 兜底会让 default_settings() 里没有这个键，
        # 于是 reset() 之后行为取决于 `.get` 的兜底值，而不是配置。
        # （我上一版就是这么漏的：功能正常但键不存在。）
        "show_idle_hint": True,
        "x": None,
        "y": None,                   # None = 自动贴底部
        # 宽度基准：字幕行业惯例是**每行约 42 个字符**（Netflix 指南）。
        # 我们 1080p 下字号 42px，42 字符约 1420px。原来 780px 太窄 ——
        # 实测长句在 780px 下要 4~6 行，一条就占 300px、3 条铺满屏幕。
        # 1200px 在 1920 屏上仍留足边距，而长句降到 2~3 行。
        "width": 1200,
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


def _text_overlap(a, b):
    """两条原文是否像"同一句话的不同切面"（前缀/重叠度）。

    PENDING_KEEP_OLD 沿用旧译文前的校验：只有新原文与上一条原文
    高度重叠（同一句话被重新切分/加长）时，旧译文才配得上新原文；
    否则会把上一句的译文张冠李戴到新句子下面。
    """
    a, b = (a or "").strip(), (b or "").strip()
    if not a or not b:
        return False
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n >= max(4, int(0.6 * min(len(a), len(b))))


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
        self._prov_lb = None            # 流式模式的"临时条目"标签（update_last 用）
        self._drag = None
        self._hover_grip = False
        self._cmd_ts = None            # 控制面板命令的时间戳（见 _read_cmd）
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

        # 轮询控制面板的命令（文件协议，见 control_panel.py）
        self._cmd_timer = QTimer()
        self._cmd_timer.timeout.connect(self._poll_cmd)
        self._cmd_timer.start(300)
        # 启动时立刻写一次设置，让面板能读到当前值
        self._save()

    def _poll_cmd(self):
        """看一眼控制面板有没有新命令（每 300ms）。出错不影响字幕。"""
        try:
            ch = self._read_cmd()
        except Exception:
            return
        if ch is None:
            return
        try:
            self.apply_cmd(ch)
        except Exception as e:
            print(f"  面板命令应用失败: {type(e).__name__} {e}", flush=True)

    # ── 设置校验 ─────────────────────────────────────────────
    @staticmethod
    def _validate(d):
        from PyQt5.QtWidgets import QApplication
        out = {}
        scr = QApplication.primaryScreen().geometry()
        rng = {"font_size": (10, 200), "opacity": (0.15, 1.0),
               "max_lines": (1, 12), "hold_seconds": (1.0, 3600.0),
               "width": (240, scr.width()), "border_width": (0, 8),
               "max_entry_chars": (30, 400), "ref_height": (200, 4320),
               "original_ratio": (0.3, 1.5),
               "line_height": (0.9, 2.5), "gap_px": (0, 80),
               # bg_alpha 允许 0 —— 0 就是"没有框"（现在的默认值）
               "bg_alpha": (0.0, 1.0),
               "line_chars": (8, 60)}
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
        for k in ("show_original", "theme_dark_video", "edit_mode",
                  "show_idle_hint"):
            if isinstance(d.get(k), bool):
                out[k] = d[k]
        lv = d.get("layout")
        if lv in ("inline", "stacked", "zh_only"):
            out["layout"] = lv
        ff = d.get("font_family")
        if ff in ("Microsoft YaHei UI", "SimSun", "KaiTi",
                  "Segoe UI", "Georgia", "Consolas"):
            out["font_family"] = ff
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

    # ── 与控制面板的通信（文件协议，见 control_panel.py）────────
    def _read_cmd(self):
        """看控制面板有没有发新命令（watch_cmd 优先，兼容 control_cmd）。

        协议：{"_ts": 时间戳, "_changes": {...}}，300ms 轮询。
        两份文件都看，取 _ts 最新的那份；旧版后台只写 control_cmd 也能
        让 watch 生效（不需要重启后台）。
        """
        best_ts, best_ch = None, None
        for p in (CMD_PATH, CMD_FALLBACK):
            try:
                with open(p, encoding="utf-8") as f:
                    j = json.load(f)
            except Exception:
                continue
            ts = j.get("_ts")
            if ts is None:
                continue
            if best_ts is None or ts > best_ts:
                best_ts, best_ch = ts, (j.get("_changes") or {})
        if best_ts is None or best_ts == self._cmd_ts:
            return None
        self._cmd_ts = best_ts
        return best_ch

    def apply_cmd(self, ch):
        """应用面板发来的改动。特殊动作以 _action 为键。"""
        if not ch:
            return
        act = ch.pop("_action", None)
        if act == "sync_position":
            try:
                sec = max(0.0, float(ch.pop("pos", 0) or 0))
            except Exception:
                sec = 0.0
            cb = getattr(self, "on_sync_position", None)
            if cb is not None:
                try:
                    cb(sec)
                except Exception:
                    pass
            print(f"  手动对齐到 {sec:.1f}s", flush=True)
        elif act == "recenter":
            self.recenter()
        elif act == "reset":
            self.reset()
        elif act == "toggle":
            self.toggle()
        # 普通键：只接受 _validate 认得的（防面板发来坏值把浮窗搞坏）
        if ch:
            ok = type(self)._validate(dict(self.st, **ch))
            for k, v in ok.items():
                self.st[k] = v
        # 应用
        try:
            self.w.setWindowOpacity(float(self.st["opacity"]))
        except Exception:
            pass
        self._apply_style()
        self.refresh()
        self._layout()
        self._save()
        print(f"  面板改动已应用: {list(ch.keys()) or act}", flush=True)

    def _save(self):
        try:
            s = dict(self.st)
            # 带上时间戳，面板据此判断"主程序有新状态"
            s["_app_ts"] = time.time()
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
        _ff = self.st.get("font_family") or "Microsoft YaHei UI"
        self.w.setStyleSheet(f"""
            QLabel#zh {{
                color: {self.st['text_color']};
                font-family: "{_ff}", "Microsoft YaHei UI", "Microsoft YaHei";
            }}
            QLabel#en {{
                color: {self.st['original_text_color']};
                font-family: "{_ff}", "Segoe UI", "Microsoft YaHei UI";
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
        # 中文视频：whisper 的源文和 qwen-mt 的译文几乎一样，stacked 会
        # 把同一句话上下显示两遍（用户看到的就是“整体很乱”）。相似度
        # 高于 0.82 时只显示译文；外语视频仍保留双语。
        if show_en and zh and en:
            import difflib
            import re as _re
            _a = _re.sub(r"[\s，。！？、；：,.!?;:…—\-]", "", zh)
            _b = _re.sub(r"[\s，。！？、；：,.!?;:…—\-]", "", en)
            if _a and _b and difflib.SequenceMatcher(None, _a, _b).ratio() >= 0.82:
                show_en = False

        def zh_div(extra=""):
            return (f'<span style="color:{self.st["text_color"]}; '
                    f'font-size:{fs}px; '
                    f'-webkit-text-stroke:{ow:.2f}px {oc}; text-shadow:0 2px 4px rgba(0,0,0,0.85);">'
                    f'{escape(zh).replace(chr(10), "<br>")}{extra}</span>')

        def en_div(span_style=""):
            return (f'<span style="color:{self.st["original_text_color"]}; '
                    f'font-size:{fs_o}px; {span_style}'
                    f'-webkit-text-stroke:{ow * 0.8:.2f}px {oc}; text-shadow:0 1px 3px rgba(0,0,0,0.8);">'
                    f'{escape(en).replace(chr(10), "<br>")}</span>')

        if layout == "inline":
            # ── 两列布局，但要先判断"放不放得下" ──────────────────
            #
            # 教训：我上一轮把顺序流式改成两列表格，理由是"inline 最省高度"。
            # 实测那个理由**只对短文本成立**：780px 宽度下两列各约 390px，
            # 一句长字幕中英文都要折 5~6 行，于是**每条占 300px**，
            # 3 条 = 906px，几乎铺满屏幕（比 stacked 严重得多）。
            #     短文本 1 条 → 126px
            #     长文本 1 条 → 318px   ← 回归
            # 所以加判据：只有"两列各自都放得下"时才用两列，
            # 放不下就退回上下两行（那才是长文本下更省高度的排法）。
            avail = max(200, self.w.width() - 28)
            gap = float(self.st.get("gap_px", 10))
            # 估算两列各自需要的行数（按字符宽度估，不求精确，够做决策）
            zh_w = len(zh) * fs * 0.95
            en_w = sum(fs_o * (0.28 if ch in " .,!?'\"" else 0.50)
                       for ch in en)
            n_zh = max(1, int((zh_w + gap) / (avail * 0.5 - 6 - gap)) + 1)
            n_en = max(1, int(en_w / (avail * 0.5 - 6)) + 1)
            fits_two_cols = (n_zh <= 2 and n_en <= 2)
            if not fits_two_cols:
                layout = "stacked"      # 退回上下两行（下面那段会处理）

        if layout == "inline":
            # **真的"同行"：两列并排，各自在列内居中。**
            #
            # 我第一版只是把两个 span 顺序拼在一行里（中间塞 &nbsp;），
            # 结果实测（780px 宽、42px 字号）**要 5 行** —— 比 stacked 的
            # 2 行还高，完全违背 "inline 最省高度" 的初衷：
            #     中文一行 + 英文三行，因为是顺序流式排布，各自独立折行。
            # 改成两列表格布局后，译文与原文**并排**且各自在列内居中：
            #     一行高度 = max(译文行数, 原文行数)，真正只有一行。
            # 列宽按文本长度分配（不是各 50%），短的一侧不会浪费空间。
            w_zh = max(1, len(zh) * 2)      # 汉字按两个西文字宽估
            w_en = max(1, len(en))
            tot = w_zh + w_en
            pct_zh = max(22, min(78, round(w_zh / tot * 100)))
            pct_en = 100 - pct_zh
            cells = []
            if zh:
                cells.append(
                    f'<td style="width:{pct_zh}%; vertical-align:middle; '
                    f'text-align:{ta}; overflow-wrap:break-word; '
                    f'word-break:normal; padding-right:0.35em;">{zh_div()}</td>')
            if show_en and not zh:
                cells.append(
                    f'<td style="width:100%; vertical-align:middle; '
                    f'text-align:{ta}; overflow-wrap:break-word; '
                    f'word-break:normal;">{en_div()}</td>')
            elif show_en:
                cells.append(
                    f'<td style="width:{pct_en}%; vertical-align:middle; '
                    f'text-align:{ta}; overflow-wrap:break-word; '
                    f'word-break:normal;">{en_div()}</td>')
            body = ("<table style='width:100%; border-collapse:collapse; "
                    "table-layout:fixed;'><tr>" + "".join(cells) +
                    "</tr></table>")
            return (f'<div style="line-height:{lh}; margin:0; '
                    f'white-space:normal;">{body}</div>')

        # stacked / zh_only
        # **每行各自居中**（text-align:center）。译文与原文长度不同，
        # 各自居中才会在视觉上对齐 —— 原来完全没有 text-align，
        # 所以是左对齐，两行左边缘齐、右边缘参差。
        rows = []
        if zh:
            rows.append(f'<div style="font-size:{fs}px; line-height:{lh}; '
                        f'margin:0; text-align:{ta}; '
                        f'overflow-wrap:break-word; word-break:normal;">'
                        f'{zh_div()}</div>')
        if show_en:
            rows.append(f'<div style="font-size:{fs_o}px; line-height:{lh}; '
                        f'margin:1px 0 0 0; text-align:{ta}; '
                        f'overflow-wrap:break-word; word-break:normal;">'
                        f'{en_div()}</div>')
        return "".join(rows) or "&nbsp;"

    # ── 几何：贴底部，按内容向上长 ────────────────────────────
    def _content_height(self, cap=None):
        """用当前所有条目的实际高度算窗口高度（不靠猜）。

        **cap 是必须的。** 实测过一次失控：`max_lines=3` 是**条目数**，
        而条目开了 wordWrap —— 一条 260 字符的字幕在 1200px 宽下折成
        约 5 行，3 条目就是 15 行 ≈ 675px，被屏幕夹到 525px。
        窗口占掉近半屏，位置也从贴底被推到屏幕中部。
        所以除了按折行量，还要按"每条最多几行"封顶。
        """
        h = self.box.contentsMargins().top() + self.box.contentsMargins().bottom()
        for e in self.entries:
            h += e[3].sizeHint().height() + self.box.spacing()
        h = max(60, h + 4)
        if cap is not None:
            h = min(h, int(cap))
        return h

    def content_height_cap(self):
        """窗口高度上限：按每条最多 max_lines_per_entry 行算。

        为什么需要上限而不是只靠 max_lines：
            `max_lines` 限制的是**保留几条**，每条的字数由
            `max_entry_chars` 限制，两者相乘才是真正的行数上限。
            不封顶的话长句会把窗口撑到半个屏幕。
        """
        from PyQt5.QtWidgets import QApplication
        per_entry = int(self.st.get("max_lines_per_entry", 4))
        fs = float(self.fs())
        fs_o = float(self.fs_original()) if self.st.get("show_original") \
            else 0.0
        lh = float(self.st.get("line_height", 1.15))
        # 一个条目 = 译文行 + 原文行（原文按比例小）
        one = fs * lh * per_entry + fs_o * lh * per_entry
        n = int(self.st.get("max_lines", 3))
        m = self.box.contentsMargins()
        gap = float(self.st.get("gap_px", 10))
        cap = (one * n + gap * max(0, n - 1)
               + float(m.top() + m.bottom()) + 2.0 * 6.0 + 4.0)
        scr = QApplication.primaryScreen()
        if scr is not None:
            # 最多占屏幕可用高度的 1/3 —— 字幕不该盖住半个画面
            cap = min(cap, float(scr.availableGeometry().height()) / 3.0)
        return int(max(120, cap))

    # ── 空状态提示 ───────────────────────────────────────────
    # 为什么需要：没有音频时浮窗是全透明的（bg_alpha=0），所以
    # "程序在跑但没声音" 和 "程序没启动" 看起来一模一样 ——
    # 操作者几次报"字幕消失"都是这个原因。加一行暗淡的常驻提示，
    # 让空状态也能看出程序活着。
    # 点播版文案：回环那份是"正在听……（有声音就出字幕）"—— 那是直播的
    # 话术。点播没有"听"，只有"识别+翻译还没落盘"，照抄会让人以为程序
    # 在等声音，实则卡在识别/翻译上。
    IDLE_HINT = "● 字幕待命……（识别 / 翻译进行中，就绪后自动上屏）"

    def idle_hint(self):
        """显示空状态提示（hold=None，不参与过期清理）。"""
        if not self.st.get("show_idle_hint", True):
            return
        for e in self.entries:
            if e[1] == self.IDLE_HINT:
                return                      # 已经有了
        lb = self._make_label()
        lb.setText(
            f'<div style="text-align:center; line-height:1.15; '
            f'font-size:{max(11, int(self.fs() * 0.55))}px; '
            f'color:{self.st.get("original_text_color", "#B7C4D8")}; '
            f'-webkit-text-stroke:0.6px #000; '
            f'text-shadow:0 1px 3px rgba(0,0,0,0.8);">'
            f'{self.IDLE_HINT}</div>')
        self.box.insertWidget(self.box.count() - 1, lb)
        self.entries.append([time.time(), self.IDLE_HINT, "", lb, None])
        self._relayout()

    def clear_idle_hint(self):
        for i in reversed(range(len(self.entries))):
            if self.entries[i][1] == self.IDLE_HINT:
                self._drop(i)

    # ── 多屏 bounds 与最小高度（第⑤项 A+B，参考 Nola windows.ts）──────

    #: 定位模式：free 自由（用存的坐标）/ top 贴顶 / bottom 贴底
    OVERLAY_MARGIN = 24          # 贴顶/贴底时留的边距

    def _screen_for(self, x, y, w, h):
        """找出这块矩形主要落在哪块屏上。

        **不能用 primaryScreen()** —— 浮窗被拖到副屏后，主屏的坐标范围
        里根本没有它，夹取就会把它拽回主屏（或者夹到屏外）。
        没有交集时回退主屏（比如坐标全在屏外时）。
        """
        from PyQt5.QtWidgets import QApplication
        from PyQt5.QtCore import QRect
        rect = QRect(int(x), int(y), int(w), int(h))
        best, best_area = None, 0
        for s in QApplication.screens():
            inter = s.availableGeometry().intersected(rect)
            area = max(0, inter.width()) * max(0, inter.height())
            if area > best_area:
                best, best_area = s, area
        if best is None:
            return QApplication.primaryScreen()
        return best

    def minimum_height(self):
        """至少能显示一行的高度 —— **算出来的，不是猜的**。

        公式（对应 Nola 的 overlayMinimumHeight）：
            最高行高 × 可见轨道数 + 轨道间距 ×(n-1)
            + 上下内边距 + 卡片内缩×2 + 余量

        为什么要余量：窗口矩形的高度由 setGeometry 定，而布局用的**内容盒**
        比它小（内边距/内缩吃掉一部分），+qt 的 sizeHint 是整数而计算值可能是
        小数，差一点就会**丢掉最后一行**。所以留 2px。
        """
        from PyQt5.QtWidgets import QApplication
        fs_zh = float(self.fs())                      # 译文字号（已按屏幕缩放）
        fs_en = float(self.fs_original()) if self.st.get("show_original") \
            else 0.0
        lh = float(self.st.get("line_height", 1.15))
        tracks = [fs_zh * lh]
        if fs_en > 0:
            tracks.append(fs_en * lh)
        tracks = [h for h in tracks if h > 0]
        if not tracks:
            return 60
        gap = float(self.st.get("gap_px", 10))
        m = self.box.contentsMargins()
        pad = float(m.top() + m.bottom())
        slack = 2.0
        need = max(tracks) * len(tracks) + gap * max(0, len(tracks) - 1) \
            + pad + 2.0 * 6.0 + slack       # 6.0 = 卡片内缩（对应 Nola 的 inset）
        # 屏幕比最小值还矮时，宁可少一行也别让窗口超出屏幕
        scr = QApplication.primaryScreen()
        if scr is not None:
            need = min(need, float(scr.availableGeometry().height()))
        return int(max(60, need))

    def _compute_bounds(self, mode, scr_rect, cur_w, cur_h, cx, cy):
        """把窗口夹到指定屏的工作区里，按模式定位。

        返回 (x, y, w, h)。**每一步都夹**，所以任何输入都不会跑出屏幕 ——
        这比"夹到主屏"稳，因为副屏的 left/top 可能是负数（主屏右侧的屏）。
        """
        w = min(int(cur_w), int(scr_rect.width()))
        h = min(int(cur_h), int(scr_rect.height()))
        centered_x = int(scr_rect.left() + (scr_rect.width() - w) / 2)
        if mode == "top":
            y = int(scr_rect.top() + self.OVERLAY_MARGIN)
            return centered_x, y, w, h
        if mode == "bottom":
            y = int(scr_rect.top() + scr_rect.height() - h - self.OVERLAY_MARGIN)
            return centered_x, y, w, h
        # free：用存的坐标，但夹回本屏
        x = centered_x if cx is None else int(cx)
        y = int(scr_rect.top() + self.OVERLAY_MARGIN) if cy is None else int(cy)
        x = max(int(scr_rect.left()),
                min(x, int(scr_rect.left() + scr_rect.width() - w)))
        y = max(int(scr_rect.top()),
                min(y, int(scr_rect.top() + scr_rect.height() - h)))
        return x, y, w, h

    def _layout(self):
        """算窗口位置。

        **关键改动（第⑤项 A）**：不再是"夹到主屏"，而是
          ① 先按存的坐标找出它**该在哪块屏**上
          ② 再用**那块屏**的工作区夹取
        这样副屏上的浮窗不会被拽回主屏，也不会因为屏幕布局变化而跑到屏外。
        """
        from PyQt5.QtWidgets import QApplication
        if not QApplication.screens():
            return
        w = int(self.st["width"])
        # 高度按"折行量"算，但**封顶** —— 否则长句把窗口撑到半个屏幕
        h = max(self._content_height(cap=self.content_height_cap()),
                self.minimum_height())
        cx = self.st.get("x")
        cy = self.st.get("y")
        # 先粗定一个位置，用来判断"主要落在哪块屏"
        probe = QApplication.primaryScreen().availableGeometry()
        px = probe.left() + (probe.width() - w) // 2 if cx is None else int(cx)
        py = probe.top() + probe.height() - h - self.OVERLAY_MARGIN \
            if cy is None else int(cy)
        scr = self._screen_for(px, py, w, h)
        r = scr.availableGeometry()
        # 贴底（未手动定位）用 bottom 模式，否则按存的坐标 free 模式
        mode = "bottom" if (cx is None and cy is None) else "free"
        x, y, w, h = self._compute_bounds(mode, r, w, h, cx, cy)
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
        self._write_state()

    def _write_state(self):
        """把浮窗的**实际显示状态**写到 overlay_state.json。

        为什么需要：日志能证明"字幕产出了"，但证明不了"屏幕上显示了"。
        我两次用像素判据失败（见 STATE 定义的注释），所以让浮窗自己报告。

        kind:
          subtitle → 屏上有真字幕
          idle     → 只有"正在听……"提示（没字幕）
          empty    → 什么都没有（**这才是真故障**）
        """
        try:
            n = len(self.entries)
            subs = [e for e in self.entries if e[2]]
            if subs:
                kind, en, zh = "subtitle", subs[-1][1], subs[-1][2]
            elif n:
                kind, en, zh = "idle", self.entries[-1][1], ""
            else:
                kind, en, zh = "empty", "", ""
            json.dump({"ts": time.time(), "kind": kind, "n": n,
                       "en": en[:200], "zh": zh[:200]},
                      open(STATE, "w", encoding="utf-8"),
                      ensure_ascii=False)
        except Exception:
            pass

    # ── 事件：拖动 / 滑轮 / 快捷键 ────────────────────────────
    # ── 拖拽把手 ─────────────────────────────────────────────
    def _grip_rect(self):
        """把手的矩形（窗口内坐标）。**顶部整条**，不是小方块。

        操作者反馈"移动字幕的位置还是非常困难"。原来是一个 26x20 的小方块
        藏在右上角、还只在调整模式显示 —— 几乎抓不到。
        改成：**窗口顶部整条 22px 高都是把手**，任何位置都能按到；
        鼠标移上去高亮、光标变手型。这样拖动不再需要瞄准。
        """
        from PyQt5.QtCore import QRect
        h = int(self.st.get("grip_h", 22))
        return QRect(0, 0, max(60, self.w.width()), h)

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

        # 边框只在调整模式画（按 M 切换）
        if self.st.get("edit_mode"):
            bg = QColor(self.st.get("bg_color", "#20242A"))
            bg.setAlpha(int(255 * float(self.st.get("bg_alpha", 0.0))))
            bw = int(self.st.get("border_width", 1))
            pen = QPen(QColor(self.st.get("border_color", "#3A4450")), bw) \
                if bw > 0 else Qt.NoPen
            p.setPen(pen)
            p.setBrush(QBrush(bg))
            r = self.w.rect().adjusted(0, 0, -1, -1)
            p.drawRoundedRect(r, 8, 8)

        # 把手：顶部整条，鼠标移上去高亮。**始终绘制**（不再只在调整模式），
        # 否则用户根本不知道哪里能拖 —— 这就是"移动困难"的根源。
        g = self._grip_rect()
        hover = getattr(self, "_hover_grip", False)
        p.setPen(Qt.NoPen)
        # 底条：默认很淡（不干扰看字幕），悬停时明显
        p.setBrush(QBrush(QColor(255, 255, 255, 46 if hover else 14)))
        p.drawRoundedRect(g, 4, 4)
        # 中间的点阵提示（每 9px 一个点），悬停时更亮
        p.setBrush(QBrush(QColor(255, 255, 255, 225 if hover else 90)))
        cy = g.center().y()
        for i in range(3):
            cx = g.center().x() - 10 + i * 10
            p.drawEllipse(cx - 1, cy - 1, 3, 3)
        p.end()

    # ── 字幕条目管理 ─────────────────────────────────────────
    def _entry_max_height(self):
        """单条字幕的高度上限（像素）。

        一个条目 = 译文若干行 + 原文若干行。行数由 max_lines_per_entry
        限制；**不设上限的话长句会一直折行**，把窗口撑到半个屏幕
        （实测 3 条 × 5 行 = 525px）。
        """
        per = int(self.st.get("max_lines_per_entry", 4))
        lh = float(self.st.get("line_height", 1.15))
        h = float(self.fs()) * lh * per
        if self.st.get("show_original"):
            h += float(self.fs_original()) * lh * per
        return int(max(30, h + 2))       # +2 与 minimum_height 的 slack 同源

    def add(self, en, zh="", hold=None, provisional=False):
        """加一条字幕：译文在上（大、亮），原文在下（小、暗）。

        provisional=True：流式模式的"临时条目"——后续 update_last() 会
        **就地更新**它，而不是每次局部结果都新起一条（否则同一句话的
        多个 partial 层层堆叠、max_lines 内互相顶掉，观感闪烁）。
        """
        from PyQt5.QtCore import Qt
        from PyQt5.QtWidgets import QLabel

        en = clean_caption(en, int(self.st["max_entry_chars"]))
        zh = clean_caption(zh, int(self.st["max_entry_chars"]))
        if not en and not zh:
            return
        self.clear_idle_hint()      # 有真字幕了，提示该撤掉
        lb = self._make_label()
        lb.setText(self._render_html(en, zh))
        # **单条高度上限**：长句折行不该把窗口撑大，超出部分在条目内裁掉。
        try:
            lb.setMaximumHeight(self._entry_max_height())
        except Exception:
            pass
        # 插在末尾 stretch 之前，这样内容整体靠底、向上长
        self.box.insertWidget(self.box.count() - 1, lb)
        self.entries.append([time.time(), en, zh, lb, hold or self.st["hold_seconds"]])
        if provisional:
            self._prov_lb = lb
        while len(self.entries) > int(self.st["max_lines"]):
            self._drop(0)
        self._relayout()

    def update_last(self, en, zh=""):
        """流式模式的局部结果：就地更新最后一条临时字幕，而不是新增。

        zh == PENDING_KEEP_OLD：译文还在途，只更新原文、保留旧译文。
        """
        en = clean_caption(en, int(self.st["max_entry_chars"]))
        keep_old = (zh == self.PENDING_KEEP_OLD)
        if not keep_old:
            zh = clean_caption(zh, int(self.st["max_entry_chars"]))
        lb = self._prov_lb
        if lb is not None:
            for e in self.entries:
                if e[3] is lb:
                    e[0] = time.time()          # 刷新时间，不过期
                    e[1] = en
                    if not keep_old:
                        e[2] = zh
                    try:
                        lb.setText(self._render_html(e[1], e[2]))
                    except Exception:
                        pass
                    self._relayout()
                    return
        # 没有可更新的临时条目 → 新起一条
        self.add(en, "" if keep_old else zh, provisional=True)

    def finalize_last(self):
        """流式：上一句已确认，临时条目转正；下一条 update_last 会新起一条。"""
        self._prov_lb = None

    def _drop(self, i):
        try:
            ts, en, zh, lb, _hold = self.entries.pop(i)
            if lb is self._prov_lb:
                self._prov_lb = None
            self.box.removeWidget(lb)
            lb.setParent(None)
            lb.deleteLater()
        except Exception:
            pass

    def _expire(self):
        """超时条目淡出（成熟方案的 fade_duration=5 秒）。

        hold=None 的条目（空状态提示）**永不过期**，要跳过 ——
        否则会 NameError/TypeError（None 不能和数字比大小），
        而且提示本来就应该一直在。
        """
        now = time.time()
        dead = [i for i, (ts, _, _, _, hold) in enumerate(self.entries)
                if hold is not None and now - ts > hold]
        if dead:
            for i in reversed(dead):
                self._drop(i)
            self._relayout()
            # 字幕全过期了 → 把空状态提示放回来
            if not self.entries:
                self.idle_hint()

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
            last_zh, last_en = "", ""
            for e in reversed(self.entries):
                if e[2]:
                    last_zh, last_en = e[2], e[1]
                    break
            # **只有新原文与上一条原文高度重叠才沿用旧译文。**
            # 原来无条件沿用：上一句的译文会被配到新句子的原文下面
            # （张冠李戴），新译文到达时又刷掉 —— 观感是字幕乱跳。
            if last_zh and _text_overlap(en, last_en):
                self.add(en, last_zh, **kw)
            else:
                # 没有可沿用的译文 → 只显示原文（空译文），等译文到达
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
        chk("字号基准 24（1080p → 36px）", o.st["font_size"] == 24)
        chk("基准高度 720（mpv 的窗口高度基准）", o.st["ref_height"] == 720)
        exp = max(10, int(round(24 * sh / 720)))
        chk(f"随屏幕等比缩放（{sh}p → {o.fs()}px，期望 {exp}px）", o.fs() == exp)
        chk("原文按比例缩小", o.fs_original() < o.fs())
        chk("行距收紧到 1.15", abs(o.st["line_height"] - 1.15) < 1e-6)
        chk("最多 3 条（从 5 减到 3）", o.st["max_lines"] == 3)
        # 回归：字幕**必须留到下一句替换**，不能自己消失。
        # 4 秒的保持时间实测会让字幕过期、然后只剩一条几乎看不见的
        # 暗淡提示，操作者报"字幕消失"就是这个。改为 600 秒（≈不过期）。
        # 回归：浮窗必须暴露自身显示状态。
        # 为什么：日志能证明"字幕产出了"，但证明不了"屏幕上显示了" ——
        # 操作者几次报"字幕消失"，我两次用像素判据都失败：
        #   ① 亮像素占比把背后浏览器窗口也算成字幕
        #   ② 按设置文件坐标裁剪，但那是 Qt 逻辑坐标、截屏是物理像素，
        #      缩放 125% 时差 20%（实测 288,734 960x77 vs 360,831 1200x165）
        # 现在让浮窗自己写 overlay_state.json，外部读它就能拿到真值。
        chk("浮窗有状态输出方法", hasattr(o, "_write_state"))
        chk("状态文件路径已定义", STATE.endswith("watch_overlay_state.json"))
        import json as _j
        o._write_state()
        _s1 = _j.load(open(STATE, encoding="utf-8"))
        chk("状态文件可读且 kind 合法",
            _s1.get("kind") in ("subtitle", "idle", "empty"))
        chk("空浮窗 -> kind=empty", _s1.get("kind") == "empty")
        o.add("hello", "你好")
        _s2 = _j.load(open(STATE, encoding="utf-8"))
        chk("有字幕 -> kind=subtitle", _s2.get("kind") == "subtitle")
        chk("状态里带译文", _s2.get("zh") == "你好")
        chk("状态里带时间戳", float(_s2.get("ts", 0)) > 0)

        # ── 第⑤项 A+B：多屏 bounds 与最小高度（参考 Nola windows.ts）──
        # 回归：**不能再用 primaryScreen() 夹取** —— 浮窗拖到副屏后，
        # 主屏的范围里没有它，会被拽回主屏或夹到屏外。
        chk("有 _screen_for（按实际所在屏判断）", hasattr(o, "_screen_for"))
        chk("有 _compute_bounds", hasattr(o, "_compute_bounds"))
        # 回归：长字幕**不能**把窗口撑到半个屏幕。
        # 实测失控过一次：3 条 × 5 行 = 525px（占屏高 49%），
        # 位置也从贴底被推到屏幕中部。
        _cap = o.content_height_cap()
        chk("窗口高度上限存在且不超过屏高 1/3（%dpx）" % _cap,
            _cap <= o._screen_h() / 3 + 1)
        chk("窗口高度上限有下限 120", _cap >= 120)
        _eh = o._entry_max_height()
        chk("单条高度上限存在（%dpx）" % _eh, _eh >= 30)
        _st1 = dict(o.st)
        try:
            o.st["max_lines_per_entry"] = 2
            _e2 = o._entry_max_height()
            o.st["max_lines_per_entry"] = 8
            _e8 = o._entry_max_height()
            chk("单条上限随 max_lines_per_entry 增大（%d < %d）" % (_e2, _e8),
                _e8 > _e2)
            o.st["max_lines_per_entry"] = 20
            chk("上限不会失控（<= 屏高 1/3 的 2 倍）",
                o.content_height_cap() <= o._screen_h())
        finally:
            o.st.clear()
            o.st.update(_st1)

        chk("有 minimum_height", hasattr(o, "minimum_height"))
        from PyQt5.QtWidgets import QApplication as _QA
        _r = _QA.primaryScreen().availableGeometry()
        # 屏外坐标必须被夹回屏内
        for _cx, _cy, _lbl in ((99999, 99999, "远处"),
                               (-5000, -5000, "负坐标")):
            _x, _y, _w, _h = o._compute_bounds("free", _r, 1200, 200,
                                               _cx, _cy)
            chk("坐标%s被夹回屏内" % _lbl,
                _r.left() <= _x <= _r.left() + _r.width() - _w
                and _r.top() <= _y <= _r.top() + _r.height() - _h)
        # 三种模式
        _xt, _yt, _wt, _ht = o._compute_bounds("top", _r, 800, 200, None, None)
        _xb, _yb, _wb, _hb = o._compute_bounds("bottom", _r, 800, 200,
                                               None, None)
        chk("top 模式贴顶（留边距）", _yt == _r.top() + o.OVERLAY_MARGIN)
        chk("bottom 模式贴底（留边距）",
            _yb == _r.top() + _r.height() - _hb - o.OVERLAY_MARGIN)
        chk("top 与 bottom 不重合", _yt != _yb)
        # 超大尺寸被夹到屏幕内
        _x2, _y2, _w2, _h2 = o._compute_bounds("free", _r, 99999, 99999,
                                               0, 0)
        chk("超大尺寸被夹到工作区内",
            _w2 <= _r.width() and _h2 <= _r.height())
        # 最小高度公式：字号越大越高；显示原文比只显示译文高
        _st0 = dict(o.st)
        try:
            o.st["font_size"] = 18
            o.st["show_original"] = True
            _m18 = o.minimum_height()
            o.st["font_size"] = 48
            _m48 = o.minimum_height()
            chk("最小高度随字号增大（%d < %d）" % (_m18, _m48), _m48 > _m18)
            o.st["show_original"] = False
            _off = o.minimum_height()
            chk("不显示原文时最小高度更低（%d < %d）" % (_off, _m48),
                _off < _m48)
            chk("最小高度有下限 60", _m18 >= 60)
        finally:
            o.st.clear()
            o.st.update(_st0)

        chk("字幕留到下一句替换（hold_seconds 足够大）",
            o.st["hold_seconds"] >= 60.0)
        chk("保持时间能配到不过期（默认值即为上限内）",
            o.st["hold_seconds"] <= 3600.0)
        # 默认排布：**中文在上、原语在下**（操作者明确要的）。
        # 我之前默认 inline（左右两列）是为了省高度 —— 那是我的偏好，
        # 不是他要的，已改回 stacked。
        chk("默认排布 = 上下两行（中文在上、原语在下）",
            o.st["layout"] == "stacked")
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
    print("\n  空状态提示（没有音频时也能看出程序在跑）")
    o.clear()
    _IH = QtOverlay.IDLE_HINT
    o.idle_hint()
    chk("提示已加", any(e[1] == _IH for e in o.entries))
    chk("提示让窗口有可见高度", o.geo[3] > 60)
    o._expire()
    chk("hold=None 的提示不被过期清理", any(e[1] == _IH for e in o.entries))
    o.set("real sub", "真字幕")
    chk("有真字幕时提示被撤", not any(e[1] == _IH for e in o.entries))
    o.clear()

    print("\n  拖拽把手")
    g = o._grip_rect()
    # 把手已从"右上角小方块"改成"顶部整条"（操作者反馈拖动困难）：
    # 整条都能按到，不用瞄准。断言跟着改成"横跨整宽 + 够高"。
    chk("把手横跨整宽（不是小方块）",
        g.left() == 0 and g.width() >= o.w.width() - 2)
    chk("把手贴顶", g.top() <= 2)
    chk("把手够高（好按到）", g.height() >= 16)
    chk("把手在窗口内", g.top() >= 0 and g.height() <= o.w.height())
    chk("按把手中心命中", o._in_grip(g.center()))
    from PyQt5.QtCore import QPoint as _QP
    chk("按窗口正中不命中（把手只在顶部）",
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
    chk("可重置", o.st["font_size"] == 24)
    o.close()
    chk("设置落盘", os.path.exists(SETTINGS))
    # 自检跑完，把用户原来的设置放回去（自检不该改用户的配置）
    if _saved_settings is not None:
        open(SETTINGS, "w", encoding="utf-8", newline="").write(_saved_settings)
    print(f"\n合计 {ok} 通过 / {fail} 失败")
    sys.exit(0 if fail == 0 else 1)
    demo(0)
