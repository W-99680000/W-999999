# -*- coding: utf-8 -*-
"""Google 免费翻译：试多个主机/端点，判断是"IP 被限"还是"全都限"。
   同时测延迟，因为字幕要 <1.5s。
"""
import io
import json
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
VI = "khiến bốn bà cháu trong một gia đình tử vong."
Q = urllib.parse.quote(VI)

CANDIDATES = [
    ("translate.googleapis.com gtx",
     f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=vi&tl=zh-CN&dt=t&q={Q}"),
    ("translate.googleapis.com dict",
     f"https://translate.googleapis.com/translate_a/single?client=dict-chrome-ex&sl=vi&tl=zh-CN&dt=t&q={Q}"),
    ("clients5.google.com",
     f"https://clients5.google.com/translate_a/single?client=dict-chrome-ex&sl=vi&tl=zh-CN&dt=t&q={Q}"),
    ("translate.google.com (网页)",
     f"https://translate.google.com/m?sl=vi&tl=zh-CN&q={Q}"),
    ("translate.google.cn",
     f"https://translate.google.cn/translate_a/single?client=gtx&sl=vi&tl=zh-CN&dt=t&q={Q}"),
]

for name, url in CANDIDATES:
    try:
        t0 = time.time()
        r = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(r, timeout=12, context=CTX) as resp:
            b = resp.read().decode("utf-8", "replace")
        el = time.time() - t0
        out = b[:120].replace("\n", " ")
        print(f"  [OK  ] {name:<32} {el:5.2f}s  {out}")
    except urllib.error.HTTPError as e:
        print(f"  [HTTP] {name:<32} {e.code}")
    except Exception as e:
        print(f"  [FAIL] {name:<32} {type(e).__name__} {str(e)[:56]}")

print()
print("=" * 84)
print("结论")
print("=" * 84)
print("  · 若全是 429 → 这台机器的出口 IP 被 Google 限流（免费端点常见）")
print("  · 若某个主机通 → 那个就能用（但同样可能随时被限）")
print("  · 字幕要求延迟 < 1.5s，所以就算通了也要实测延迟和持续可用性")
