# -*- coding: utf-8 -*-
"""_diag_resolve.py —— "标题 → 网址 → 时长核对" 全链路诊断（不需要模型）。

排查两类实际问题：
  ① "未找到对应的网址" —— 逐档打印反查候选（严格档/放宽档），
     列出发现了哪些浏览器历史库、每档命中几条；
  ② "视频识别" —— 列出当前系统全部媒体会话及其打分细节
     （标题/播放中/浏览器/时间线前进），看 _pick 选对没有。

跑法：
    python bench/_diag_resolve.py                # 只看病历（历史库+会话）
    python bench/_diag_resolve.py "视频标题"     # 外加完整反查链路
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def show_history_dbs():
    from browser_history import history_dbs
    dbs = history_dbs()
    print(f"== 发现的浏览器历史库（{len(dbs)} 个）==")
    for n, p in dbs:
        print(f"  [{n}] {p}")
    if not dbs:
        print("  ！一个都没有 —— 反查注定 no_url。"
              "确认浏览器是 Chrome 系且用过它播视频。")
    print()


def show_sessions():
    from smtc_tracker import _OK
    print("== 当前系统媒体会话 ==")
    if not _OK:
        print("  ！winsdk 未安装，SMTC 不可用")
        return
    import asyncio
    from winsdk.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as Mgr)

    async def probe():
        mgr = await Mgr.request_async()
        return mgr.get_sessions()

    ss = asyncio.run(probe())
    print(f"  共 {len(ss)} 个会话：")
    for s in ss:
        try:
            import asyncio as _a
            props = _a.run(s.try_get_media_properties_async())
            title = props.title or "(无标题)"
        except Exception:
            title = "(读不到)"
        try:
            st = int(s.get_playback_info().playback_status)
            st = {4: "播放中", 5: "暂停"}.get(st, str(st))
        except Exception:
            st = "?"
        try:
            tl = s.get_timeline_properties()
            pos = f"{tl.position.total_seconds():.1f}s"
        except Exception:
            pos = "无时间线"
        print(f"  [{s.source_app_user_model_id}] {st}　{pos}　{title}")
    if not ss:
        print("  ！没有会话 —— 先让视频播起来再跑本脚本")
    print()


def show_resolve(title):
    from browser_history import norm_title, search_all
    from autopipe import probe_duration
    print(f"== 反查链路：{title!r} ==")
    print(f"  归一化: {norm_title(title)!r}")
    t0 = time.time()
    cands = search_all(title)
    print(f"  候选 {len(cands)} 条（{time.time()-t0:.2f}s）：")
    for c in cands:
        tag = "放宽档" if c.get("relaxed") else "严格档"
        print(f"    [{tag}] 相似度 {c['score']:.2f}　{c['url'][:70]}")
        print(f"           历史标题: {c['title'][:60]}")
    if not cands:
        print("  ！零候选 —— 常见原因：历史还没落盘（刚打开视频，"
              "等几秒再跑）、无痕模式、标题差异过大")
        return
    print()
    print("== 时长效验（模拟 _pick_candidate）==")
    for c in cands[:4]:
        t0 = time.time()
        d = probe_duration(c["url"])
        print(f"    {d and f'{d:.0f}s' or '探不到'}"
              f"（{time.time()-t0:.1f}s）　{c['url'][:56]}")
    print("  对照 SMTC 报的时长，谁对上谁是正主。")


if __name__ == "__main__":
    show_history_dbs()
    show_sessions()
    if len(sys.argv) > 1:
        show_resolve(sys.argv[1])
    else:
        print("（加标题参数可跑完整反查：python bench/_diag_resolve.py "
              "\"视频标题\"）")
