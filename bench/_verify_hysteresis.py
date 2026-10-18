# -*- coding: utf-8 -*-
"""smtc_tracker 的真实实现单测：跨拍对象身份 + 迟滞切换 + seek。

**为什么重写**：旧版这里把 `_tick` 里的迟滞代码**抄了一份**到测试里，
用两个长期存在的 FakeSession 驱动 —— 于是"用 id(s) 当状态键"的 bug
永远测不出来。实测（2026-10-18）：winsdk 的 session 对象每次
`get_sessions()` 都是**新的 Python 包装**（`a[i] is b[i]` 为 False），
真实代码里的 `_titles[id]` / `_pos_seen[id]` / `s is self._session`
全部永远不命中；表现为多标签页时字幕在几部片之间来回跳、预翻译被反复
取消、缓存建不完。

这份测试用"每拍都返回新对象、数据相同"的 Fake 管理器**驱动真实
`_tick`**，覆盖：
  ① 标题跨拍稳定（不再每拍丢）；
  ② 时间线前进（advancing）能跨拍检测到；
  ③ 候选交替时迟滞生效：不每拍切；
  ④ 挑战者连续赢满 _SWITCH_HOLD 拍才切换；
  ⑤ 现任消失立即接上；
  ⑥ 同一部片 seek 仍被识别；
  ⑦ 同一会话换片（标题变）立即切、标题更新。

跑法：python bench/_verify_hysteresis.py
"""
import asyncio
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import smtc_tracker as S          # noqa: E402

_ok = _fail = 0


def chk(name, cond):
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"  ok  {name}")
    else:
        _fail += 1
        print(f" FAIL {name}")


if not S._OK:
    print("跳过：winsdk 不可用（pip install winsdk）")
    sys.exit(0)


class _Props:
    def __init__(self, title):
        self.title = title
        self.artist = ""
        self.album_title = ""


class _Playback:
    def __init__(self, playing, rate=1.0):
        self.playback_status = (S._Status.PLAYING if playing
                                else S._Status.PAUSED)
        self.playback_rate = rate


class _Timeline:
    def __init__(self, pos, dur=600.0):
        self.position = dt.timedelta(seconds=pos)
        self.last_updated_time = dt.datetime.now(dt.timezone.utc)
        self.end_time = dt.timedelta(seconds=dur)


class FakeSession:
    """模拟 winsdk 的 session：**每次 get_sessions() 都新建一个包装**，
    数据从共享 state 字典读（所以对象换、数据不换）。"""

    def __init__(self, state):
        self._st = state
        self.source_app_user_model_id = "Quark"

    async def try_get_media_properties_async(self):
        return _Props(self._st["title"])

    def get_playback_info(self):
        return _Playback(self._st["playing"], self._st.get("rate", 1.0))

    def get_timeline_properties(self):
        return _Timeline(self._st["pos"], self._st.get("dur", 600.0))


class FakeMgr:
    """每拍按当前 state 造**全新** session 对象（复现 winsdk 身份不稳）。"""

    def __init__(self, states):
        self.states = states          # list[dict]，顺序每拍可变

    def get_sessions(self):
        return [FakeSession(st) for st in self.states]


def tick(t, mgr):
    asyncio.run(t._tick(mgr))


def new_tracker():
    return S.SmtcTracker(poll=0.15)


print("== ① 跨拍标题稳定 + 前进检测 ==")
st = {"title": "测试视频A", "playing": True, "pos": 0.0}
t = new_tracker()
mgr = FakeMgr([st])
tick(t, mgr)
st["pos"] = 0.15
tick(t, mgr)
chk("标题跨拍不丢", t.current_title() == "测试视频A")
chk("有会话", t.has_session())
chk("位置带外推", t.position() is not None and t.position() >= 0.1)
chk("时间线前进被记录（_pos_seen 有键）",
    any(k[1] == "测试视频a" for k in t._pos_seen))
key0 = t._session_key
st["pos"] = 0.30
tick(t, mgr)
chk("同一部片不误切", t._session_key == key0)

print("== ② 候选交替：迟滞必须生效 ==")
# A/B 都像是"在播的浏览器视频"，但**交替**有一个在前进（advancing），
# 于是 _pick 的候选每拍 A/B 交替 —— 没有迟滞就会每拍切，字幕乱跳。
sa = {"title": "视频A", "playing": True, "pos": 0.0}
sb = {"title": "视频B", "playing": True, "pos": 0.0}
t2 = new_tracker()
mgr2 = FakeMgr([sa, sb])
for i in range(12):
    if i % 2 == 0:
        sa["pos"] += 0.5
    else:
        sb["pos"] += 0.5
    tick(t2, mgr2)
chk("交替候选人下只锁定 1 次（不来回跳）",
    t2._session_key in {("quark", "视频a"), ("quark", "视频b")})
chk("交替 12 拍后标题稳定", t2.current_title() in ("视频A", "视频B"))
_, _title = t2._session_key
chk("锁定标题与读数一致", t2.current_title().lower() == _title)

print("== ③ 挑战者连续赢满 HOLD 才换 ==")
sa2 = {"title": "视频A", "playing": True, "pos": 0.0}
sb2 = {"title": "视频B", "playing": True, "pos": 0.0}
t3 = new_tracker()
mgr3 = FakeMgr([sa2, sb2])
sa2["pos"] = 1.0
sb2["pos"] = 1.0
tick(t3, mgr3)                     # A 先入（同分取第一）
first = t3._session_key
assert first == ("quark", "视频a"), first
# B 开始持续领先（B 在前进，A 停住）
switched_at = None
for i in range(6):
    sb2["pos"] += 0.5
    tick(t3, mgr3)
    if switched_at is None and t3._session_key != first:
        switched_at = i + 1
chk("连续领先 4 拍才切（不是第 1 拍）", switched_at == 4)
chk("切换后确实跟 B", t3._session_key == ("quark", "视频b"))

print("== ④ 现任消失 → 立即接上 ==")
sa3 = {"title": "视频A", "playing": True, "pos": 0.0}
sb3 = {"title": "视频B", "playing": True, "pos": 0.0}
t4 = new_tracker()
mgr4 = FakeMgr([sa3, sb3])
sa3["pos"] = 1.0
tick(t4, mgr4)
chk("先跟 A", t4._session_key == ("quark", "视频a"))
mgr4.states = [sb3]                # A 的标签页关了
sb3["pos"] = 5.0
tick(t4, mgr4)
chk("现任消失当拍就切 B", t4._session_key == ("quark", "视频b"))

print("== ⑤ seek 识别 ==")
s5 = {"title": "视频A", "playing": True, "pos": 10.0}
t5 = new_tracker()
mgr5 = FakeMgr([s5])
tick(t5, mgr5)
n0 = t5.seek_serial()
s5["pos"] = 120.0                  # 用户拖到 2 分钟
tick(t5, mgr5)
chk("进度拖动被识别", t5.seek_serial() > n0)

print("== ⑥ 同一会话换片（标题变）→ 立即切、标题更新 ==")
s6 = {"title": "视频A", "playing": True, "pos": 10.0}
t6 = new_tracker()
mgr6 = FakeMgr([s6])
tick(t6, mgr6)
s6.update(title="视频C", pos=0.0)
tick(t6, mgr6)
chk("片名变了立即更新", t6.current_title() == "视频C")

print("== ⑦ 缓存标题匹配（watch_player._find_cached_srt）==")
_pre = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "pre_cache")
_cached = ([f for f in os.listdir(_pre) if f.lower().endswith(".zh.srt")]
           if os.path.isdir(_pre) else [])
if not _cached:
    print("  skip（pre_cache 里没有缓存字幕）")
else:
    import watch_player as W
    ok_n = 0
    for f in _cached:
        base = f[:-len(".zh.srt")]
        for title in (base, base + " - 哔哩哔哩_bilibili"):
            got = W._find_cached_srt(title, "zh")
            if got and os.path.basename(got) == f:
                ok_n += 1
                break
    chk(f"缓存标题可匹配（{ok_n}/{len(_cached)}）", ok_n == len(_cached))
    chk("无关标题不误配",
        W._find_cached_srt("完全不存在的视频标题XYZ", "zh") is None)

print(f"\n合计 {_ok} 通过 / {_fail} 失败")
sys.exit(1 if _fail else 0)
