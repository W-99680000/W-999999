# -*- coding: utf-8 -*-
"""重点验证 MyMemory 是否真的可用（上次它在长句上返回维基模板）。
   用真实越南新闻句子逐条测，判据：译出的是中文 + 不含模板痕迹 + 不是复述原文。
   同时测它的限流边界（免费档通常有每日字数上限）。
"""
import io
import json
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"

# 真实越南新闻句子（从刚才的 run.log 里取的）
SENTS = [
    "tình quảng ninh đưa ra xét sử bị cáo doãn chung dũng",
    "và vụ thạm án tại phường Phương Nam, thành phố Uống",
    "khiến bốn bà cháu trong một gia đình tử vong.",
    "áp lực từ dư luận cũng như những chỉ đạo của khách",
    "Nóng lên từng giờ, nhưng ngoài thông tin chứng cứ để",
    "trẻ thủ ác vẫn còn là một ứng số.",
    "Cảm ơn các bạn và hẹn gặp lại video này.",
    "Theo tin từ chính phủ, kinh tế năm nay tăng trưởng tốt.",
    "Giã trung dũng bị áp dải đến toa trong bộ mặt hốc",
    "Mục đích cuối cùng của lối tượng là cơ tài sản",
]

CRUFT = ("存档", "学名", "帮助", "维基", "Wikipedia", "请检查")


def tr_mymemory(text, pair="vi|zh-CN", timeout=15):
    u = ("https://api.mymemory.translated.net/get?q="
         + urllib.parse.quote(text[:480]) + f"&langpair={pair}")
    r = urllib.request.Request(u, headers={"User-Agent": UA})
    with urllib.request.urlopen(r, timeout=timeout, context=CTX) as resp:
        j = json.loads(resp.read().decode("utf-8", "replace"))
    rd = j.get("responseData") or {}
    return rd.get("translatedText", ""), j.get("quotaFinished"), j.get("matches")


print("=" * 96)
print("MyMemory vi→zh 逐句实测（真实越南新闻句子）")
print("=" * 96)
ok = bad = 0
times = []
for i, s in enumerate(SENTS, 1):
    try:
        t0 = time.time()
        zh, qf, mt = tr_mymemory(s)
        el = time.time() - t0
        times.append(el)
        is_cruft = any(c in zh for c in CRUFT)
        has_cjk = bool(re.search(r"[\u4e00-\u9fff]", zh))
        # 复述原文（没翻译）也是失败
        echo = zh.strip().lower() == s.strip().lower()
        good = has_cjk and not is_cruft and not echo and len(zh) > 1
        ok += good; bad += (not good)
        mark = "OK  " if good else "BAD "
        note = []
        if is_cruft: note.append("模板")
        if echo: note.append("复述原文")
        if not has_cjk: note.append("无中文")
        print(f"  [{mark}]{i:>2} {el:4.2f}s  VI: {s[:44]}")
        print(f"            ZH: {zh[:70]}   {' '.join(note)}")
    except Exception as e:
        bad += 1
        print(f"  [ERR ]{i:>2}        VI: {s[:44]}")
        print(f"            {type(e).__name__} {str(e)[:70]}")

print()
print(f"  成功 {ok} / 失败 {bad}   平均 {sum(times)/max(1,len(times)):.2f}s")
print()
print("=" * 96)
print("限流探测：快速连打 20 次，看是否触发 429 / quotaFinished")
print("=" * 96)
hit = 0
for k in range(20):
    try:
        zh, qf, mt = tr_mymemory("xin chào", timeout=8)
        if qf:
            print(f"  第 {k+1} 次：quotaFinished={qf}（**已到额度**）")
            hit += 1
            break
    except Exception as e:
        print(f"  第 {k+1} 次：{type(e).__name__} {str(e)[:60]}")
        hit += 1
        break
if not hit:
    print("  连打 20 次未触发限流")
print()
print("  注：MyMemory 免费档匿名使用通常有每日字数上限（约 5000 词/天，"
      "按邮箱注册后 50000 词/天）。")
