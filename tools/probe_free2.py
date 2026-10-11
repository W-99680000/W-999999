# -*- coding: utf-8 -*-
"""继续找能用的免费方案：重点看延迟（字幕要 <1.5s）和稳定性。
   A Google 免费端点 retry（刚才 429，看退避后能否恢复）
   B libretranslate.de 的正确 API 路径
   C 其他免 key 镜像
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
UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
]
VI = "Theo tin từ chính phủ, kinh tế năm nay tăng trưởng tốt."
VI2 = "khiến bốn bà cháu trong một gia đình tử vong."


def get(url, ua=None, timeout=15):
    r = urllib.request.Request(url, headers={"User-Agent": ua or UAS[0]})
    with urllib.request.urlopen(r, timeout=timeout, context=CTX) as resp:
        return resp.read().decode("utf-8", "replace")


print("=" * 92)
print("A、Google 免费端点（换 UA + 退避重试）")
print("=" * 92)
gurl = ("https://translate.googleapis.com/translate_a/single"
        "?client=gtx&sl=vi&tl=zh-CN&dt=t&q=")
for attempt, ua in enumerate(UAS * 2, 1):
    try:
        t0 = time.time()
        b = get(gurl + urllib.parse.quote(VI), ua)
        j = json.loads(b)
        zh = "".join(x[0] for x in j[0] if x and x[0])
        print(f"  第 {attempt} 次 OK  {time.time()-t0:.2f}s  → {zh}")
        break
    except urllib.error.HTTPError as e:
        print(f"  第 {attempt} 次 HTTP {e.code}")
    except Exception as e:
        print(f"  第 {attempt} 次 {type(e).__name__} {str(e)[:60]}")
    time.sleep(3)

print()
print("=" * 92)
print("B、libretranslate 镜像（多种路径）")
print("=" * 92)
mirrors = [
    ("libretranslate.de", "https://libretranslate.de/translate"),
    ("translate.terraprint.co", "https://translate.terraprint.co/translate"),
    ("libretranslate.pussthecat.org", "https://libretranslate.pussthecat.org/translate"),
    ("trans.zillyhuhn.com", "https://trans.zillyhuhn.com/translate"),
    ("lt.vern.cc", "https://lt.vern.cc/translate"),
]
for name, url in mirrors:
    body = json.dumps({"q": VI, "source": "vi", "target": "zh",
                       "format": "text"}).encode()
    try:
        t0 = time.time()
        r = urllib.request.Request(url, data=body, headers={
            "User-Agent": UAS[0], "Content-Type": "application/json"})
        with urllib.request.urlopen(r, timeout=15, context=CTX) as resp:
            j = json.loads(resp.read().decode("utf-8", "replace"))
        print(f"  [OK  ] {name:<30} {time.time()-t0:5.2f}s  {str(j.get('translatedText'))[:60]}")
    except urllib.error.HTTPError as e:
        print(f"  [FAIL] {name:<30} HTTP {e.code} {e.read(120).decode('utf-8','replace')[:70]}")
    except Exception as e:
        print(f"  [FAIL] {name:<30} {type(e).__name__} {str(e)[:60]}")

print()
print("=" * 92)
print("C、Bing/微软免费端点（无 key 的网页接口）")
print("=" * 92)
try:
    t0 = time.time()
    tok = get("https://edge.microsoft.com/translate/auth", timeout=12)
    if tok and len(tok) < 4000 and "." in tok:
        print(f"  拿到 auth token（{len(tok)} 字节，{time.time()-t0:.2f}s）")
        body = json.dumps([{"Text": VI2}]).encode()
        r = urllib.request.Request(
            "https://api-edge.cognitive.microsofttranslator.com/translate"
            "?from=vi&to=zh-Hans&api-version=3.0",
            data=body, headers={
                "User-Agent": UAS[0], "Content-Type": "application/json",
                "Authorization": "Bearer " + tok.strip()})
        with urllib.request.urlopen(r, timeout=15, context=CTX) as resp:
            j = json.loads(resp.read().decode("utf-8", "replace"))
        print(f"  [OK  ] 微软 edge 端点 → {j[0]['translations'][0]['text']}")
    else:
        print(f"  [FAIL] token 异常: {tok[:80]}")
except Exception as e:
    print(f"  [FAIL] {type(e).__name__} {str(e)[:100]}")
