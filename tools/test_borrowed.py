# -*- coding: utf-8 -*-
"""验证本轮借鉴成熟方案的五项改动是否真的生效。

  ① LanguageLock.peek()：只读不投票
  ② 批前定译器：language lock 在转写前取
  ③ LRU + TTL 缓存：容量淘汰、过期、命中会挪到最新
  ④ 失败不污染字幕：不返回 [译错 ...]
  ⑤ google-free 用 sl=auto
  ⑥ 坐标占位符校验：默认位置不算用户设置
"""
import io
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

ok = fail = 0
def chk(n, c):
    global ok, fail
    if c:
        ok += 1; print(f"  ok   {n}")
    else:
        fail += 1; print(f"  FAIL {n}")

import live_subtitle_v2 as M

print("=== ① LanguageLock.peek() ===")
lk = M.LanguageLock()
chk("初始 peek 为 None", lk.peek() is None)
chk("peek 不改状态", lk.locked is None and lk.votes == {} and lk.history == [])
lk.observe("vi", 0.97)
chk("1 票还没锁（min_votes=3）", lk.peek() is None)
lk.observe("vi", 0.96)
chk("2 票还没锁", lk.peek() is None)
lk.observe("vi", 0.95)
chk("3 票锁定 vi", lk.peek() == "vi")
before = (lk.locked, dict(lk.votes), len(lk.history))
lk.peek()
chk("peek 后状态完全没变", (lk.locked, dict(lk.votes), len(lk.history)) == before)
lk2 = M.LanguageLock()
for lg, p in (("en", 0.227), ("en", 0.340), ("zh", 0.659),
              ("zh", 0.552), ("ko", 0.661)):
    lk2.observe(lg, p)
chk("低置信全被拒（一票没投）", lk2.votes == {} and lk2.locked is None)
chk("拒票计数 = 5", lk2.rejected_lowconf == 5)

print("\n=== ② 源语言路由（ASR 异步化后的顺序）===")
# 背景：这一节原来验的是"批前 peek 定译器"。去掉语言锁定后按**本批自己的
# 检测结果**路由；本轮又把 ASR 移出主线程（异步），所以顺序变成：
#   后台提交 ASR → 主线程取回结果 → 喂诊断投票 → 换译器/路由
# 下面验这个新顺序（旧断言 "texts = th.transcribe_batch(segs_in" 已不存在）。
src = open(os.path.join(HERE, "live_subtitle_v2.py"), encoding="utf-8").read()
i_sub = src.find("_asr_fut = _asr_pool.submit(th.transcribe_batch")
i_helper0 = src.find("def _on_asr_done(")
i_helper1 = src.find("    try:\n        while True:", max(i_helper0, 0))
helper = src[i_helper0:i_helper1] if (i_helper0 >= 0 and i_helper1 > i_helper0) else ""
h_res = helper.find("texts = fut.result()")
h_obs = helper.find("lang_lock.observe(th.last_lang")
h_sw = helper.find("_switch_by_lang()")
i_sw = src.find("def _switch_by_lang(")
i_pick = src.find("want = _pick_offline_source(th.last_lang", max(i_sw, 0))
chk("ASR 在后台线程提交（不阻塞主循环）", i_sub > 0)
chk("ASR 结果在主线程取回（_on_asr_done 里）", i_helper0 > 0 and h_res > 0)
chk("投票在结果之后喂（只用于诊断显示）", h_res < h_obs)
chk("路由决策在结果之后（用本批检测结果）", h_res < h_sw)
chk("路由判据在独立函数 _switch_by_lang 里", i_sw > 0 and i_pick > i_sw)
chk("已无'批前 peek 定译器'的老写法",
    "locked = lang_lock.peek()" not in src)
chk("初始译器是 auto（不是 args.src，避免第一批把非英语喂给 en→zh）",
    'Translator(args.lang or "auto"' in src and "Translator(args.src" not in src)
chk("路由判据存在且是独立函数", "def _pick_offline_source(" in src)

print("\n=== ③ LRU + TTL 缓存 ===")
c = M.LruTtlCache(max_size=3, ttl=0.4)
c.set("a", "甲"); c.set("b", "乙"); c.set("c", "丙")
chk("容量 3 装得下", len(c) == 3)
chk("get 命中", c.get("a") == "甲")
c.set("d", "丁")                    # 超容量
chk("超容量淘汰最旧的（a 在 get 后已挪新，b 最旧）", c.get("b") is None)
chk("len 仍为 3", len(c) == 3)
chk("淘汰计数", c.evicted == 1)
c2 = M.LruTtlCache(max_size=10, ttl=0.3)
c2.set("k", "v")
chk("过期前命中", c2.get("k") == "v")
time.sleep(0.35)
chk("过期后失效", c2.get("k") is None)
chk("过期计数", c2.expired == 1)
chk("Translator 用的是 LruTtlCache",
    type(M.Translator("en", "zh").cache).__name__ == "LruTtlCache")

print("\n=== ④ 失败不污染字幕 ===")
t = M.Translator("en", "zh")
t.mode = "google-free"
t._google_free = lambda s: (_ for _ in ()).throw(OSError("boom"))
t._pivot = None                      # 没有中继可退 → 必须返回空串
out = t("some text that will fail")
chk("失败返回空串", out == "")
chk("绝不返回 [译错 ...]", "[译错" not in out)
chk("不返回 [ 开头的占位", not out.startswith("["))

print("\n=== ⑤ google-free 用 sl=auto ===")
chk("_google_free 里是 sl=auto", "sl=auto" in src)
chk("不再用 sl={self.src}", "sl={self.src}" not in src)

print("\n=== ⑥ 坐标占位符校验 ===")
os.environ.setdefault("QT_QPA_PLATFORM", "windows")
from PyQt5.QtWidgets import QApplication
app = QApplication([])
sp = os.path.join(HERE, "overlay_qt.json")
if os.path.exists(sp):
    os.remove(sp)
import overlay_qt as O
scr = QApplication.primaryScreen().availableGeometry()
dw = min(780, scr.width() - 8)
dflt_x = scr.left() + (scr.width() - dw) // 2
res = O.QtOverlay._validate({"x": dflt_x, "y": 500, "width": 780})
chk(f"默认水平居中位置被拒绝（x={dflt_x}）", "x" not in res)
chk("y 仍保留（只有 x 有默认值可判）", res.get("y") == 500)
res2 = O.QtOverlay._validate({"x": dflt_x + 137, "width": 780})
chk("用户真拖过的 x 保留", res2.get("x") == dflt_x + 137)

print(f"\n合计 {ok} 通过 / {fail} 失败")
sys.exit(0 if fail == 0 else 1)
