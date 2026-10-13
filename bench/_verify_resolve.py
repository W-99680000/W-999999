# -*- coding: utf-8 -*-
"""_verify_resolve.py —— "网址反查（两档渐进）+ no_url 退避" 的纯逻辑单测。

用临时 Chrome 风格 History 库（sqlite）注入假数据，不碰真实历史库。
覆盖：
  ① 严格档命中时不放宽
  ② 严格档零结果 → 放宽档拿到"包装 URL"（非 _VIDEO_PAT）候选，标记 relaxed
  ③ 直播 URL 任何档都排除
  ④ 相似度低于放宽阈值不返回
  ⑤ 去重（同一 BV 的不同写法归一成一条）
  ⑥ no_url_backoff：不耗真尝试次数、45s 冷却、上限封顶

跑法：python bench/_verify_resolve.py
"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from browser_history import search_all, canonical_url, norm_title
from watch_player import no_url_backoff

ok = fail = 0


def chk(name, cond):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ok   {name}")
    else:
        fail += 1
        print(f"  FAIL {name}")


_EPOCH = 11644473600
CT = "10月新番「FX战士久留美」第1话【中文字幕】"

# ── 造一个假 History 库 ──
rows = [
    # 严格档：标题完全一样 + 正常 B 站视频 URL
    (f"https://www.bilibili.com/video/BV1wwa86KE2C/?spm_id_from=333.337",
     CT + "_哔哩哔哩_bilibili"),
    # 去重：同一 BV 的另一种写法（只有严格档能满足标题时才可能撞）
    ("https://b23.tv/BV1wwa86KE2C", CT + "_哔哩哔哩_bilibili"),
    # 直播页：任何档都要排除
    (f"https://live.bilibili.com/12345?title={CT}", CT),
]
tmpd = tempfile.mkdtemp(prefix="ls_vfy_")
dbp = os.path.join(tmpd, "History")
conn = sqlite3.connect(dbp)
conn.execute("CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT, "
             "title TEXT, visit_count INTEGER, last_visit_time INTEGER)")
import time as _time
base = _EPOCH + int(_time.time())          # 必须"最近"，否则被 90 天过滤掉
for i, (u, t) in enumerate(rows):
    conn.execute("INSERT INTO urls (url,title,visit_count,last_visit_time)"
                 " VALUES (?,?,?,?)", (u, t, 3, (base + i) * 1_000_000))
conn.commit()
conn.close()
DBS = [("fake", dbp)]

print("== 严格档命中 ==")
c1 = search_all(CT, dbs=DBS)
chk("有候选", len(c1) >= 1)
chk("严格档不放松", all(not x["relaxed"] for x in c1))
urls = [x["url"] for x in c1]
chk("直播 URL 被排除", not any("live.bilibili.com" in u for u in urls))
# 去重：两条都是 BV1wwa86KE2C → 归一后同一条
chk("同 BV 去重为一条",
    len([u for u in urls if "BV1wwa86KE2C" in canonical_url(u)]) == 1)

print("== 放宽档：严格档零结果时接管 ==")
# 造第二个库：只有一条"包装 URL"（不含任何 _VIDEO_PAT 特征）+ 略低的相似度
tmpd2 = tempfile.mkdtemp(prefix="ls_vfy2_")
dbp2 = os.path.join(tmpd2, "History")
conn = sqlite3.connect(dbp2)
conn.execute("CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT, "
             "title TEXT, visit_count INTEGER, last_visit_time INTEGER)")
wrapped = "https://m.quark.cn/s?share=abcd#/play?from=web&vid=xyz"
# 相似度略低：标题少一个括号词，SequenceMatcher 会跌到 0.6 档
t2 = CT.replace("【中文字幕】", "")
conn.execute("INSERT INTO urls (url,title,visit_count,last_visit_time)"
             " VALUES (?,?,?,?)", (wrapped, t2, 9, base * 1_000_000))
conn.commit()
conn.close()
c2 = search_all(CT, dbs=[("fake2", dbp2)])
chk("放宽档拿到包装 URL 候选", len(c2) == 1)
chk("放宽档被标记", c2 and c2[0]["relaxed"] is True)
chk("拿到的正是包装 URL", c2 and c2[0]["url"] == wrapped)

print("== 相似度太低不返回 ==")
c3 = search_all("完全不相干的一个视频名字", dbs=[("fake2", dbp2)])
chk("零候选", c3 == [])

print("== 严格档命中时不混入放宽档 ==")
# 设计：严格档非空 → 根本不去跑放宽档（省一次全库扫描，
# 也避免包装链接污染本来就准确的候选）
c4 = search_all(CT, dbs=DBS + [("fake2", dbp2)])
chk("有候选", len(c4) >= 1)
chk("全部来自严格档", all(not x["relaxed"] for x in c4))
chk("放宽档的包装 URL 不在其中",
    not any(x["url"] == wrapped for x in c4))
# 排序：relaxed 标记恒在末尾（这里的 vacuous 情形也一并守住）
flags = [x["relaxed"] for x in c4]
chk("relaxed 全在末尾", flags == sorted(flags))

print("== no_url_backoff ==")
tried = {}
okr, tr = no_url_backoff(tried, "t1", now=1000.0)
chk("首次仍允许重试", okr is True)
chk("独立计数 nu=1", tr["nu"] == 1)
chk("真尝试计数退回为 0", tr["n"] == 0)
chk("冷却为 45s 档", abs(tr["ts"] - (1000.0 + (45.0 - 20.0))) < 1e-6)
# 模拟真实尝试已经耗掉 1 次：no_url 后应退回到 0
tried["t2"] = {"n": 1, "ts": 0.0}
_ok, tr2 = no_url_backoff(tried, "t2", now=2000.0)
chk("耗掉的计数被退回", tr2["n"] == 0)
# 累计到上限：不再重试
tried["t3"] = {"n": 0, "ts": 0.0, "nu": 20}
ok3, tr3 = no_url_backoff(tried, "t3", now=3000.0, max_tries=20)
chk("到上限不再重试", ok3 is False)
chk("到上限不再改冷却", tr3["ts"] == 0.0)

print(f"\n合计 {ok} 通过 / {fail} 失败")
sys.exit(0 if fail == 0 else 1)
