# -*- coding: utf-8 -*-
"""foreground.py —— 前台窗口与浏览器标签：判断"用户在在看哪一个"。

两个信号源，按可用性自动取舍：

1. **前台窗口标题**（永远可用，纯 ctypes 无依赖）
   Chrome/Edge/夸克等浏览器的窗口标题 = 当前标签页标题 + 浏览器名后缀。
   用户正在看哪个网页视频，前台窗口就是哪个 —— 这是多会话并存时
   "翻译哪一个"最本质的判据，比任何打分都直接。

2. **CDP 标签列表**（浏览器带 --remote-debugging-port 启动时可用）
   直接枚举所有标签的 url + title，是网址反查的最强来源 ——
   不依赖浏览器历史落盘，隐私窗口也能看到。
   没开调试端口就静默返回 []，不影响主链路。
"""
from __future__ import annotations

import ctypes
import json
import time
import urllib.request

# ── 前台窗口 ──────────────────────────────────────────────
_u32 = ctypes.windll.user32
_k32 = ctypes.windll.kernel32

BROWSER_PROC_HINTS = ("chrome", "msedge", "firefox", "brave", "opera",
                      "vivaldi", "360", "qqbrowser", "sogou", "quark",
                      "browser")


def foreground_window():
    """当前前台窗口 → (进程名小写, 窗口标题)。失败返回 ("", "")。"""
    try:
        hwnd = _u32.GetForegroundWindow()
        if not hwnd:
            return "", ""
        n = _u32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 2)
        _u32.GetWindowTextW(hwnd, buf, n + 2)
        title = buf.value or ""
        pid = ctypes.c_ulong(0)
        _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        proc = ""
        if pid.value:
            h = _k32.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED
            if h:
                try:
                    pbuf = ctypes.create_unicode_buffer(260)
                    sz = ctypes.c_ulong(260)
                    # QueryFullProcessImageNameW 拿全路径，只留文件名
                    if _k32.QueryFullProcessImageNameW(h, 0, pbuf,
                                                       ctypes.byref(sz)):
                        proc = (pbuf.value or "").rsplit("\\", 1)[-1].lower()
                finally:
                    _k32.CloseHandle(h)
        return proc, title
    except Exception:
        return "", ""


def foreground_is_browser():
    """前台窗口是不是浏览器；是 → (True, 窗口标题)，否 → (False, "")。"""
    proc, title = foreground_window()
    if proc and any(h in proc for h in BROWSER_PROC_HINTS):
        return True, title
    return False, ""


def title_matches_window(session_title, window_title):
    """SMTC 会话标题是否就是前台浏览器窗口正在显示的标签。

    浏览器窗口标题 = 标签标题 + 后缀（" - Google Chrome" 之类），
    所以做双向包含判断；空串一律不命中。
    """
    a = (session_title or "").strip().lower()
    b = (window_title or "").strip().lower()
    if not a or not b:
        return False
    return a in b or b in a


# ── CDP 标签列表 ──────────────────────────────────────────
_CDP_PORTS = (9222, 9223, 9229, 19527)      # 常见调试端口，0.25s 超时探
_cdp_cache = {"ts": 0.0, "tabs": []}
_CDP_CACHE_TTL = 8.0                        # 最多 8 秒探一次，别空转


def cdp_tabs(ports=_CDP_PORTS, force=False):
    """所有开了调试端口的 Chromium 标签 → [{"url","title","port"}]。

    找不到任何调试端口 → []（正常情况，浏览器默认不开）。
    结果缓存 TTL 秒：CDP 探测是 HTTP 请求，轮询链路不能每拍都做。
    """
    now = time.monotonic()
    if not force and now - _cdp_cache["ts"] < _CDP_CACHE_TTL:
        return _cdp_cache["tabs"]
    tabs = []
    for port in ports:
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json/list",
                    timeout=0.25) as r:
                for t in json.loads(r.read().decode("utf-8", "replace")):
                    if t.get("type") == "page" and t.get("url"):
                        tabs.append({"url": t["url"],
                                     "title": t.get("title") or "",
                                     "port": port})
        except Exception:
            continue
    _cdp_cache["ts"] = now
    _cdp_cache["tabs"] = tabs
    return tabs


def cdp_find_by_title(title, min_ratio=0.60):
    """在 CDP 标签里按标题找最像的一个 → tab dict 或 None。

    供 autopipe 反查网址用：历史记录之外的更高优先级来源。
    """
    import difflib
    want = (title or "").strip().lower()
    if not want:
        return None
    best, best_r = None, 0.0
    for t in cdp_tabs():
        have = t["title"].strip().lower()
        if not have:
            continue
        r = 0.97 if (want in have or have in want) \
            else difflib.SequenceMatcher(None, want, have).ratio()
        if r > best_r:
            best, best_r = t, r
    return best if best_r >= min_ratio else None


if __name__ == "__main__":
    # 探测：python foreground.py
    proc, title = foreground_window()
    print(f"前台窗口: [{proc}] {title}")
    tabs = cdp_tabs(force=True)
    print(f"CDP 标签 {len(tabs)} 个：")
    for t in tabs:
        print(f"  :{t['port']} {t['title'][:40]}  {t['url'][:80]}")
