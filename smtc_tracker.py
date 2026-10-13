# -*- coding: utf-8 -*-
"""smtc_tracker.py —— 用 Windows SMTC 追踪浏览器里在线视频的播放位置。

为什么需要它
============
浏览器（Chrome / Edge / Firefox）播放网页视频时，会通过系统媒体会话
（GlobalSystemMediaTransportControls，简称 SMTC —— 就是锁屏/音量条上
显示"正在播放"的那个接口）上报：
  · 媒体标题、来源应用
  · 播放状态（播放 / 暂停）
  · 时间线（position + 上报时刻）
这意味着**不用浏览器插件、不用 mpv、不用手动对表**，就能知道网页视频
"现在播到第几秒"——暂停自动停、拖进度条自动跟。这是点播预翻译字幕
在"浏览器看在线视频"场景下的最佳位置源。

注意
----
  · 需要 winsdk（pip install winsdk）。
  · SMTC 的 position 是"上报时刻的位置"，当前位置要按播放状态自己推算。
  · 部分网站/播放器不上报时间线（只有标题没有 position），此时返回 None，
    由上层决定降级（watch_player 会落到手动时钟）。
  · B 站实测：标题、状态、时间线全部上报。
"""
from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timezone

try:
    from winsdk.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as _Mgr,
        GlobalSystemMediaTransportControlsSessionPlaybackStatus as _Status,
    )
    _OK = True
except Exception:
    _OK = False

BROWSER_HINTS = ("chrome", "msedge", "firefox", "brave", "opera", "vivaldi",
                 "360", "qq", "sogou", "browser")


def available():
    return _OK


class SmtcTracker:
    """后台轮询系统媒体会话，持续给出网页视频的播放位置。"""

    def __init__(self, title_hint=None, poll=0.25):
        """title_hint：视频标题（缓存 srt 的文件名），用来在多个媒体会话
        里认出正确的那一个；没有也能工作（选正在播放的浏览器会话）。"""
        self.title_hint = (title_hint or "").lower()
        self.poll = poll
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._pos = None            # (上报位置秒, 上报时刻 datetime)
        self._playing = False
        self._title = ""
        self._source = ""
        self._dur = None            # 总时长（秒）—— 用来消歧多个候选网址
        self._alive = False         # 有没有锁定到会话（与标题是否读到无关）
        self._session = None        # 当前锁定的会话
        self._thread = None
        self._anchor = None         # 上一次的时间线锚点 (pos, ts)，seek 检测用
        self._seek_n = 0            # 检测到的进度拖动次数（每次跳变 +1）
        self._seek_at = 0.0         # 最近一次 seek 的 monotonic 时刻
        self._pos_seen = {}         # id(会话) -> 上次看到的 position，判"在动"

    # ── 生命周期 ──
    def start(self):
        if not _OK:
            return False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def close(self):
        self._stop.set()

    # ── 对外读数 ──
    def position(self):
        """当前播放位置（秒）。没有会话 / 会话不上报时间线时 None。

        关键：SMTC 给的是"last_updated_time 时刻的 position"。很多应用
        （夸克浏览器实测）只在 seek/暂停时才刷新时间线，所以必须以
        **上报时刻**为基准推算，不能以我们轮询的时刻为基准 —— 否则
        每次轮询都把推算窗口清零，位置永远卡在原地。
        """
        with self._lock:
            if self._pos is None:
                return None
            pos, ts = self._pos
            if self._playing and ts is not None:
                now = datetime.now(timezone.utc)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                return max(0.0, pos + (now - ts).total_seconds())
            return pos

    def playing(self):
        with self._lock:
            return self._playing

    def describe(self):
        with self._lock:
            if not self._alive:
                return "未找到媒体会话"
            st = "▶" if self._playing else "⏸"
            return f"{st} {self._title or '(未读到标题)'}（{self._source}）"

    def has_session(self):
        """有没有锁定到媒体会话。

        **不能拿标题判断** —— 自验实测：会话正常播放、时间线在走，
        但 try_get_media_properties 拿到的标题是空的（尤其播放器/网页
        没准备好元数据时）。那时窗口其实在播，却会被当成"没有会话"
        而永远不出字幕。
        """
        with self._lock:
            return self._alive

    def current_title(self):
        with self._lock:
            return self._title

    def duration(self):
        """当前视频总时长（秒）；会话不上报时间线时 None。

        为什么重要：同一个标题在浏览器历史里常常对应**好几个网址**
        （B 站推荐位自动连播会边切 URL 边沿用旧标题），光看标题分不清
        在播哪个。时长是唯一稳定的判据 —— 拿它去筛候选网址，一对上
        就知道是哪一个。
        """
        with self._lock:
            return self._dur

    # ── seek 检测 ──
    @staticmethod
    def _anchor_jumped(old, new, playing, threshold=1.5):
        """新旧两个时间线锚点 (pos, ts) 是否构成一次"拖动进度条"。

        判据：把旧锚点外推到新锚点的时刻（播放中才外推，暂停时位置
        不该自己动），与新锚点的位置偏差超过 threshold 秒 → 用户拖了
        进度条（或切了集），不是正常播放能走到的范围。
        ts 是 datetime（UTC 或 naive），允许为 None（不能外推时只看
        位置差）。
        """
        if old is None or new is None:
            return False
        o_pos, o_ts = old
        n_pos, n_ts = new
        if o_pos is None or n_pos is None:
            return False
        extrap = o_pos
        if playing and o_ts is not None and n_ts is not None:
            try:
                a = o_ts if o_ts.tzinfo else o_ts.replace(tzinfo=timezone.utc)
                b = n_ts if n_ts.tzinfo else n_ts.replace(tzinfo=timezone.utc)
                extrap = o_pos + max(0.0, (b - a).total_seconds())
            except Exception:
                extrap = o_pos
        return abs(n_pos - extrap) > threshold

    def seek_serial(self):
        """seek 事件序号。上层记下上次的值，变了就是刚拖过进度条。"""
        with self._lock:
            return self._seek_n

    def last_seek_at(self):
        """最近一次 seek 的 monotonic 时刻（没有过就是 0）。"""
        with self._lock:
            return self._seek_at

    # ── 内部 ──
    def _set(self, pos, ts, playing, title, source, alive=True, dur=None):
        with self._lock:
            self._pos = None if pos is None else (pos, ts)
            self._playing = playing
            self._title = title
            self._source = source
            self._alive = alive
            self._dur = dur

    def _run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._poll_loop())
        except Exception:
            pass
        finally:
            try:
                loop.close()
            except Exception:
                pass

    async def _poll_loop(self):
        mgr = await _Mgr.request_async()
        while not self._stop.is_set():
            try:
                await self._tick(mgr)
            except Exception:
                pass
            await asyncio.sleep(self.poll)

    def _pick(self, sessions):
        """从所有媒体会话里挑我们要跟的那个。"""
        cands = []
        for s in sessions:
            try:
                aumid = (s.source_app_user_model_id or "").lower()
            except Exception:
                aumid = ""
            try:
                info = s.get_playback_info()
                playing = info.playback_status == _Status.PLAYING
            except Exception:
                playing = False
            is_browser = any(h in aumid for h in BROWSER_HINTS)
            # "时间线在前进"是真正在播视频的最强信号 —— 没有 title_hint
            # 时（--auto 常驻）全靠它把视频会话和挂起的音乐标签页分开：
            # 两者都可能"播放中+浏览器"，但只有前者的 position 在动。
            pos = None
            try:
                pos = s.get_timeline_properties().position.total_seconds()
            except Exception:
                pass
            advancing = False
            if pos is not None:
                last = self._pos_seen.get(id(s))
                advancing = last is not None and pos > last + 0.05
                self._pos_seen[id(s)] = pos
            cands.append((s, aumid, playing, is_browser, advancing))
        if not cands:
            return None
        # 优先级：标题命中 > 时间线在前进 > 浏览器且播放中 > 播放中 >
        # 浏览器 > 第一个
        def score(c):
            s, aumid, playing, is_browser, advancing = c
            sc = 0
            if self.title_hint:
                t = getattr(self, "_titles", {}).get(id(s), "")
                if t and (self.title_hint in t or t in self.title_hint):
                    sc += 100
            if advancing:
                sc += 20
            if playing:
                sc += 10
            if is_browser:
                sc += 5
            return sc
        cands.sort(key=score, reverse=True)
        return cands[0][0]

    async def _remember_title(self, s):
        """记住会话标题（供匹配缓存 srt 用）。

        **读到空标题不能缓存** —— 实测：会话刚建立时元数据还没准备好，
        读到的就是空串；原来一读就存下来，于是这个会话永远"没有标题"，
        哪怕之后标题早就有了也不会再查。改成空标题 2 秒后重试，
        并退一步用 artist/album 兜底。
        """
        if not hasattr(self, "_titles"):
            self._titles = {}
        if not hasattr(self, "_title_try"):
            self._title_try = {}
        sid = id(s)
        if self._titles.get(sid):
            return                      # 已经有标题，不重复查
        now = time.monotonic()
        if now - self._title_try.get(sid, 0.0) < 2.0:
            return                      # 空标题：每 2 秒重试一次
        self._title_try[sid] = now
        try:
            props = await s.try_get_media_properties_async()
            t = (props.title or "").strip()
            if not t:                   # 没有标题 → 用艺人/专辑名兜底
                t = " ".join(
                    x.strip() for x in
                    (getattr(props, "artist", "") or "",
                     getattr(props, "album_title", "") or "")
                    if x and x.strip())
            if t:
                self._titles[sid] = t.lower()
        except Exception:
            pass

    async def _tick(self, mgr):
        sessions = mgr.get_sessions()
        # 记住每个会话的标题（供 _pick 匹配；空标题会定时重试）
        for s in sessions:
            await self._remember_title(s)
        s = self._pick(sessions)
        if s is None:
            self._set(None, None, False, "", "", alive=False)
            return
        if s is not self._session:
            self._session = s
            with self._lock:
                self._anchor = None          # 换会话 = 换片子，旧锚点作废
            await self._remember_title(s)
        title = getattr(self, "_titles", {}).get(id(s), "")
        try:
            aumid = s.source_app_user_model_id or ""
        except Exception:
            aumid = ""
        try:
            playing = s.get_playback_info().playback_status == _Status.PLAYING
        except Exception:
            playing = False
        pos, ts, dur = None, None, None
        try:
            tl = s.get_timeline_properties()
            # position 是 last_updated_time 时刻的位置；TimeSpan→timedelta
            pos = tl.position.total_seconds()
            ts = tl.last_updated_time          # datetime（UTC）
            dur = tl.end_time.total_seconds()
        except Exception:
            pass
        # 锚点级 seek 检测：很多浏览器只在 seek/暂停时刷新时间线，
        # 新锚点一到这里就能发现跳变 —— 比上层靠外推位置比较更早。
        if pos is not None and ts is not None:
            with self._lock:
                old_anchor = self._anchor
            if self._anchor_jumped(old_anchor, (pos, ts), playing):
                with self._lock:
                    self._seek_n += 1
                    self._seek_at = time.monotonic()
            with self._lock:
                self._anchor = (pos, ts)
        self._set(pos, ts, playing, title, aumid, dur=dur)


if __name__ == "__main__":
    # 探测当前系统的媒体会话：python smtc_tracker.py
    import sys
    if not _OK:
        sys.exit("winsdk 未安装：pip install winsdk")

    async def probe():
        mgr = await _Mgr.request_async()
        ss = mgr.get_sessions()
        print(f"系统里共有 {len(ss)} 个媒体会话：")
        for s in ss:
            try:
                props = await s.try_get_media_properties_async()
                title = props.title or "(无标题)"
            except Exception:
                title = "(读不到标题)"
            try:
                st = s.get_playback_info().playback_status
                st = {0: "关闭", 1: "打开", 2: "切换中", 3: "停止",
                      4: "播放中", 5: "暂停"}.get(int(st), str(st))
            except Exception:
                st = "?"
            try:
                tl = s.get_timeline_properties()
                pos = f"{tl.position.total_seconds():.1f}s / " \
                      f"{tl.end_time.total_seconds():.1f}s"
            except Exception:
                pos = "无时间线"
            print(f"  [{s.source_app_user_model_id}] {st}　{pos}　{title}")

    asyncio.run(probe())
