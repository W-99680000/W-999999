# -*- coding: utf-8 -*-
"""持续吞吐测试：模拟字幕的真实负载。
   分块模式每 4 秒音频产生一次翻译请求。看长期跑能不能跟上、会不会被限。
   判据：单次翻译耗时 < 段间隔（4s），且长期不降级。
"""
import io
import json
import random
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
EP = ("https://clients5.google.com/translate_a/single"
      "?client=dict-chrome-ex&sl=vi&tl=zh-CN&dt=t&q=")

# 真实新闻句子池（每段 4 秒音频大约这么长）
POOL = [
    "tình quảng ninh đưa ra xét sử bị cáo doãn chung dũng",
    "và vụ thạm án tại phường Phương Nam, thành phố Uống",
    "khiến bốn bà cháu trong một gia đình tử vong.",
    "áp lực từ dư luận cũng như những chỉ đạo của khách",
    "Nóng lên từng giờ, nhưng ngoài thông tin chứng cứ để",
    "trẻ thủ ác vẫn còn là một ứng số.",
    "Cảm ơn các bạn và hẹn gặp lại video này.",
    "Theo tin từ chính phủ, kinh tế năm nay tăng trưởng tốt.",
    "đến nhà những người quen tìm sự giúp đỡ.",
    "Đây là vụ án đặc biệt nghiêm trọng được dư luận quan tâm.",
]


def tr(text, timeout=10):
    r = urllib.request.Request(EP + urllib.parse.quote(text[:1500]),
                               headers={"User-Agent": UA})
    with urllib.request.urlopen(r, timeout=timeout, context=CTX) as resp:
        j = json.loads(resp.read().decode("utf-8", "replace"))
    return "".join(x[0] for x in j[0] if x and x[0])


print("=" * 92)
print("模拟 5 分钟字幕负载：每 4 秒一段，共 75 段")
print("=" * 92)
SEG_INTERVAL = 4.0
N = 75
lat, fails, backlog = [], 0, 0.0
t_start = time.time()
for k in range(N):
    # 按真实节奏：到点才发
    due = t_start + k * SEG_INTERVAL
    now = time.time()
    if now < due:
        time.sleep(due - now)
    else:
        backlog += now - due          # 记录积压
    s = time.time()
    try:
        tr(random.choice(POOL))
        lat.append(time.time() - s)
    except Exception as e:
        fails += 1
        if fails <= 3:
            print(f"  第 {k+1} 段失败: {type(e).__name__} {str(e)[:56]}")
        break
    if (k + 1) % 15 == 0:
        recent = lat[-15:]
        print(f"  第 {k+1:>2} 段  近15段中位 {sorted(recent)[len(recent)//2]:.2f}s  "
              f"最大 {max(recent):.2f}s  累计失败 {fails}  积压 {backlog:.1f}s")

el = time.time() - t_start
print()
print("=" * 92)
print("结果")
print("=" * 92)
if lat:
    lat_s = sorted(lat)
    print(f"  共 {len(lat)} 段，用时 {el:.0f}s（音频时长 {N*SEG_INTERVAL:.0f}s）")
    print(f"  延迟：中位 {lat_s[len(lat_s)//2]:.2f}s  "
          f"P90 {lat_s[int(len(lat_s)*0.9)]:.2f}s  最大 {lat_s[-1]:.2f}s")
    print(f"  失败 {fails} 次   总积压 {backlog:.1f}s")
    print()
    print(f"  判据：中位延迟 < {SEG_INTERVAL}s（跟得上段落节奏）")
    med = lat_s[len(lat_s)//2]
    if med < SEG_INTERVAL and fails == 0:
        print(f"  → ** 可用于字幕 **（中位 {med:.2f}s 小于段间隔 {SEG_INTERVAL}s，"
              f"无失败）")
    else:
        print(f"  → ** 边缘/不适合 **（中位 {med:.2f}s，失败 {fails}）")
