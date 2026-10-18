# -*- coding: utf-8 -*-
"""watch_player.py —— 预翻译字幕的浮窗同步观看器。

与直播路线（live_subtitle_v2.py）的分工
========================================
直播：音频边播边采，识别翻译都在追时间 —— 用 start.bat。
点播：整片已经预翻译好（pretranslate.py 产出的双语 srt），
      浮窗唯一要解决的问题是**"现在播到第几秒"** —— 用本脚本。

播放位置的三种来源（自动选择）
==============================
① mpv IPC（本地文件 + 有 mpv 时，最准）：
   由本脚本启动 mpv 并挂上命名管道，每 100ms 读一次 time-pos。
   暂停/快进/拖动全部天然正确（位置是播放器自己报的）。
② Windows SMTC（浏览器看在线视频、PotPlayer 等，全自动）：
   播放器/浏览器会把"播到第几秒"上报给系统媒体会话，直接读它。
   **什么都不用按**：开播自动对上，暂停/拖动自动跟随。
   实测夸克浏览器时间线精度 ±0.1s。
③ 手动时钟（冷门播放器兜底）：
     空格      开始/暂停（视频开始播的那一刻按一下）
     ←  →     校准 ±2 秒（发现字幕偏了就拍一下）
     ↑  ↓     微调 ±0.5 秒
     Home     回到 0 秒重新对

--auto 常驻模式
---------------
开着它，随便用什么看什么：SMTC 报出标题就自动去 pre_cache/ 找同名
字幕（模糊匹配），有缓存就自动跟上 —— **凡是有缓存的视频，字幕
自动出现**，不用每次手动启动。

用法
----
    python watch_player.py 电影.mp4                 # 自动找 电影.zh.srt，没有则先预翻译
    python watch_player.py https://www.bilibili.com/video/BVxxx/
    python watch_player.py 电影.zh.srt --clock      # 强制手动时钟
    python watch_player.py 电影.mp4 --mpv D:\\tools\\mpv.exe
    python watch_player.py --auto                   # 常驻：有缓存自动出字幕
"""
from __future__ import annotations

import argparse
import bisect
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

PIPE_NAME = r"\\.\pipe\live-subtitle-mpv"
from subtitle_layout import split_cue as _split_cue_raw


def _split_cue_display(start, end, zh, src, max_chars=24):
    return _split_cue_raw(start, end, zh, src, max_chars=max_chars)
LOG_PATH = os.path.join(HERE, "watch.log")

# 自动预翻译的节流：同一个视频失败后隔多久再试、最多试几次。
# 为什么需要：网址可能刚打开、历史库还没落盘，或站点临时抽风 ——
# 立刻反复重试只会把日志刷爆，等一会儿再看往往就有。
AUTO_RETRY_COOLDOWN = 20.0
AUTO_MAX_TRIES = 3
# no_url（历史里查不到网址）单独计策：它多半不是真没有，而是浏览器
# 历史还没落盘 —— 晚几十秒再查往往就有了。所以 no_url **不耗**上面
# 的 3 次真尝试，独立计数、45s 冷却、20 次封顶（≈15 分钟窗口，
# 这期间历史一落盘就自动接上，不用用户手动重开）。
AUTO_NOURL_COOLDOWN = 45.0
AUTO_NOURL_MAX_TRIES = 20


def _log(msg):
    """把观看器的状态同时写到控制台和 watch.log。

    为什么需要：双击 watch.bat 时控制台一闪而过或没人盯着看，
    "有缓存却不显示字幕"到底是没检测到会话、标题不匹配、还是
    会话不报时间线，光看浮窗看不出来。写一份日志就能事后定位。
    """
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _engine_stop_requested():
    """管理端（桌面/后台）叫停：写 work/engine_stop.flag。

    watch 由 babel_admin.serve 用 CREATE_NO_WINDOW 拉起，没有控制台，
    CTRL_BREAK 送不进来；和直播引擎一样用文件哨兵，tick 每 100ms 查一次，
    退出走正常 Qt 收尾（关浮窗、关 tracker/autocache）。
    """
    try:
        return os.path.isfile(os.path.join(HERE, "work", "engine_stop.flag"))
    except OSError:
        return False


# ══════════════════════════════════════════════════════════
# srt 解析
# ══════════════════════════════════════════════════════════
_TS_RE = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)")


def _to_sec(m):
    return (int(m.group(1)) * 3600 + int(m.group(2)) * 60
            + int(m.group(3)) + int(m.group(4)) / 1000.0)


def _line_chars_pref(default=24):
    try:
        d = json.load(open(os.path.join(HERE, "watch_overlay.json"),
                           encoding="utf-8"))
        return max(8, min(60, int(d.get("line_chars", default))))
    except Exception:
        return default


def _merge_two_lines(cues, line_chars=24):
    """相邻两条短 cue 合成一条两行字幕。

    为什么：切短后如果每条都单独一闪，观感是“碎句”；标准字幕是
    **一条字幕最多两行、停留 2~7 秒**。这里把间隔很短的相邻两条合成
    一条，两行分别来自两条 cue；只合并一对，不会堆成 4 行。
    字幕内容不变，只是排版更像人工字幕。
    """
    out = []
    i = 0
    while i < len(cues):
        st, en, zh, src = cues[i]
        if i + 1 < len(cues):
            nst, nen, nzh, nsrc = cues[i + 1]
            gap = nst - en
            dur = nen - st
            zlen = len((zh + nzh).replace("\n", ""))
            slen = len((src + nsrc).replace("\n", ""))
            if (gap <= 0.35 and dur <= 7.0
                    and zlen <= line_chars * 2 + 2
                    and slen <= line_chars * 2 + 8
                    and "\n" not in zh and "\n" not in nzh):
                out.append((st, nen, (zh + "\n" + nzh).strip(),
                            (src + "\n" + nsrc).strip()))
                i += 2
                continue
        out.append((st, en, zh, src))
        i += 1
    return out


def _read_text_retry(path, attempts=6):
    """读取预翻译正在写的 srt：Windows 上偶发 PermissionError，重试即可。

    预翻译用原子 replace 写盘，但极少数情况下旧文件句柄未释放/杀软扫描
    会让读端拿到 PermissionError；tick 每 2 秒重读，一次失败不该让浮窗
    "卡死"或丢掉整批字幕。
    """
    last = None
    for i in range(attempts):
        try:
            with open(path, encoding="utf-8-sig", errors="replace") as f:
                return f.read()
        except PermissionError as e:
            last = e
            time.sleep(0.03)
        except OSError as e:
            last = e
            time.sleep(0.03)
    if last:
        raise last
    return ""


def parse_srt(path, line_chars=24):
    """返回 [(start, end, zh, src)]；line_chars = 单行字幕长度目标。
    也兼容只有一行（当原文）的普通 srt。"""
    try:
        txt = _read_text_retry(path)
    except OSError:
        # 预翻译正在写盘/杀软短暂锁文件：这一拍先不更新，tick 下一拍再读，
        # 绝不让一次读失败把整个浮窗 tick 打断（用户看到的就是“卡死”）。
        return []
    cues = []
    for block in re.split(r"\r?\n\s*\r?\n", txt):
        lines = [l.strip() for l in block.strip().splitlines() if l.strip()]
        if len(lines) < 2:
            continue
        tm = [_to_sec(m) for m in _TS_RE.finditer(lines[1])]
        # 有些 srt 序号行被省略，时间行就是第一行
        if len(tm) < 2:
            tm = [_to_sec(m) for m in _TS_RE.finditer(lines[0])]
            body = lines[1:]
        else:
            body = lines[2:]
        if len(tm) < 2 or not body:
            continue
        zh = body[0] if len(body) >= 2 else ""
        src = body[1] if len(body) >= 2 else body[0]
        cues.append((tm[0], tm[1], zh, src))
    cues.sort(key=lambda c: c[0])
    # 视频翻译的 cue 常常 8~16 秒、40~55 字一条；显示端切成 2~5.5 秒、
    # 每条约 24 个汉字，避免浮窗/播放器硬折行把中文从词中间劈开。
    pretty = []
    for st, en, zh, src in cues:
        pretty.extend(_split_cue_display(st, en, zh, src,
                                         max_chars=line_chars))
    return _merge_two_lines(pretty, line_chars)


def no_url_backoff(tried, nt, now=None, max_tries=AUTO_NOURL_MAX_TRIES,
                   cooldown=AUTO_NOURL_COOLDOWN,
                   base_cooldown=AUTO_RETRY_COOLDOWN):
    """处理一次 no_url 事件：独立计数、不耗真尝试次数、推迟下次重查。

    为什么单独一套计数：no_url 多半不是"真没有"，而是浏览器历史还没
    落盘（视频刚打开时）。若和识别失败共用 AUTO_MAX_TRIES=3，历史晚
    十几秒落盘就永远没机会了 —— 用户观感就是"老是查不到网址"。

    返回 (还会不会重试, 更新后的记录)。
    """
    now = time.time() if now is None else now
    tr = dict(tried.get(nt) or {})
    tr["n"] = int(tr.get("n", 0))
    tr["ts"] = float(tr.get("ts", 0.0))
    tr["nu"] = int(tr.get("nu", 0)) + 1
    retry = tr["nu"] <= max_tries
    if retry:
        tr["n"] = max(0, tr["n"] - 1)     # 退回这次真尝试的计数
        tr["ts"] = now + (cooldown - base_cooldown)
    tried[nt] = tr
    return retry, tr


def _file_sig(path):
    """文件的"内容签名"（mtime + 大小），用来判断 srt 有没有增长。

    预翻译现在是分块流水的：srt 会边译边长。观看端每 2 秒看一次签名，
    变了就重读 —— 新译好的段落立刻能显示，不必等整片跑完。
    """
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _sub_on_screen(ov):
    """浮窗上现在有没有**真字幕**（状态提示 / 空状态提示都不算）。

    判据只看"原文行"：真字幕由 `ov.set(原文, 译文)` 加进来，原文一定非空；
    而所有提示（状态 / 浮窗自带的 idle_hint）原文行都是空串或 IDLE_HINT。

    为什么要这个判断：浮窗可能被清过（状态提示走过 clear、或条目过期），
    此时"当前显示到第几条"（idx）没变，显示函数会直接 return ——
    字幕就**永远回不来**。有了它就能发现"屏上其实空了"，强制补画。
    """
    try:
        for e in ov.entries:
            if e[1] and e[1] != ov.IDLE_HINT:
                return True
        return False
    except Exception:
        return True              # 读不到就当在屏上，别乱重画


# ══════════════════════════════════════════════════════════
# 位置源①：mpv IPC（Windows 命名管道，JSON 一行一报文）
# ══════════════════════════════════════════════════════════
class MpvIPC:
    def __init__(self, mpv_path, video_path):
        args = [mpv_path, "--really-quiet",
                f"--input-ipc-server={PIPE_NAME}",
                "--sid=no",                 # 字幕由浮窗显示，避免双重
                "--keep-open=yes", video_path]
        self.proc = subprocess.Popen(
            args,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.pipe = None
        self.ok = False
        t0 = time.time()
        while time.time() - t0 < 10:
            if self.proc.poll() is not None:
                break
            try:
                self.pipe = open(PIPE_NAME, "r+b", buffering=0)
                self.ok = True
                break
            except OSError:
                time.sleep(0.2)
        if not self.ok:
            raise RuntimeError("mpv 启动了但管道连不上")

    def _cmd(self, *c):
        self.pipe.write((json.dumps({"command": list(c)}) + "\n").encode())
        # 管道里可能混着事件报文，找到带 error 字段的应答
        deadline = time.time() + 1.0
        buf = b""
        while time.time() < deadline:
            chunk = (self.pipe.read1(4096)
                     if hasattr(self.pipe, "read1") else self.pipe.read(4096))
            if not chunk:
                continue
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    j = json.loads(line.decode("utf-8", "replace"))
                except Exception:
                    continue
                if "error" in j:
                    return j.get("data")
        return None

    def position(self):
        v = self._cmd("get_property", "time-pos")
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def seek(self, delta):
        try:
            self._cmd("seek", delta, "relative")
        except Exception:
            pass

    def alive(self):
        return self.proc.poll() is None

    def close(self):
        try:
            if self.alive():
                self.proc.terminate()
        except Exception:
            pass


# ══════════════════════════════════════════════════════════
# 位置源②：SMTC（浏览器/任意播放器自动跟随）+ 手动时钟兜底
# ══════════════════════════════════════════════════════════
class AutoClock:
    """优先读系统媒体会话（SMTC）的位置——浏览器播 B 站、PotPlayer 播
    本地文件都会自动上报，暂停/拖动天然跟随；读不到时退回手动时钟。

    这样用户什么都不用按：视频一开始播，字幕自动对上。
    """

    def __init__(self, title_hint=""):
        from smtc_tracker import SmtcTracker, available
        self.manual = ManualClock()
        self.smtc = None
        if available():
            self.smtc = SmtcTracker(title_hint=title_hint)
            self.smtc.start()

    def position(self):
        if self.smtc is not None:
            p = self.smtc.position()
            if p is not None:
                return p
        return self.manual.position() if self.manual.playing else None

    def smtc_live(self):
        return self.smtc is not None and self.smtc.position() is not None

    def describe(self):
        if self.smtc is not None and self.smtc.has_session():
            return self.smtc.describe()
        return None

    # 手动校准只作用于兜底时钟
    def toggle(self):
        self.manual.toggle()

    def seek(self, d):
        self.manual.seek(d)

    def reset(self):
        self.manual.reset()

    def close(self):
        if self.smtc is not None:
            self.smtc.close()


class ManualClock:
    def __init__(self):
        self.playing = False
        self.base = 0.0
        self.t0 = None

    def position(self):
        if self.playing and self.t0 is not None:
            return self.base + (time.time() - self.t0)
        return self.base

    def toggle(self):
        if self.playing:
            self.base = self.position()
            self.playing = False
        else:
            self.t0 = time.time()
            self.playing = True

    def seek(self, delta):
        pos = self.position() + delta
        self.base = max(0.0, pos)
        if self.playing:
            self.t0 = time.time()

    def reset(self):
        self.base = 0.0
        if self.playing:
            self.t0 = time.time()


# ══════════════════════════════════════════════════════════
# 进度拖动检测（任意拖进度条，字幕立刻重新对齐）
# ══════════════════════════════════════════════════════════
class _JumpDetector:
    """比较相邻两拍的播放位置，判断"用户拖了进度条"。

    为什么需要：字幕按位置 bisect 命中 cue 后，idx 没变就跳过不重画；
    而拖动之后浮窗里还残留着拖动前滚动的旧条目，两者叠加的观感就是
    "内容对不上"。检测到跳变后由调用方 clear() + 强制重画当前 cue。

    判据：|本拍位置 - 上拍位置| > threshold + 两拍间的自然流逝。
    正常播放每拍只走 0.1s（tick 是 10Hz），永远不会误报；
    暂停时位置不动、drift 再大也不会误报。
    """

    def __init__(self, threshold=2.0):
        self.threshold = float(threshold)
        self.prev = None
        self.prev_t = 0.0

    def feed(self, pos, now=None):
        if pos is None:
            return False                      # 没位置不算跳变，也别覆盖基准
        now = time.monotonic() if now is None else now
        jumped = False
        if self.prev is not None:
            drift = max(0.0, now - self.prev_t)
            if abs(pos - self.prev) > self.threshold + drift:
                jumped = True
        self.prev, self.prev_t = pos, now
        return jumped

    def reset(self):
        self.prev = None
        self.prev_t = 0.0


# ══════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════

def _find_srt_for(video_path, dst):
    stem = os.path.splitext(os.path.abspath(video_path))[0]
    p = stem + f".{dst}.srt"
    return p if os.path.isfile(p) else None


def _run_pretranslate(src, dst):
    """srt 不存在时现场跑预翻译（有缓存会秒过）。"""
    print(f"  没有找到现成字幕，先跑预翻译 …")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", HF_HUB_OFFLINE="1")
    r = subprocess.run(
        [sys.executable, "-u", os.path.join(HERE, "pretranslate.py"),
         src, "--to", dst],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    sys.stdout.write(r.stdout)
    if r.returncode != 0:
        sys.stdout.write(r.stderr)
        raise SystemExit("预翻译失败，无法进入观看模式")
    m = re.findall(r"完成：(.+?\.srt)\s*$", r.stdout, re.M)
    if not m:
        # 缓存命中时输出里没有"完成："，换一种找法
        m = re.findall(r"输出：(.+?\.srt)\s*$", r.stdout, re.M)
    if m and os.path.isfile(m[-1]):
        return m[-1]
    raise SystemExit("预翻译跑完了但找不到产出的 srt")


def _find_mpv(explicit=None):
    if explicit and os.path.isfile(explicit):
        return explicit
    p = shutil.which("mpv") or shutil.which("mpv.exe")
    if p:
        return p
    for c in (r"C:\Program Files\mpv\mpv.exe",
              os.path.expanduser(r"~\scoop\apps\mpv\current\mpv.exe")):
        if os.path.isfile(c):
            return c
    return None


def main():
    ap = argparse.ArgumentParser(description="预翻译字幕的浮窗同步观看器")
    ap.add_argument("source", nargs="?", default=None,
                    help="视频文件 / srt 文件 / 网页 URL")
    ap.add_argument("--video", default=None,
                    help="source 是 srt 时，对应的视频文件（mpv 模式用）")
    ap.add_argument("--to", dest="dst", default="zh")
    ap.add_argument("--mpv", default=None, help="mpv.exe 路径")
    ap.add_argument("--clock", action="store_true",
                    help="强制手动时钟（即使有 mpv）")
    ap.add_argument("--auto", action="store_true",
                    help="常驻模式：任何在播视频，pre_cache 里有字幕就自动跟上")
    ap.add_argument("--no-pretranslate", action="store_true",
                    help="常驻模式下只匹配已有缓存，不自动反查网址并预翻译")
    args = ap.parse_args()

    if args.auto:
        _auto_main(args)
        return
    if not args.source:
        ap.error("需要 视频文件 / srt / URL，或用 --auto 常驻模式")

    src = args.source
    is_url = bool(re.match(r"^https?://", src))
    is_srt = src.lower().endswith(".srt") and os.path.isfile(src)

    # ── 定位 srt ──
    if is_srt:
        srt_path = src
        video = args.video
    elif is_url:
        srt_path = _run_pretranslate(src, args.dst)
        video = None                  # URL 没有本地视频，mpv 模式不适用
    else:
        video = os.path.abspath(src)
        srt_path = _find_srt_for(video, args.dst)
        if srt_path is None:
            srt_path = _run_pretranslate(video, args.dst)

    cues = parse_srt(srt_path)
    if not cues:
        raise SystemExit(f"srt 里没有字幕：{srt_path}")
    dur = max(c[1] for c in cues)
    print(f"字幕: {srt_path}")
    print(f"      {len(cues)} 条，覆盖到 {dur/60:.1f} 分钟")
    _log("=" * 44)
    _log(f"watch 启动: {srt_path}（{len(cues)} 条，覆盖 {dur/60:.1f} 分钟）")

    # ── 位置源 ──
    pos_src = None
    mpv = None if (args.clock or is_url) else _find_mpv(args.mpv)
    if mpv and video:
        try:
            pos_src = MpvIPC(mpv, video)
            print(f"位置源: mpv IPC（{mpv}）— 暂停/拖动自动跟随")
        except Exception as e:
            print(f"mpv 启动失败（{e}），改用自动时钟")
            pos_src = None
    if pos_src is None:
        # 标题提示：拿 srt 文件名（去掉 .zh.srt）去认系统媒体会话
        hint = re.sub(r"\.[a-z]{2,3}\.srt$", "", os.path.basename(srt_path),
                      flags=re.I)
        pos_src = AutoClock(title_hint=hint)
        print("位置源: 系统自动同步（SMTC）——视频开播即自动对上，"
              "暂停/拖动自动跟随")
        print("        认不到会话时退回手动：【空格】对齐，←/→ ±2s，"
              "↑/↓ ±0.5s，Home 归零")
        _log(f"位置源: AutoClock（提示标题 {hint!r}）")

    # ── 浮窗 + 同步循环 ──
    # 用 watch 专属的浮窗复刻（watch_overlay.py），与回环共用同一套显示
    # 逻辑，但设置/状态文件独立 —— 两边互不覆写，见该文件头说明。
    from watch_overlay import QtOverlay
    from PyQt5.QtCore import QTimer, Qt
    ov = QtOverlay()
    # Qt 默认"最后一个窗口关闭 → 退出事件循环"。浮窗反复 clear/flash
    # （状态提示）时会销毁瞬时 QLabel，实测会把整个观看器带走
    # （log 里先 lastWindowClosed 再 watch 退出）。观看器常驻，退出只认
    # Esc / Ctrl+C，因此关掉这个默认行为。
    ov.app.setQuitOnLastWindowClosed(False)
    ov.flash("字幕已加载 —— 与播放位置同步中", 3.0)

    starts = [c[0] for c in cues]
    state = {"idx": -2}          # 当前显示的 cue 序号（-2=无）

    def _show(i):
        """与回环路线**同一套显示观感**：字幕滚动保留最近若干条
        （条数由浮窗的 max_lines 管），句与句之间的空隙**不清屏**。

        为什么这样改：原来这里是"一进一出"—— 新句 clear() + set()、
        间隙 _show(None) 再 clear()。屏幕会周期性出现空白，长句间隙稍大
        就像"字幕挂了"。回环路线没有这个问题，正是因为它从不清屏、只让
        旧条目被 max_lines 挤出去（见浮窗 _expire）。所以点播照它对齐。
        """
        if i is None or i < 0:
            # 间隙：保留上一句，且**不动 idx**，否则同一句会被重复添加
            return
        # 自愈：屏上已经没有真字幕了（条目过期清掉过）—— idx 没变也得重画
        if not _sub_on_screen(ov):
            state["idx"] = -2
        if i == state["idx"]:
            return
        state["idx"] = i
        _st, _en, zh, src_txt = cues[i]
        # 不传 hold → 用配置里的 hold_seconds（回环默认 600s）。
        # 显示与清除由 max_lines 滚动控制，不让浮窗的过期机制把长句撤掉
        ov.set(src_txt, zh)

    _warn = {"pos": False}
    _jump = _JumpDetector()
    _sync = {"seek_n": 0}

    def _resync_if_jumped(pos):
        """进度条被拖动时，清掉残留的旧条目并强制按新位置重画。

        双通道检测：
          ① 位置跳变（_JumpDetector）—— 对 mpv / SMTC / 手动时钟都有效；
          ② SMTC 的锚点级 seek 事件 —— 比位置跳变更早半拍感知
             （浏览器一上报新锚点就触发，不等位置差累积）。
        """
        jumped = _jump.feed(pos)
        smtc = getattr(pos_src, "smtc", None)
        if smtc is not None:
            n = smtc.seek_serial()
            if n != _sync["seek_n"]:
                _sync["seek_n"] = n
                jumped = True
        if jumped and pos is not None:
            ov.clear()
            state["idx"] = -2
            _log(f"检测到进度拖动 → 重新对齐 pos={pos:.1f}s")

    def _tick():
        try:
            if isinstance(pos_src, MpvIPC) and not pos_src.alive():
                _log("mpv 已退出，观看结束")
                ov.app.quit()
                return
            pos = pos_src.position()
            _resync_if_jumped(pos)
            if pos is None:
                if not _warn["pos"]:
                    _warn["pos"] = True
                    _log("等待位置：系统未上报时间线，"
                         "可点浮窗按【空格】手动对齐起点")
                return
            _warn["pos"] = False
            i = bisect.bisect_right(starts, pos) - 1
            if i >= 0 and pos <= cues[i][1] + 0.35:
                _show(i)
            else:
                _show(None)      # 间隙：静默跳过（保留上一句，不清屏）
        except Exception as e:
            _log(f"tick 异常: {type(e).__name__}: {e}")

    # 手动模式的校准键：包一层原键盘处理（其余按键行为不变）
    _orig_key = ov.w.keyPressEvent

    def _key(e):
        k = e.key()
        if k == Qt.Key_Escape:
            ov.app.quit()          # 关掉 quitOnLastWindowClosed 后要显式退
            return
        if isinstance(pos_src, MpvIPC):
            # mpv 模式：←/→ 直接让 mpv 自己 seek，位置它自己会上报
            if k == Qt.Key_Left:
                pos_src.seek(-2.0)
                return
            if k == Qt.Key_Right:
                pos_src.seek(2.0)
                return
        else:
            # AutoClock：SMTC 活着时位置归系统管，按键只用于
            # SMTC 认不到会话时的手动兜底
            if pos_src.smtc_live():
                _orig_key(e)
                return
            if k == Qt.Key_Space:
                pos_src.toggle()
                ov.flash("▶ 时钟走起来" if pos_src.manual.playing
                         else "⏸ 已暂停", 1.2)
                return
            if k == Qt.Key_Left:
                pos_src.seek(-2.0)
                return
            if k == Qt.Key_Right:
                pos_src.seek(2.0)
                return
            if k == Qt.Key_Up:
                pos_src.seek(0.5)
                return
            if k == Qt.Key_Down:
                pos_src.seek(-0.5)
                return
            if k == Qt.Key_Home:
                pos_src.reset()
                return
        _orig_key(e)

    ov.w.keyPressEvent = _key

    timer = QTimer()
    timer.timeout.connect(_tick)
    timer.start(100)             # 10Hz：人眼对字幕同步的感知阈值约 120ms
    ov.app.exec_()


# ══════════════════════════════════════════════════════════
# --auto 常驻模式：任何在播视频，缓存里有字幕就自动跟上
# ══════════════════════════════════════════════════════════

def _norm_title(s):
    """标题归一化。**与 browser_history 用同一实现** —— 因为自动管线的
    任务键、缓存文件匹配、历史库反查三处必须用同一把尺子，否则同一个
    视频在三处会被归一成不同字符串，互相认不出。"""
    from browser_history import norm_title
    return norm_title(s)


def _cached_names(dst="zh"):
    """pre_cache 里现有的字幕文件名（诊断用）。"""
    pre = os.path.join(HERE, "pre_cache")
    if not os.path.isdir(pre):
        return []
    return [f for f in os.listdir(pre)
            if f.lower().endswith(f".{dst}.srt")]


#: "第几话 / 第几集 / EP12" 的序号。**必须有** —— 标题高度相似的连播剧集，
#  只差这一个字（第1话 vs 第2话），泛匹配会把整集字幕配错。
_SERIAL_RE = re.compile(
    r"第\s*([0-9]{1,4}|[一二三四五六七八九十百零两]{1,3})\s*[话話集期回]"
    r"|(?<![a-z0-9])(?:ep|episode|e)\s*0*([0-9]{1,4})", re.I)
_CN_DIGIT = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _serial(s):
    """从标题 / 文件名里提"第几话（集）"。提不到返回 None。"""
    m = _SERIAL_RE.search(s or "")
    if not m:
        return None
    a, b = m.group(1), m.group(2)
    if b:
        return int(b)
    if a.isdigit():
        return int(a)
    if "十" in a:                      # 十 / 十二 / 二十 / 二十三
        h, _, t = a.partition("十")
        return ((_CN_DIGIT.get(h, 1) if h else 1) * 10
                + (_CN_DIGIT.get(t, 0) if t else 0))
    return _CN_DIGIT.get(a)


def _find_cached_srt(title, dst="zh"):
    """在 pre_cache 里按标题模糊匹配字幕文件。

    **集号闸门**：实测『fx战士久留美』第2话 被泛匹配到了第1话的字幕 ——
    两部标题几乎一样、只差一个数字，SequenceMatcher 轻松过 0.6，结果
    整集字幕的内容都是错的（用户观感就是"翻译的内容不对/没出来"）。
    规则：两边都有集号就必须相等；只有一边有也判为不匹配。
    """
    import difflib
    pre = os.path.join(HERE, "pre_cache")
    want = _norm_title(title)
    if not want:
        return None
    want_ser = _serial(title)
    best, best_r = None, 0.0
    for f in os.listdir(pre) if os.path.isdir(pre) else []:
        if not f.lower().endswith(f".{dst}.srt"):
            continue
        raw = re.sub(rf"\.{dst}\.srt$", "", f, flags=re.I)
        have = _norm_title(raw)
        if not have:
            continue
        have_ser = _serial(raw)
        if (want_ser is None) != (have_ser is None):
            continue
        if want_ser is not None and want_ser != have_ser:
            continue
        r = 1.0 if (want in have or have in want) \
            else difflib.SequenceMatcher(None, want, have).ratio()
        if r > best_r:
            best, best_r = os.path.join(pre, f), r
    return best if best_r >= 0.6 else None


def _auto_main(args):
    """常驻：SMTC 报什么标题，就去 pre_cache 找什么字幕。

    使用姿势：开着它（可最小化），然后随便用什么看什么 ——
    浏览器里的 B 站 / YouTube、PotPlayer 里的本地片，只要之前
    用 cache.bat 预翻译过，字幕自动出现并跟着播放位置走。
    """
    from smtc_tracker import SmtcTracker, available
    if not available():
        raise SystemExit("--auto 需要 winsdk：pip install winsdk")
    from watch_overlay import QtOverlay
    from PyQt5.QtCore import QTimer, Qt

    tracker = SmtcTracker(poll=0.15, log=_log)   # 位置感知更快一档（原 0.25）
    tracker.start()
    ov = QtOverlay()
    # 常驻模式：别因为某个瞬时子窗口关闭就整体退出。
    # Qt 默认"最后一个窗口关闭→quit"，而浮窗在反复 clear/flash（状态提示）
    # 时会 setParent(None)/销毁临时 QLabel；实测 watch 启动 1 秒就自己退出
    # （日志先 lastWindowClosed 再 watch 退出）。退出只认 Esc / Ctrl+C。
    ov.app.setQuitOnLastWindowClosed(False)
    _orig_key = ov.w.keyPressEvent

    def _key(e):
        if e.key() == Qt.Key_Escape:
            ov.app.quit()
            return
        # 本地时钟兜底时的手动校正：Home 从当前时刻归零；←/→ ±2s
        if clock["on"] and e.key() == Qt.Key_Home:
            clock["base"] = 0.0
            clock["t0"] = time.monotonic()
            clock["offset"] = 0.0
            _log("本地时钟：从当前时刻归零")
            return
        if clock["on"] and e.key() in (Qt.Key_Left, Qt.Key_Right):
            clock["offset"] += (-2.0 if e.key() == Qt.Key_Left else 2.0)
            _log(f"本地时钟校正: offset={clock['offset']:+.1f}s")
            return
        _orig_key(e)

    ov.w.keyPressEvent = _key
    try:                                     # 清掉上一次的残留
        os.remove(os.path.join(HERE, "work", "engine_stop.flag"))
    except OSError:
        pass
    _log("=" * 44)
    _log("watch --auto 启动")
    # SMTC 会话可能“有标题/在播，但不上报 position”（夸克部分视频、广告、
    # 直播回放都会这样）。此时不能只会显示“等待播放位置”，否则用户看到
    # 的就是视频翻译完全没字幕。用本地时钟先跑起来，并允许 Home/←→ 校正。
    clock = {"on": False, "base": 0.0, "t0": time.monotonic(),
             "playing": False, "offset": 0.0, "last_pos": 0.0,
             "warned": False}
    def _sync_to(sec):
        """面板“视频当前时间”手动对齐：把本地时钟设到该位置。"""
        clock.update(on=True, base=max(0.0, float(sec)),
                     t0=time.monotonic(), offset=0.0,
                     playing=bool(tracker.playing()), warned=True)
        cur["status"] = None
        _log(f"手动对齐：本地时钟 base={clock['base']:.1f}s")
    ov.on_sync_position = _sync_to
    ov.flash("常驻字幕已启动 —— 有缓存直接出，没缓存自动预翻译", 5.0)
    print("常驻模式：监听系统媒体会话，自动匹配 pre_cache 里的字幕")
    print("        （Ctrl+C 或关浮窗退出）")

    # ── 自动预翻译管线：没缓存时，去浏览器历史反查网址并后台预翻译 ──
    autocache = None
    if not getattr(args, "no_pretranslate", False):
        try:
            from autopipe import AutoCache
            autocache = AutoCache(dst=args.dst, log=_log)
            print("        + 没缓存的视频 → 自动反查网址并预翻译（首播稍等十几秒）")
        except Exception as e:
            _log(f"自动管线不可用（{type(e).__name__}: {e}）—— 仅用缓存模式")
    else:
        print("        --no-pretranslate：只匹配已有缓存，不自动预翻译")

    cur = {"srt": None, "cues": [], "starts": [], "idx": -2, "title": "",
           "status": None, "err_ts": 0.0, "warned_pos": False,
           "pos_logged": False, "sig": None, "seek_n": 0,
           "status_lb": None, "status_ts": 0.0}
    _jump = _JumpDetector()
    # 自动预翻译的尝试记录：ntitle -> {"n": 次数, "ts": 上次时间}
    tried = {}
    # 已经为哪些标题续跑过预翻译（避免每拍重复提交）
    resumed = set()

    def _cache_unfinished(srt_path):
        """缓存是否只是半截（上次看到一半关了/崩了/还在翻）。

        为什么需要：命中缓存就直接 _load 的话，遇到只翻了前 4 条的半截
        srt 会永远停在那里 —— 用户看到的就是"缓存有了但字幕不跟"。
        判据：meta.complete 不为 true，或最后一条 cue 离视频时长还差 5s 以上。
        """
        try:
            meta = json.load(open(srt_path + ".meta.json", encoding="utf-8"))
        except Exception:
            meta = {}
        # **complete=true 就是完整缓存**：不要再按“最后一条离片尾差几秒”
        # 反复判半截。视频平台经常把片尾卡/黑屏算进时长，旧逻辑会在
        # 每次 watch 启动都触发一次“续跑预翻译”，白占 GPU/磁盘，表现为
        # 视频翻译偶发卡顿、启动变慢。
        if meta.get("complete"):
            return False
        try:
            cues = parse_srt(srt_path, _line_chars_pref())
        except Exception:
            return True
        if not cues:
            return True
        # meta 没有 complete（旧缓存/中断）：用最后一条是否到片尾兜底
        dur = tracker.duration()
        if dur and cues[-1][1] < float(dur) - 5.0:
            return True
        return False

    def _resume_if_needed(srt_path, title):
        """半截缓存：先把已有部分显示上，同时后台续跑预翻译。

        续跑是 pretranslate 自带的断点续跑（读回已有 srt，从最后一条
        的 end 继续），tick 里的热重载会边看边长 —— 这就是缓存模块的
        "实时性"，不需要借回环模块。
        """
        if autocache is None or not cur["title"]:
            return
        nt = cur["title"]
        if nt in resumed or not _cache_unfinished(srt_path):
            return
        resumed.add(nt)
        _log(f"缓存未完成（半截 srt）→ 后台续跑预翻译: "
             f"{os.path.basename(srt_path)}")
        try:
            autocache.ensure_async(title, args.dst,
                                   dur_provider=tracker.duration)
            _status("字幕只译到前面 → 后台续译中，会边看边长…")
        except Exception as e:
            _log(f"续跑预翻译启动失败: {type(e).__name__}: {e}")

    # ── 浮窗状态：没字幕可显示时，直接告诉用户卡在哪一环 ──
    def _drop_status():
        """撤掉上一条状态提示。

        提示条目 hold=3600（要一直在），不主动撤就会赖在屏上 —— 和后面
        的真字幕叠在一起，看着就是"浮窗乱了"。
        """
        lb = cur.get("status_lb")
        cur["status_lb"] = None
        if lb is None:
            return
        for i, e in enumerate(list(ov.entries)):
            if e[3] is lb:
                ov._drop(i)
                ov._relayout()
                return

    def _status(msg, hold=3600.0):
        """显示"卡在哪一环"。

        为什么重写（用户报"没有内容 / 浮窗炸了"的根因）：
          原来这里无条件 `ov.clear()` + `flash(msg, 3600)`。而 SMTC 的
          标题会**瞬时读到空**（winsdk 元数据未就绪时标题就是 ""，所以
          smtc_tracker 现在不缓存空标题）。每闪一次标题，浮窗就被清一次
          屏、并留下一条**留屏一小时**的提示：字幕被擦掉，提示还会和
          随后回来的字幕叠着显示。实测（bench/_repro_stuck.py）：
          t=3s 有字幕 → t=6s 全被擦掉只剩提示 → t=9s 提示与字幕并存。
        现在：有字幕时绝不清屏；提示是可追踪的条目，换状态先撤旧的。
        """
        if msg == cur["status"]:
            return
        cur["status"] = msg
        if not msg:
            _drop_status()
            return
        _log(f"状态: {msg}")
        cur["status_ts"] = time.time()
        _drop_status()
        if cur["cues"]:
            # 已有字幕：只补一条短提示，绝不 clear（clear 会连字幕一起擦）
            n0 = len(ov.entries)
            ov.flash(msg, min(6.0, hold))
            cur["status_lb"] = (ov.entries[-1][3]
                                if len(ov.entries) > n0 else None)
            return
        ov.clear()
        n0 = len(ov.entries)
        ov.flash(msg, hold)
        cur["status_lb"] = (ov.entries[-1][3]
                            if len(ov.entries) > n0 else None)

    def _load(srt_path):
        cues = parse_srt(srt_path)
        if not cues:
            _log(f"字幕文件是空的: {srt_path}")
            return False
        cur.update(srt=srt_path, cues=cues,
                   starts=[c[0] for c in cues], idx=-2, status=None,
                   warned_pos=False, pos_logged=False,
                   sig=_file_sig(srt_path), status_lb=None)
        ov.clear()
        ov.flash(f"已匹配字幕：{os.path.basename(srt_path)}"
                 f"（{len(cues)} 条）", 3.0)
        _log(f"匹配字幕: {os.path.basename(srt_path)}（{len(cues)} 条）")
        return True

    def _show(i):
        """同 main 路径：滚动保留最近若干条、间隙不清屏（对齐回环观感）。"""
        if i is None or i < 0:
            return
        # 有字幕要上屏了 → 状态提示先撤（它可能正赖在屏上跟字幕叠着）
        if cur["status"] or cur.get("status_lb"):
            cur["status"] = None
            _drop_status()
        # 自愈：屏上已经没有真字幕了（被状态提示清过 / 条目过期清掉）——
        # 把 idx 置回去强制重画，否则长句里会一直空白。
        if not _sub_on_screen(ov):
            cur["idx"] = -2
        if i == cur["idx"]:
            return
        cur["idx"] = i
        _st, _en, zh, src_txt = cur["cues"][i]
        ov.set(src_txt, zh)

    def _tick():
        if _engine_stop_requested():
            _log("收到管理端停止请求（engine_stop.flag），退出 watch")
            ov.app.quit()
            return
        try:
            # ① 先处理后台自动预翻译送回来的事件
            if autocache is not None:
                for ev in autocache.take_events():
                    if ev["state"] in ("partial", "done") and ev.get("srt"):
                        # partial = 首块刚译完就落盘了 → 先加载上，剩下的
                        # 交给 tick 里的热重载慢慢续（这才是"边看边等"）
                        if ev["ntitle"] == cur["title"] and not cur["cues"]:
                            _load(ev["srt"])
                        if ev["state"] == "done":
                            _log(f"自动预翻译完成: "
                                 f"{os.path.basename(ev['srt'])}")
                    elif ev["state"] == "failed":
                        _log(f"自动预翻译失败: {ev.get('msg')}")
                        if ev["ntitle"] == cur["title"]:
                            _status(f"在播：{(ev.get('title') or '')[:22]}"
                                    f"　预翻译失败（见 watch.log）")
                    elif ev["state"] == "no_url":
                        _log("自动管线：未在历史里找到视频网址")
                        nt0 = ev["ntitle"]
                        retry, tr0 = no_url_backoff(tried, nt0)
                        if retry:
                            _log(f"no_url 第 {tr0['nu']}/"
                                 f"{AUTO_NOURL_MAX_TRIES} 次，"
                                 f"{AUTO_NOURL_COOLDOWN:.0f}s 后自动重查")
                        if ev["ntitle"] == cur["title"]:
                            if retry:
                                _status("在播："
                                        f"{(ev.get('title') or '')[:18]}"
                                        "　暂未查到网址，稍后自动重查…")
                            else:
                                _status("在播："
                                        f"{(ev.get('title') or '')[:18]}"
                                        "　未能反查到网址"
                                        "（可用 cache.bat 手动指定）")

            title = tracker.current_title()
            if not title:
                # 标题瞬时读不到是**常态**：winsdk 的元数据没就绪时标题就是
                # 空（smtc_tracker 因此不缓存空标题，每 2 秒重试）。已经有
                # 字幕时绝不能清屏回去等 —— 那正是"看着看着字幕没了、
                # 只剩一条提示"的来源。没字幕才提示并等下一拍。
                if not cur["cues"]:
                    if tracker.has_session():
                        _status("已连上媒体会话，正在读取标题…")
                    else:
                        _status("等待视频播放…（播放任意视频即自动出字幕）")
                    return
            # 会话换了片子 → 清掉旧的，按新标题重新匹配缓存
            elif _norm_title(title) != cur["title"]:
                cur.update(srt=None, cues=[], starts=[], idx=-2,
                           title=_norm_title(title), status=None,
                           warned_pos=False, pos_logged=False,
                           status_lb=None)
                _jump.reset()          # 换片子：位置基准作废，防误判为拖动
                # 切视频 = 旧的预翻译任务全部作废。**必须真杀子进程**：
                # 每个任务占 3~6GB 显存，不杀就连新任务一起拖死 ——
                # 这正是"切个视频浮窗就停了"的根因。
                if autocache is not None:
                    try:
                        n_kill = autocache.cancel_except(cur["title"])
                        if n_kill:
                            _log(f"已取消 {n_kill} 个旧预翻译任务"
                                 f"（显存释放）")
                    except Exception as e:
                        _log(f"取消旧任务异常: {e}")
                # 同一标题的失败计数放 5 分钟自动清零：上次失败可能是
                # 被旧任务抢显存挤爆的，不该永久拉黑这部片
                tr_stale = tried.get(cur["title"])
                if tr_stale and time.time() - tr_stale["ts"] > 300:
                    tried.pop(cur["title"], None)
                # 记下来源进程（AUMID）：多会话选错时（比如跟到了
                # 音乐标签页）这一行能直接指出是选错了哪一个会话
                _log(f"检测到会话: {title!r} playing={tracker.playing()}"
                     f" 来源={tracker.describe()}")
                hit = _find_cached_srt(title, args.dst)
                if hit:
                    _load(hit)
                    _resume_if_needed(hit, title)
                else:
                    _log("无缓存字幕；pre_cache 候选: "
                         f"{_cached_names(args.dst)}")
                    nt = cur["title"]
                    if autocache is None:
                        _status(f"在播：{title[:24]}　无缓存字幕")
                    else:
                        st = autocache.state_of(nt)
                        if st and st["state"] == "running":
                            _status("自动预翻译中… "
                                    f"{st.get('msg') or ''}")
                        elif st and st["state"] == "done" and st.get("srt"):
                            _load(st["srt"])      # 事件漏了就直接读结果
                        else:
                            tr = tried.get(nt) or {"n": 0, "ts": 0.0}
                            if tr["n"] < AUTO_MAX_TRIES and \
                                    time.time() - tr["ts"] >= AUTO_RETRY_COOLDOWN:
                                tried[nt] = {"n": tr["n"] + 1,
                                             "ts": time.time()}
                                _log(f"自动管线启动（第 {tr['n']+1}/"
                                     f"{AUTO_MAX_TRIES} 次）: {title!r} "
                                     f"时长={tracker.duration()}")
                                autocache.ensure_async(
                                    title, args.dst,
                                    dur_provider=tracker.duration)
                                _status("无缓存 → 正在自动预翻译"
                                        "（首次稍慢，就绪后自动上字幕）…")
                            else:
                                msg = (st or {}).get("msg") or "无缓存字幕"
                                _status(f"在播：{title[:22]}　{msg}")
            # ① 字幕还没出来时，把自动预翻译的进度**持续**显示出来。
            #    关键是那个 mm:ss 计时器：它每秒都在变 —— 变了浮窗就刷新，
            #    用户一眼能看出"活着，在干活"。光有静态文字分不清是在跑
            #    还是卡死了（下载/模型加载阶段可能几十秒没有新进度行）。
            if not cur["cues"] and autocache is not None and cur["title"]:
                st = autocache.state_of(cur["title"])
                if st and st["state"] == "running" \
                        and time.time() - cur.get("status_ts", 0.0) >= 1.0:
                    el = max(0, int(time.time()
                                    - st.get("t0", time.time())))
                    stage_cn = {"resolve": "反查网址",
                                "translate": "预翻译"}.get(
                                    st.get("stage") or "", "处理")
                    _status(f"{stage_cn}中 "
                            f"{el // 60:02d}:{el % 60:02d}"
                            f"｜{st.get('msg') or ''}")
            # ② srt 增量热重载：预翻译是**分块流水**地往里写的，文件一变
            #    就重读 —— 新译好的段落立刻可看，不必等整片跑完。
            #    这就是点播版对"先翻译一部分就显示"的落地。
            if cur["srt"]:
                sig = _file_sig(cur["srt"])
                if sig and sig != cur.get("sig"):
                    new_cues = parse_srt(cur["srt"], _line_chars_pref())
                    if len(new_cues) > len(cur["cues"]):
                        cur.update(cues=new_cues,
                                   starts=[c[0] for c in new_cues],
                                   sig=sig, status=None)
                        _log(f"字幕增量：{len(new_cues)} 条"
                             f"（覆盖到 {new_cues[-1][1]/60:.1f} 分钟）")
                    else:
                        cur["sig"] = sig
            if not cur["cues"]:
                return
            # 单行字幕长度热更新：浮窗设置改了 → 重新切条，无需重启 watch
            _lc = _line_chars_pref()
            if _lc != cur.get("line_chars"):
                cur["line_chars"] = _lc
                if cur.get("srt"):
                    _new = parse_srt(cur["srt"], _lc)
                    if _new:
                        cur.update(cues=_new, starts=[c[0] for c in _new],
                                   idx=-2)
                        _log(f"单行字幕长度 → {_lc} 字，已重新切条"
                             f"（{len(_new)} 条）")
            pos = tracker.position()
            # SMTC 没时间线时的兜底：从“最后一次已知位置”起，按墙钟推进；
            # 暂停冻结；若之前从未有过位置，则从 0 开始（Home 可从当前时刻归零）。
            if pos is None:
                now = time.monotonic()
                if not clock["on"]:
                    clock.update(on=True, base=clock["last_pos"],
                                 t0=now, offset=0.0,
                                 playing=bool(tracker.playing()))
                    if not clock["warned"]:
                        clock["warned"] = True
                        _log("SMTC 不上报时间线 → 启用本地时钟兜底"
                             "（Home 当前归零 / ←→ ±2s）")
                _playing = bool(tracker.playing())
                if _playing:
                    if not clock["playing"]:
                        clock["t0"] = now
                        clock["playing"] = True
                    pos = clock["base"] + (now - clock["t0"]) + clock["offset"]
                else:
                    if clock["playing"]:
                        clock["base"] += now - clock["t0"]
                        clock["playing"] = False
                    pos = clock["base"] + clock["offset"]
            else:
                if clock["on"]:
                    _log("SMTC 时间线恢复，切回系统位置")
                    clock.update(on=False, warned=False)
                clock["last_pos"] = float(pos)
            # 进度拖动检测：跳变命中 → 清残留旧条目、强制按新位置重画。
            # 双通道：位置跳变（通用）+ SMTC 锚点级 seek 事件（更早半拍）。
            jumped = _jump.feed(pos)
            n_seek = tracker.seek_serial()
            if n_seek != cur["seek_n"]:
                cur["seek_n"] = n_seek
                jumped = True
            if jumped and pos is not None:
                ov.clear()
                cur["idx"] = -2
                _log(f"检测到进度拖动 → 重新对齐 pos={pos:.1f}s")
            if pos is None:
                _status(f"字幕已就绪（{len(cur['cues'])} 条）"
                        f"　播放器未上报时间线；已启用本地时钟"
                        f"（Home 归零 / ←→ 校正）")
                return
            # 位置有了 → 状态提示该撤（它是"等位置"用的，别跟字幕叠着）
            if cur["status"] or cur.get("status_lb"):
                cur["status"] = None
                _drop_status()
            if not cur.get("pos_logged"):
                cur["pos_logged"] = True
                _log(f"位置已对齐: pos={pos:.2f}s，开始同步")
            i = bisect.bisect_right(cur["starts"], pos) - 1
            if i >= 0 and pos <= cur["cues"][i][1] + 0.35:
                _show(i)
            else:
                _show(None)
            # 当前播放位置还没被译到 → 告诉用户"已译到哪 / 当前在哪"，
            # 别让浮窗在等待期间一片空白（缓存模块允许边看边等，
            # 但不能让人以为死了）。节流 3s，避免提示闪个不停。
            _covered = cur["cues"][-1][1] if cur["cues"] else 0.0
            if pos > _covered + 0.5 \
                    and time.time() - cur.get("status_ts", 0.0) >= 3.0:
                st = (autocache.state_of(cur["title"])
                      if autocache is not None else None)
                if st and st["state"] == "running":
                    _status(f"续译中：已译到 {_covered/60:.1f} 分钟"
                            f" / 当前 {pos/60:.1f} 分钟")
                elif autocache is None:
                    _status(f"缓存只译到 {_covered/60:.1f} 分钟"
                            f"（当前 {pos/60:.1f}）")
        except Exception as e:
            now = time.time()
            if now - cur["err_ts"] > 2:      # 限频，别刷爆日志
                cur["err_ts"] = now
                _log(f"tick 异常: {type(e).__name__}: {e}")

    timer = QTimer()
    timer.timeout.connect(_tick)
    timer.start(100)
    try:
        ov.app.exec_()
    finally:
        tracker.close()
        if autocache is not None:
            autocache.close()
        _log("watch 退出")


if __name__ == "__main__":
    main()
