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
                 "360", "qq", "sogou", "browser", "quark", "chromium", "edge")


def available():
    return _OK


class SmtcTracker:
    """后台轮询系统媒体会话，持续给出网页视频的播放位置。"""

    def __init__(self, title_hint=None, poll=0.25, log=None):
        """title_hint：视频标题（缓存 srt 的文件名），用来在多个媒体会话
        里认出正确的那一个；没有也能工作（选正在播放的浏览器会话）。
        log：可选回调，会话切换/失去会话时打一行（watch.log 排障用）。"""
        self.title_hint = (title_hint or "").lower()
        self.poll = poll
        self._log = log
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._pos = None            # (上报位置秒, 上报时刻 datetime)
        self._rate = 1.0            # 播放倍速（1.5x/2x 追剧时外推必须乘它）
        self._playing = False
        self._title = ""
        self._source = ""
        self._dur = None            # 总时长（秒）—— 用来消歧多个候选网址
        self._alive = False         # 有没有锁定到会话（与标题是否读到无关）
        self._session = None        # 最近一拍锁定的会话对象（只用于本拍读数）
        self._session_key = None    # **稳定键** (aumid, title)，见 _key_of
        self._thread = None
        self._anchor = None         # 上一次的时间线锚点 (pos, ts)，seek 检测用
        self._seek_n = 0            # 检测到的进度拖动次数（每次跳变 +1）
        self._seek_at = 0.0         # 最近一次 seek 的 monotonic 时刻
        # 稳定键 -> (上次位置, monotonic 时刻)，判"时间线在前进"
        self._pos_seen = {}
        # 迟滞切换：挑战者要连续 _SWITCH_HOLD 拍击败现任才换会话 ——
        # 标签页闪烁/广告弹窗抢 SMTC 时字幕不会来回跳。
        # 注意：比较的是**稳定键**，不是会话对象（对象每拍都是新的）。
        self._challenger_key = None
        self._chall_streak = 0
        self._SWITCH_HOLD = 4       # 150ms 轮询下 ≈ 0.6s 确认窗口

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

        倍速：外推必须乘 playback_rate —— 1.5x/2x 倍速看视频时按 1.0x
        推算，字幕每分钟能漂出几十秒。Chrome/Edge 会经 SMTC 上报倍速；
        读不到（老系统 / 播放器不报）就按 1.0 处理。
        """
        with self._lock:
            if self._pos is None:
                return None
            pos, ts = self._pos
            if self._playing and ts is not None:
                now = datetime.now(timezone.utc)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                return max(0.0, pos + (now - ts).total_seconds() * self._rate)
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

    # ── 会话识别（稳定键）───────────────────────────────────────
    # **关键教训（2026-10-18 实测）**：winsdk 的
    # GlobalSystemMediaTransportControlsSession 对象**每次 get_sessions()
    # 都是新的 Python 包装**（两次调用 `a[i] is b[i]` == False）。所以任何
    # 拿 id(s) 当键的状态都**永远不命中**：
    #   · _titles[id] 每拍查不到 → 标题存不起来；
    #   · _pos_seen[id] 每拍都是空 → "时间线在前进"永远 False；
    #   · s is self._session 永远 False → 迟滞切换永远走"现任消失"分支，
    #     等于完全没有迟滞 —— 多标签页/多会话时字幕在几部片之间来回跳，
    #     预翻译被反复取消，缓存永远建不完（watch.log 实测就是这个）。
    # 修法：用**数据本身**当稳定键 —— (AUMID, 归一化标题)。标题读取很便宜
    # （实测全部会话一次约 1ms），每拍读一次即可。
    async def _read_title(self, s):
        try:
            props = await s.try_get_media_properties_async()
            t = (props.title or "").strip()
            if not t:                   # 没有标题 → 用艺人/专辑名兜底
                t = " ".join(
                    x.strip() for x in
                    (getattr(props, "artist", "") or "",
                     getattr(props, "album_title", "") or "")
                    if x and x.strip())
            return t
        except Exception:
            return ""

    @staticmethod
    def _key_of(aumid, title):
        return ((aumid or "").lower(), (title or "").strip().lower())

    def _prune_pos_seen(self, now):
        """删掉 2 分钟没再出现的键，免得常驻久了字典无限长。"""
        dead = [k for k, (_p, t) in self._pos_seen.items()
                if now - t > 120.0]
        for k in dead:
            self._pos_seen.pop(k, None)

    def _pick(self, infos):
        """从本拍现读的会话数据里挑要跟的那个（infos 见 _tick）。"""
        fg_title = ""
        try:
            from foreground import foreground_is_browser
            fg_ok, fg_title = foreground_is_browser()
            if not fg_ok:
                fg_title = ""
        except Exception:
            pass

        def score(inf):
            sc = 0
            t = inf["title"]
            if self.title_hint:
                if t and (self.title_hint in t or t in self.title_hint):
                    sc += 100
            if fg_title and t:
                try:
                    from foreground import title_matches_window
                    if title_matches_window(t, fg_title):
                        sc += 50
                except Exception:
                    pass
            if inf["advancing"]:
                sc += 20
            if inf["playing"]:
                sc += 10
            if inf["is_browser"]:
                sc += 5
            return sc

        infos.sort(key=score, reverse=True)
        return infos[0]

    def _switch_to(self, inf):
        """真正切换锁定的会话（只存**稳定键**+本拍对象，不存旧包装）。"""
        old = (self._session_key or (None, None))[1]
        self._session_key = inf["key"]
        self._session = inf["s"]
        self._challenger_key = None
        self._chall_streak = 0
        with self._lock:
            self._anchor = None          # 换会话 = 换片子，旧锚点作废
        if self._log:
            try:
                self._log(f"[smtc] 会话切换 → {inf['title'][:48]!r}"
                          f"（上一部: {(old or '无')[:32]!r}）")
            except Exception:
                pass

    async def _tick(self, mgr):
        sessions = mgr.get_sessions()
        # ① 本拍读数：标题/播放状态/倍速/时间线，全部现读
        infos = []
        for s in sessions:
            try:
                aumid = s.source_app_user_model_id or ""
            except Exception:
                aumid = ""
            title = await self._read_title(s)
            playing, rate = False, 1.0
            try:
                pi = s.get_playback_info()
                playing = pi.playback_status == _Status.PLAYING
                r = getattr(pi, "playback_rate", None)
                if r and float(r) > 0:
                    rate = float(r)
            except Exception:
                pass
            pos = ts = dur = None
            try:
                tl = s.get_timeline_properties()
                pos = tl.position.total_seconds()
                ts = tl.last_updated_time
                dur = tl.end_time.total_seconds()
            except Exception:
                pass
            infos.append({
                "s": s, "aumid": aumid, "title": title,
                "key": self._key_of(aumid, title),
                "playing": playing, "rate": rate,
                "pos": pos, "ts": ts, "dur": dur,
                "is_browser": any(h in aumid.lower()
                                  for h in BROWSER_HINTS),
            })
        # ② "时间线在前进"：按稳定键跨拍比较
        now = time.monotonic()
        for inf in infos:
            k, pos = inf["key"], inf["pos"]
            last = self._pos_seen.get(k)
            inf["advancing"] = bool(
                last is not None and pos is not None
                and pos > last[0] + 0.05)
            if pos is not None:
                self._pos_seen[k] = (pos, now)
        self._prune_pos_seen(now)
        if not infos:
            self._set(None, None, False, "", "", alive=False)
            self._session_key = None
            self._challenger_key = None
            self._chall_streak = 0
            return
        # ③ 选会话 + 迟滞切换（比较稳定键，而不是对象身份）
        cand = self._pick(infos)
        cur_key = self._session_key
        cur_alive = cur_key is not None and any(
            i["key"] == cur_key for i in infos)
        if cur_key is None or not cur_alive:
            self._switch_to(cand)        # 现任没了 → 立即接上
        elif cand["key"] == cur_key:
            self._challenger_key = None
            self._chall_streak = 0
        elif cand["key"] == self._challenger_key:
            self._chall_streak += 1
            if self._chall_streak >= self._SWITCH_HOLD:
                self._switch_to(cand)
        else:
            self._challenger_key = cand["key"]
            self._chall_streak = 1
        chosen = next((i for i in infos if i["key"] == self._session_key),
                      cand)
        self._session = chosen["s"]
        # ④ 锚点级 seek 检测：同一部片的新锚点跳变 → 用户拖了进度条
        if chosen["pos"] is not None and chosen["ts"] is not None:
            with self._lock:
                old_anchor = self._anchor
            if self._anchor_jumped(old_anchor, (chosen["pos"], chosen["ts"]),
                                   chosen["playing"]):
                with self._lock:
                    self._seek_n += 1
                    self._seek_at = time.monotonic()
            with self._lock:
                self._anchor = (chosen["pos"], chosen["ts"])
        self._rate = chosen["rate"]
        self._set(chosen["pos"], chosen["ts"], chosen["playing"],
                  chosen["title"], chosen["aumid"], dur=chosen["dur"])


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
