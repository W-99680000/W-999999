# -*- coding: utf-8 -*-
"""大量实测 clients5.google.com 免 key 端点。
   字幕的硬指标：
     · 延迟 < 1.5s（否则跟不上 4 秒一段的音频）
     · 持续稳定（连打 60 次不能挂）
     · 质量可读（人名地名允许音译，但不能是模板/胡话）
"""
import io
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

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
    "đến nhà những người quen tìm sự giúp đỡ.",
    "Đây là vụ án đặc biệt nghiêm trọng được dư luận quan tâm.",
]

EP = "https://clients5.google.com/translate_a/single?client=dict-chrome-ex&sl=vi&tl=zh-CN&dt=t&q="


def tr(text, timeout=10):
    r = urllib.request.Request(EP + urllib.parse.quote(text[:1500]),
                               headers={"User-Agent": UA})
    with urllib.request.urlopen(r, timeout=timeout, context=CTX) as resp:
        j = json.loads(resp.read().decode("utf-8", "replace"))
    return "".join(x[0] for x in j[0] if x and x[0])


print("=" * 96)
print("一、质量实测")
print("=" * 96)
times = []
ok = 0
for i, s in enumerate(SENTS, 1):
    try:
        t0 = time.time()
        zh = tr(s)
        el = time.time() - t0
        times.append(el)
        has_cjk = bool(re.search(r"[\u4e00-\u9fff]", zh))
        ok += has_cjk
        print(f"  [{el:4.2f}s] {s[:52]}")
        print(f"           → {zh}")
    except Exception as e:
        print(f"  [ERR ] {s[:52]}")
        print(f"           {type(e).__name__} {str(e)[:66]}")

print()
print(f"  成功 {ok}/{len(SENTS)}　平均 {sum(times)/max(1,len(times)):.2f}s　"
      f"最快 {min(times):.2f}s　最慢 {max(times):.2f}s")

print()
print("=" * 96)
print("二、稳定性：连打 60 次")
print("=" * 96)
t0 = time.time()
fails = 0
lat = []
for k in range(60):
    try:
        s = time.time()
        tr(SENTS[k % len(SENTS)][:60])
        lat.append(time.time() - s)
    except Exception as e:
        fails += 1
        if fails <= 3:
            print(f"  第 {k+1} 次失败: {type(e).__name__} {str(e)[:56]}")
el = time.time() - t0
print(f"  60 次用时 {el:.1f}s，失败 {fails} 次")
if lat:
    lat.sort()
    print(f"  延迟：中位 {lat[len(lat)//2]:.2f}s  P90 {lat[int(len(lat)*0.9)]:.2f}s  "
          f"最大 {lat[-1]:.2f}s")

print()
print("=" * 96)
print("三、字幕可用性判定")
print("=" * 96)
p90 = lat[int(len(lat) * 0.9)] if lat else 99
print(f"  延迟 P90 = {p90:.2f}s   失败率 = {fails/60*100:.1f}%")
print(f"  判据：P90 < 1.5s 且 失败率 < 5%")
verdict = "** 可用于字幕 **" if (p90 < 1.5 and fails / 60 < 0.05) else "** 不适合做字幕 **"
print(f"  → {verdict}")
