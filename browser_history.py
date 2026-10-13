# -*- coding: utf-8 -*-
"""browser_history.py —— 把"标题"反查成"网址"。

为什么需要
==========
系统媒体会话（SMTC）只告诉你"正在播什么标题"，**不给网址**。
而预翻译必须拿到网址才能去拉音频。好消息是浏览器的历史库里
标题和网址是并排存的 —— 拿标题去反查就行了。

实测（本机，2026-10）：夸克浏览器的历史库里存着用户正在播的
那个 B 站视频标题，反查一次即命中。

覆盖哪些浏览器
==============
Chrome 系全都同一张表结构（Quark / Edge / Chrome / Brave /
Vivaldi / 360 / QQ / 搜狗 …）：

    urls(id, url, title, visit_count, last_visit_time)

`last_visit_time` 是"1601-01-01 起的微秒数"，用它排序取最近访问。

为什么不直接读原库
==================
浏览器进程一直占着这个库（SQLite 锁）。直接读会被锁住，
而且可能读到写一半的状态。所以先**复制一份**（连同 -wal /
-journal 一起，避免漏掉尚未合并的写入）再读，原库一个字节都不碰。

用法
----
    python browser_history.py "视频标题"      # 手动反查，打印候选
"""

from __future__ import annotations

import difflib
import glob
import os
import re
import shutil
import sqlite3
import tempfile
import time

# Chrome 时间戳的纪元：1601-01-01 与 1970-01-01 相差这么多秒
_CHROME_EPOCH_OFFSET = 11644473600


def _chrome_time_to_unix(us):
    try:
        return int(us) / 1_000_000 - _CHROME_EPOCH_OFFSET
    except Exception:
        return 0.0


def norm_title(s):
    """标题归一化：去平台后缀、去标点、全角转半角，只留字词。

    反查时两边（SMTC 标题 / 历史标题）都用它，才能对齐 ——
    SMTC 给的可能是 'xxx_哔哩哔哩_bilibili'，历史里也可能是
    'xxx_哔哩哔哩_bilibili'，但也可能少了后缀。
    """
    s = (s or "").strip()
    # 平台后缀（_哔哩哔哩_bilibili / - YouTube / | 腾讯视频 …）
    s = re.sub(r"[_\-—|丨]\s*(哔哩哔哩|bilibili|youtube|腾讯视频|爱奇艺"
               r"|优酷|西瓜视频|抖音|芒果TV|搜狐视频|AcFun|A站|B站)\s*$",
               "", s, flags=re.I)
    s = s.lower()
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", s)


# ── 视频/直播网址识别 ────────────────────────────────────────
_VIDEO_PAT = re.compile(
    r"(bilibili\.com/video/|b23\.tv/|youtube\.com/watch|youtu\.be/"
    r"|v\.qq\.com/x/|youku\.com/v_show|iqiyi\.com/v_|ixigua\.com/"
    r"|mgtv\.com/b/|tv\.sohu\.com/v/|douyin\.com/video|weibo\.(com|cn)/tv"
    r"|acfun\.cn/v/|le\.com/ptv/vplay)", re.I)
_LIVE_PAT = re.compile(
    r"(live\.bilibili\.com|youtube\.com/live|live\.douyin\.com"
    r"|huya\.com/|douyu\.com/|live\.qq\.com)", re.I)


def is_video_url(u):
    return bool(_VIDEO_PAT.search(u or ""))


def is_live_url(u):
    return bool(_LIVE_PAT.search(u or ""))


def canonical_url(u):
    """把网址归一成可比较的形式（去追踪参数、统一同一视频的不同写法）。

    为什么需要：B 站/YouTube 的 URL 上挂着一堆 spm_id_from / vd_source
    / trackid 之类的追踪参数，同一个视频会有十几种写法；而且推荐位自动
    连播时 URL 会变。归一之后才能对候选去重。
    """
    u = (u or "").strip()
    if not u:
        return ""
    m = re.search(r"(BV[0-9A-Za-z]+)", u)
    if m:
        return f"https://www.bilibili.com/video/{m.group(1)}"
    m = re.search(r"youtu\.be/([\w\-]+)", u)
    if m:
        return f"https://www.youtube.com/watch?v={m.group(1)}"
    m = re.search(r"[?&]v=([\w\-]+)", u)
    if m:
        return f"https://www.youtube.com/watch?v={m.group(1)}"
    return u.split("#")[0].split("?")[0].rstrip("/")


# ── 历史库定位 ───────────────────────────────────────────────
def _user_data_dirs():
    """各种 Chrome 系浏览器的 'User Data' 目录（存在才算）。"""
    la = os.path.expandvars(r"%LOCALAPPDATA%")
    cands = [
        ("quark", os.path.join(la, "Quark", "User Data")),
        ("edge", os.path.join(la, "Microsoft", "Edge", "User Data")),
        ("chrome", os.path.join(la, "Google", "Chrome", "User Data")),
        ("brave", os.path.join(la, "BraveSoftware",
                               "Brave-Browser", "User Data")),
        ("vivaldi", os.path.join(la, "Vivaldi", "User Data")),
        ("360", os.path.join(la, "360Chrome", "Chrome", "User Data")),
        ("qq", os.path.join(la, "Tencent", "QQBrowser", "User Data")),
        ("sogou", os.path.join(la, "SogouExplorer", "User Data")),
    ]
    return [(n, d) for n, d in cands if os.path.isdir(d)]


def history_dbs():
    """返回 [(浏览器名, History 库路径), …]，含多用户 Profile。"""
    out = []
    for name, ud in _user_data_dirs():
        for p in glob.glob(os.path.join(ud, "*", "History")):
            # 排除临时/备份文件
            if os.path.basename(os.path.dirname(p)).startswith("."):
                continue
            out.append((name, p))
    return out


def _open_copy(db_path):
    """把库复制到临时目录再打开（含 -wal/-journal），返回 (conn, tmpdir)。"""
    tmpd = tempfile.mkdtemp(prefix="ls_hist_")
    dst = os.path.join(tmpd, "History")
    shutil.copy2(db_path, dst)
    for ext in ("-wal", "-journal", "-shm"):
        src = db_path + ext
        if os.path.isfile(src):
            try:
                shutil.copy2(src, dst + ext)
            except Exception:
                pass
    conn = sqlite3.connect(dst)
    return conn, tmpd


def iter_history(db_path, limit=600, max_age_days=None):
    """从历史库读最近的访问记录 → [(url, title, unix_time), …]。"""
    conn, tmpd = _open_copy(db_path)
    try:
        conn.text_factory = lambda b: b.decode("utf-8", "replace")
        rows = conn.execute(
            "SELECT url, title, last_visit_time FROM urls "
            "ORDER BY last_visit_time DESC LIMIT ?", (limit,)).fetchall()
    except Exception:
        rows = []
    finally:
        try:
            conn.close()
        except Exception:
            pass
        try:
            shutil.rmtree(tmpd, ignore_errors=True)
        except Exception:
            pass
    out = []
    for u, t, lv in rows:
        ts = _chrome_time_to_unix(lv)
        if max_age_days and ts > 0 \
                and (time.time() - ts) > max_age_days * 86400:
            continue
        out.append((u or "", t or "", ts))
    return out


def _search_rows(title, min_ratio, video_only, dbs, max_age_days):
    """单档匹配：返回 {canon_url: 候选}。"""
    want = norm_title(title)
    found = {}
    if not want:
        return found
    for _name, db in (dbs if dbs is not None else history_dbs()):
        try:
            rows = iter_history(db, max_age_days=max_age_days)
        except Exception:
            continue
        for u, htitle, ts in rows:
            if is_live_url(u):
                continue            # 直播页没有可预取的整段音频，任何档都排除
            if video_only and not is_video_url(u):
                continue
            have = norm_title(htitle)
            if not have:
                continue
            r = 0.97 if (want in have or have in want) \
                else difflib.SequenceMatcher(None, want, have).ratio()
            if r < min_ratio:
                continue
            cu = canonical_url(u) or u
            old = found.get(cu)
            if old is None or (round(r, 3), ts) > (round(old["score"], 3),
                                                  old["ts"]):
                found[cu] = {"url": u, "title": htitle, "score": r, "ts": ts}
    return found


def search_all(title, min_ratio=0.72, max_age_days=90, video_only=True,
               dbs=None, limit=8):
    """按标题反查，返回**全部**候选（去重、按相似度和访问时间排序）。

    为什么要返回列表而不是一条：实测发现同一个标题在历史里会对应多个
    网址（B 站推荐位自动连播会边换 URL 边沿用旧标题，历史里就留下
    "标题正确、网址错误"的记录）。只取最近访问的那条会取错，
    必须返回候选、再由上层用时长效验挑出真正在播的那个。

    **两档渐进**：先按调用方给的严格条件（默认 视频网址 + 0.72）；
    一无所获时自动放宽 —— 不限定视频 URL（夸克等浏览器里视频页常是
    包装/跳转链接，不在 _VIDEO_PAT 里）且相似度降到 0.60。
    放宽档的候选排在严格档之后（relaxed 标记 + 排序权重），
    上层的时长效验会把不对的剔掉。

    返回 [{"url","title","score","ts","relaxed"}, …]（url 已归一化去重）。
    """
    strict = _search_rows(title, min_ratio, video_only, dbs, max_age_days)
    relaxed = {}
    if not strict:
        relaxed = _search_rows(title, max(0.5, min_ratio - 0.12),
                               False, dbs, max_age_days)
    for c in strict.values():
        c["relaxed"] = False
    for c in relaxed.values():
        c["relaxed"] = True
    found = {**relaxed, **strict}           # 同一网址严格档优先
    out = sorted(found.values(),
                 key=lambda c: (not c["relaxed"], round(c["score"], 3),
                                c["ts"]),
                 reverse=True)
    return out[:limit]


def search(title, min_ratio=0.72, max_age_days=90, video_only=True,
           dbs=None):
    """取最匹配的一条（兼容旧接口）。返回 (url, title, 得分) 或 None。"""
    cands = search_all(title, min_ratio=min_ratio,
                       max_age_days=max_age_days, video_only=video_only,
                       dbs=dbs, limit=1)
    if not cands:
        return None
    c = cands[0]
    return c["url"], c["title"], c["score"]


def resolve(title, **kw):
    """便捷封装：只返回最近匹配的网址（None = 没找到）。"""
    r = search(title, **kw)
    if not r:
        return None
    url, _t, _s = r
    if is_live_url(url):            # 直播页没有可预取的整段音频
        return None
    return url


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("已发现的历史库：")
        for n, p in history_dbs():
            print(f"  [{n}] {p}")
        print("\n用法: python browser_history.py \"视频标题\"")
        raise SystemExit(0)

    q = sys.argv[1]
    print(f"反查: {q!r}")
    print(f"归一化: {norm_title(q)!r}")
    hit = search(q)
    if not hit:
        print("  未命中（试放宽 min_ratio 或用完整标题）")
    else:
        u, t, s = hit
        print(f"  命中（相似度 {s:.2f}）")
        print(f"  标题: {t}")
        print(f"  网址: {u}")
