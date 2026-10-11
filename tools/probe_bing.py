# -*- coding: utf-8 -*-
"""单独测微软 edge 端点（免 key 的网页接口），以及 Bing 网页端。
   这个端点如果通，是免 key 里质量最好的（微软翻译引擎）。
"""
import io
import json
import ssl
import sys
import time
import urllib.error
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
SENTS = [
    "Theo tin từ chính phủ, kinh tế năm nay tăng trưởng tốt.",
    "khiến bốn bà cháu trong một gia đình tử vong.",
    "tình quảng ninh đưa ra xét sử bị cáo doãn chung dũng",
]


def get(url, timeout=12, extra=None):
    h = {"User-Agent": UA}
    if extra:
        h.update(extra)
    r = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(r, timeout=timeout, context=CTX) as resp:
        return resp.read().decode("utf-8", "replace")


print("=" * 88)
print("微软 edge 免 key 端点")
print("=" * 88)
tok = None
try:
    t0 = time.time()
    tok = get("https://edge.microsoft.com/translate/auth", timeout=12)
    print(f"  auth token: {len(tok)} 字节，{time.time()-t0:.2f}s，"
          f"开头 {tok[:40]!r}")
    print(f"  像 JWT: {tok.count('.') == 2}")
except Exception as e:
    print(f"  取 token 失败: {type(e).__name__} {str(e)[:110]}")

if tok and tok.count(".") == 2:
    print()
    for s in SENTS:
        try:
            body = json.dumps([{"Text": s}]).encode()
            r = urllib.request.Request(
                "https://api-edge.cognitive.microsofttranslator.com/translate"
                "?from=vi&to=zh-Hans&api-version=3.0",
                data=body,
                headers={"User-Agent": UA, "Content-Type": "application/json",
                         "Authorization": "Bearer " + tok.strip()})
            t0 = time.time()
            with urllib.request.urlopen(r, timeout=15, context=CTX) as resp:
                j = json.loads(resp.read().decode("utf-8", "replace"))
            zh = j[0]["translations"][0]["text"]
            print(f"  [{time.time()-t0:4.2f}s] {s[:44]}")
            print(f"            → {zh}")
        except urllib.error.HTTPError as e:
            print(f"  HTTP {e.code}: {e.read(200).decode('utf-8','replace')[:120]}")
            break
        except Exception as e:
            print(f"  {type(e).__name__} {str(e)[:100]}")
            break

print()
print("=" * 88)
print("如果上面通：这就是免 key 里质量最好的（微软翻译引擎，越南语是老牌支持）")
print("如果 401/403：需要去 Azure 注册免费档（每月 200 万字符，永久免费）")
print("=" * 88)
