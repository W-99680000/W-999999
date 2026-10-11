# -*- coding: utf-8 -*-
"""探测免费翻译端点的可达性与实际可用性。
   上次测过（几小时前）：Google 免费端点超时、LibreTranslate 403、MyMemory 可用但返回维基模板。
   现在重测一遍，并加上中文厂商的免费档端点。
   判据：能连通 + 能返回可用的中文译文（不是模板、不是空）。
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
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
VI = "Theo tin từ chính phủ, kinh tế năm nay tăng trưởng tốt."


def probe(name, url, data=None, headers=None, method=None, timeout=12):
    t0 = time.time()
    try:
        h = {"User-Agent": UA}
        if headers:
            h.update(headers)
        if data is not None:
            body = json.dumps(data).encode() if isinstance(data, (dict, list)) else data
            h.setdefault("Content-Type", "application/json")
            r = urllib.request.Request(url, data=body, headers=h, method=method or "POST")
        else:
            r = urllib.request.Request(url, headers=h, method=method or "GET")
        with urllib.request.urlopen(r, timeout=timeout, context=CTX) as resp:
            b = resp.read(600)
            el = time.time() - t0
            return True, el, b.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return False, time.time() - t0, f"HTTP {e.code} {e.read(200).decode('utf-8','replace')[:120]}"
    except Exception as e:
        return False, time.time() - t0, f"{type(e).__name__} {str(e)[:100]}"


print("=" * 90)
print("一、免费/无 key 端点可达性")
print("=" * 90)
tests = [
    ("Google translate free (gtx)", "https://translate.googleapis.com/translate_a/single"
     "?client=gtx&sl=vi&tl=zh-CN&dt=t&q=" + urllib.parse.quote(VI), None, None, None),
    ("MyMemory (vi|zh-CN)", "https://api.mymemory.translated.net/get?q="
     + urllib.parse.quote(VI[:200]) + "&langpair=vi|zh-CN", None, None, None),
    ("LibreTranslate public", "https://libretranslate.com/translate", 
     {"q": VI, "source": "vi", "target": "zh", "format": "text"}, None, None),
    ("libretranslate.de", "https://libretranslate.de/translate",
     {"q": VI, "source": "vi", "target": "zh", "format": "text"}, None, None),
    ("translate.plus (免费)", "https://translate.plus/api/translate",
     {"text": VI, "from": "vi", "to": "zh"}, None, None),
    ("SiliconFlow models", "https://api.siliconflow.cn/v1/models", None,
     {"Authorization": "Bearer none"}, None),
    ("DeepSeek models", "https://api.deepseek.com/v1/models", None,
     {"Authorization": "Bearer none"}, None),
    ("DashScope (百炼) models",
     "https://dashscope.aliyuncs.com/compatible-mode/v1/models", None,
     {"Authorization": "Bearer none"}, None),
    ("百度翻译开放平台", "https://fanyi-api.baidu.com/api/trans/vip/translate"
     "?q=" + urllib.parse.quote(VI[:50]) + "&from=vi&to=zh&appid=x&salt=1&sign=x",
     None, None, None),
    ("腾讯翻译君", "https://transmart.qq.com/api/imt", None, None, None),
    ("Bing translator (无 key)", "https://api.cognitive.microsofttranslator.com/translate"
     "?api-version=3.0&from=vi&to=zh-Hans", None, None, None),
]
for name, url, data, hdrs, m in tests:
    ok, el, txt = probe(name, url, data, hdrs, m)
    flag = "OK  " if ok else "FAIL"
    print(f"  [{flag}] {name:<28} {el:5.1f}s  {txt[:110]}")
