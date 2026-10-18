# -*- coding: utf-8 -*-
"""autopipe.py —— "在播视频 → 自动出字幕" 的编排层。

它把三件事串成一条自动路径
==========================
    ① 标题（系统媒体会话给的）
        ↓  browser_history.resolve()
    ② 网址（从浏览器历史反查）
        ↓  子进程跑 pretranslate.py
    ③ 双语 srt（缓存里躺好）
        ↓  watch_player 交给浮窗按播放位置显示

为什么用子进程而不是直接 import
==============================
预翻译要拉 large-v3 模型、跑 GPU，重且长（几秒到十几分钟）；
放子进程里跑，主进程（浮窗）始终保持响应，字幕不会卡；
而且能随时杀掉。进度从子进程 stdout 解析 —— pretranslate 会
打印 `输出: <路径>` / `字幕 cue：N 条` 等，正好拿来报进度。

线程模型
========
ensure_async() 立刻返回，工作在线程里做；结果塞进一个事件队列，
主线程（Qt 定时器）用 take_events() 取。**回调不在工作线程里执行**，
避免跨线程碰 GUI。
"""

from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


# ══════════════════════════════════════════════════════════
# 跑一次预翻译（子进程，流式读 stdout 报进度）
# ══════════════════════════════════════════════════════════

def _py_exe():
    return sys.executable or "python"


def probe_duration(url):
    """探一个网址的总时长（秒），探不到返回 None。

    只服务于"哪个候选才是正在播的那个"这一件事，所以必须快：
    B 站直接问它的公开 API（一次请求，几十毫秒），其他站点才动用 yt-dlp。
    """
    import json as _json
    import urllib.request as _ur
    m = re.search(r"(BV[0-9A-Za-z]+)", url or "")
    if m:
        try:
            req = _ur.Request(
                "https://api.bilibili.com/x/web-interface/view?bvid="
                + m.group(1),
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; "
                                       "Win64; x64) AppleWebKit/537.36 "
                                       "Chrome/120.0 Safari/537.36",
                         "Referer": "https://www.bilibili.com"})
            d = _json.loads(_ur.urlopen(req, timeout=8).read().decode())
            dur = float(((d.get("data") or {}).get("duration")) or 0)
            return dur or None
        except Exception:
            return None
    try:
        import yt_dlp
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True,
                               "noplaylist": True}) as y:
            info = y.extract_info(url, download=False)
        return float(info.get("duration") or 0) or None
    except Exception:
        return None


def run_pretranslate(src, dst="zh", on_progress=None, on_target=None,
                     extra_args=None, on_proc=None):
    """同步跑 pretranslate.py。成功返回 srt 路径，失败返回 None。

    on_progress(text)：每条可读的进度行回调一次（`下载音频` / `cue：`
                       / `识别` …）。
    on_target(path)  ：一拿到目标输出路径就回调（用来提前显示进度）。
    on_proc(proc)    ：子进程句柄一创建就回调（上层存起来用于取消 ——
                       用户切视频后旧任务必须能杀掉，否则两个 whisper
                       抢一张卡，全卡死）。
    """
    env = dict(os.environ, PYTHONIOENCODING="utf-8", HF_HUB_OFFLINE="1")
    cmd = [_py_exe(), "-u", os.path.join(HERE, "pretranslate.py"),
           src, "--to", dst] + list(extra_args or [])
    try:
        proc = subprocess.Popen(
            cmd, cwd=HERE, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            errors="replace", bufsize=1)
    except Exception as e:
        if on_progress:
            on_progress(f"启动预翻译失败：{type(e).__name__} {e}")
        return None
    if on_proc:
        try:
            on_proc(proc)
        except Exception:
            pass

    target = None
    tail = []
    # 预翻译的进度是**回车覆盖**写的（"\r识别 46%"），按行读会全挤成
    # 一整行，所以这里两种换行都切开。
    for raw in proc.stdout:
        for line in (raw or "").replace("\r", "\n").split("\n"):
            line = line.strip()
            if not line:
                continue
            tail.append(line)
            if len(tail) > 40:
                tail.pop(0)
            m = re.search(r"输出:\s*(.+?\.srt)\s*$", line)
            if m:
                target = m.group(1).strip()
                if on_target:
                    on_target(target)
            if on_progress and re.search(
                    r"下载音频|识别模型|模型加载|字幕 cue|断点|缓存命中"
                    r"|完成：|音频时长|解码音频|识别.*?\d+%|翻译.*?\d+/"
                    r"|未译出|已出字幕到|分块流水"
                    r"|\[download\].*?\d+%|下载.*?\d+%", line):
                on_progress(line)
    proc.wait()

    if target and os.path.isfile(target) and proc.returncode == 0:
        return target
    # 兜底：从尾部日志里捞 `完成：<path>`
    for line in reversed(tail):
        m = re.search(r"完成：\s*(.+?\.srt)\s*$", line)
        if m and os.path.isfile(m.group(1).strip()):
            return m.group(1).strip()
    if on_progress:
        on_progress("预翻译失败：" + (tail[-1] if tail else "无输出"))
    return None


# ══════════════════════════════════════════════════════════
# AutoCache：标题 → 缓存/自动预翻译
# ══════════════════════════════════════════════════════════

class AutoCache:
    """管理"在播标题 → 字幕就绪"的后台任务。

    · ensure_async(title)      启动（或复用）一个任务，立刻返回
    · take_events()            取出至今产生的状态事件（主线程调用）
    · state_of(title)          查某个标题当前状态
    """

    def __init__(self, dst="zh", log=None):
        self.dst = dst
        self.log = log or (lambda s: None)
        self._lock = threading.Lock()
        self._jobs = {}             # ntitle -> dict(state, srt, msg, url, t0)
        self._q = queue.Queue()
        self._closed = False

    # ── 查询 ──
    def state_of(self, ntitle):
        with self._lock:
            j = self._jobs.get(ntitle)
            return dict(j) if j else None

    def busy(self):
        with self._lock:
            return any(j.get("state") == "running"
                       for j in self._jobs.values())

    def take_events(self):
        """取出所有待处理事件。返回 [{'ntitle','state','srt','msg','url'}]。"""
        out = []
        while True:
            try:
                out.append(self._q.get_nowait())
            except queue.Empty:
                break
        return out

    # ── 启动 ──
    def ensure_async(self, title, dst=None, url=None, dur_provider=None):
        """为 title 启动自动预翻译（已在跑/已完成则复用）。返回 ntitle。

        dur_provider：一个无参函数，返回"当前在播视频的时长（秒）"。
        有多个候选网址时用它来消歧（见 _pick_candidate）。
        """
        dst = dst or self.dst
        from browser_history import norm_title
        nt = norm_title(title)
        if not nt:
            return None
        with self._lock:
            if self._closed:
                return nt
            j = self._jobs.get(nt)
            if j and j.get("state") in ("running", "done"):
                return nt
            self._jobs[nt] = {"state": "running", "srt": None, "msg":
                              "查网址…", "url": url, "title": title,
                              "t0": time.time(), "stage": "resolve"}
        th = threading.Thread(target=self._worker,
                              args=(nt, title, dst, url, dur_provider),
                              daemon=True)
        th.start()
        return nt

    # ── 取消 ──
    def cancel_except(self, nt_keep):
        """杀掉 nt_keep 之外所有还在跑的任务（含其子进程）。

        为什么必须杀子进程而不只是标记：预翻译子进程一起来就占 3~6GB
        显存。用户切了视频，旧任务不杀，新任务再拉一个 whisper ——
        8GB 卡上两个实例互抢，新的 OOM、旧的也在白跑，浮窗看着就是
        "切了个视频全停了"。返回杀掉的任务数（记日志用）。
        """
        killed = 0
        with self._lock:
            victims = [(nt, j.get("proc")) for nt, j in self._jobs.items()
                       if nt != nt_keep and j.get("state") == "running"]
            for nt, _p in victims:
                self._jobs[nt]["cancel"] = True
        for nt, p in victims:
            if p is not None:
                try:
                    p.kill()                    # 显存立刻释放
                except Exception:
                    pass
            killed += 1
            self._emit(nt, "cancelled", msg="已切换视频，旧任务取消")
            self.log(f"[autopipe] 取消旧任务（子进程已杀）: {nt[:40]}")
        return killed

    def _emit(self, nt, state, **kw):
        with self._lock:
            j = self._jobs.get(nt) or {}
            j.update(state=state, **kw)
            self._jobs[nt] = j
            ev = {"ntitle": nt, "state": state, "title": j.get("title"),
                  "srt": j.get("srt"), "msg": j.get("msg"),
                  "url": j.get("url")}
        self._q.put(ev)

    # ── 工作线程 ──
    def _pick_candidate(self, nt, cands, expect):
        """多个候选网址时，用"时长"挑出真正在播的那个。

        为什么需要：实测同一标题在历史里对应多个网址（B 站推荐位自动
        连播边换 URL 边沿用旧标题）。只按最近访问取，就会取错片子 ——
        拿错网址去预翻译，字幕自然全不对。时长是唯一稳定判据。
        """
        if len(cands) == 1:
            return cands[0]["url"]
        if not expect:
            return cands[0]["url"]
        tol = max(3.0, 0.03 * float(expect))
        self._set_running(nt, "有多个候选，按时长核对…", "resolve")
        for c in cands[:4]:
            d = probe_duration(c["url"])
            self.log(f"[autopipe] 候选时长 {d}（期望 {expect:.0f}s）"
                     f" {c['url'][:56]}")
            if d and abs(d - float(expect)) <= tol:
                return c["url"]
        self.log("[autopipe] 没有候选时长对得上，退回最近访问那个")
        return cands[0]["url"]

    def _worker(self, nt, title, dst, url, dur_provider=None):
        try:
            expect = None
            if dur_provider is not None:
                try:
                    expect = dur_provider()
                except Exception:
                    expect = None

            # ① 反查网址：CDP 标签（最强，不依赖历史落盘）→ 浏览器历史
            if not url:
                self._set_running(nt, "在标签页/历史里查网址…", "resolve")
                cands = []
                try:
                    from foreground import cdp_find_by_title
                    tab = cdp_find_by_title(title)
                    if tab:
                        cands = [{"url": tab["url"], "title": tab["title"],
                                  "score": 0.99, "ts": time.time(),
                                  "cdp": True}]
                        self.log(f"[autopipe] {title!r} CDP 直中当前标签: "
                                 f"{tab['url'][:56]}")
                except Exception as e:
                    self.log(f"[autopipe] CDP 反查异常: {e}")
                if not cands:
                    try:
                        from browser_history import search_all
                        cands = search_all(title)
                    except Exception as e:
                        self.log(f"[autopipe] 历史反查异常: {e}")
                if not cands:
                    # 浏览器历史写入有几秒延迟（视频刚打开时历史还没
                    # 落盘）—— 等一拍重读一次再认输，这是 no_url 最多
                    # 的可治根因。
                    self.log(f"[autopipe] {title!r} 首查无候选，"
                             "2.5s 后重读（历史落盘延迟）")
                    self._set_running(nt, "历史还没落盘，稍候重查…",
                                      "resolve")
                    time.sleep(2.5)
                    try:
                        from foreground import cdp_find_by_title
                        tab = cdp_find_by_title(title)
                        if tab:
                            cands = [{"url": tab["url"],
                                      "title": tab["title"],
                                      "score": 0.99, "ts": time.time(),
                                      "cdp": True}]
                    except Exception:
                        pass
                    if not cands:
                        try:
                            cands = search_all(title)
                        except Exception as e:
                            self.log(f"[autopipe] 历史反查异常: {e}")
                if not cands:
                    self._emit(nt, "no_url", msg="没找到对应的视频网址",
                               url=None)
                    self.log(f"[autopipe] {title!r} 未在历史记录里找到网址")
                    return
                if cands[0].get("relaxed"):
                    self.log(f"[autopipe] {title!r} 严格档未中，"
                             f"用放宽档候选 {cands[0]['url'][:56]}")
                url = self._pick_candidate(nt, cands, expect)
            # 预计耗时：实测约 0.16x 音频时长（识别 8.7x 实时 + 翻译）
            est = ""
            if expect:
                est = f"（约需 {max(0.3, expect * 0.16 / 60):.1f} 分钟）"
            self._set_running(nt, f"开始预翻译{est}…", "translate", url=url)
            self.log(f"[autopipe] {title!r} → {url}{est}")

            # ② 跑预翻译
            last = {"line": ""}
            target = {"p": None}          # pretranslate 打印的 srt 路径

            def prog(line):
                last["line"] = (line or "").strip()
                self._set_running(nt, last["line"][:44], "translate")
                # "已出字幕到 X 分钟" = 某一块已经译完并落盘了。
                # 立刻发一个 partial 事件：观看端可以先把它显示上，
                # 后面的块由观看端轮询 srt 增长自行续上（边看边等）。
                if target["p"] and "已出字幕到" in last["line"] \
                        and os.path.isfile(target["p"]):
                    self._q.put({"ntitle": nt, "state": "partial",
                                 "title": title, "msg": last["line"][:44],
                                 "srt": target["p"], "url": url})

            def _on_proc(p):
                with self._lock:
                    j = self._jobs.get(nt)
                    if j is not None:
                        j["proc"] = p
                # 若句柄到手前已被取消（手快连切两个视频），立刻杀
                with self._lock:
                    cancel_now = bool((self._jobs.get(nt) or {})
                                      .get("cancel"))
                if cancel_now:
                    try:
                        p.kill()
                    except Exception:
                        pass

            srt = run_pretranslate(url, dst, on_progress=prog,
                                   on_target=lambda p: target.__setitem__("p", p),
                                   on_proc=_on_proc)
            with self._lock:
                was_cancelled = bool((self._jobs.get(nt) or {})
                                     .get("cancel"))
            if was_cancelled:
                self.log(f"[autopipe] 任务已取消，结果丢弃: {nt[:40]}")
                return
            if srt:
                self._emit(nt, "done", srt=srt, msg="字幕已就绪", url=url)
                self.log(f"[autopipe] 完成: {os.path.basename(srt)}")
            else:
                why = last["line"] or "无输出"
                self._emit(nt, "failed",
                           msg=f"预翻译失败：{why[:34]}", url=url)
                self.log(f"[autopipe] 预翻译失败（{why[:60]}）: {url}")
        except Exception as e:
            self._emit(nt, "failed", msg=f"{type(e).__name__}: {e}")
            self.log(f"[autopipe] 异常: {type(e).__name__}: {e}")

    def _set_running(self, nt, msg, stage, url=None):
        with self._lock:
            j = self._jobs.get(nt) or {}
            j.update(state="running", msg=msg, stage=stage)
            if url:
                j["url"] = url
            self._jobs[nt] = j
        self._q.put({"ntitle": nt, "state": "running",
                     "title": j.get("title"), "msg": msg,
                     "stage": stage, "srt": None, "url": j.get("url")})

    def close(self):
        with self._lock:
            self._closed = True


if __name__ == "__main__":
    # 手动试跑：python autopipe.py "视频标题"
    import browser_history
    if len(sys.argv) < 2:
        print("用法: python autopipe.py \"视频标题\"（或网址/本地文件）")
        raise SystemExit(0)
    arg = sys.argv[1]
    if arg.startswith("http") or os.path.isfile(arg):
        p = run_pretranslate(arg, on_progress=lambda s: print("  ", s))
        print("srt:", p)
    else:
        u = browser_history.resolve(arg)
        print("网址:", u)
        if u:
            p = run_pretranslate(u, on_progress=lambda s: print("  ", s))
            print("srt:", p)
