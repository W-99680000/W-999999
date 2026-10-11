# -*- coding: utf-8 -*-
"""装 argos 的越南语包（vi→en / en→vi），用于「vi→en→zh」中继路径。"""
import io
import os
import ssl
import sys
import time
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
os.makedirs(OUT, exist_ok=True)

import argostranslate.package as P
import argostranslate.translate as T

P.update_package_index()
WANT = [("vi", "en"), ("en", "vi")]
for fc, tc in WANT:
    pk = [p for p in P.get_available_packages()
          if p.from_code == fc and p.to_code == tc]
    if not pk:
        print(f"  {fc}->{tc} 索引里没有"); continue
    pk = pk[0]
    url = pk.links[0]
    dest = os.path.join(OUT, os.path.basename(url))
    if not (os.path.exists(dest) and os.path.getsize(dest) > 1000):
        print(f"下载 {fc}->{tc} …")
        t0 = time.time()
        r = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(r, timeout=600, context=CTX) as resp, \
                open(dest, "wb") as f:
            total = int(resp.headers.get("Content-Length") or 0)
            got = 0
            while True:
                b = resp.read(1 << 20)
                if not b:
                    break
                f.write(b); got += len(b)
                if total:
                    print(f"\r  {got/1e6:6.1f}/{total/1e6:.1f} MB  {got/total*100:5.1f}%",
                          end="", flush=True)
        print(f"\n  完成 {os.path.getsize(dest)/1e6:.1f} MB  {time.time()-t0:.0f}s")
    else:
        print(f"  已有 {os.path.basename(dest)}")
    try:
        P.install_from_path(dest)
        print(f"  安装 {fc}->{tc} OK")
    except Exception as e:
        print(f"  安装失败 {type(e).__name__} {str(e)[:80]}")

print()
langs = {l.code: l for l in T.get_installed_languages()}
print("已装:", sorted(langs))
for src, dst in (("vi", "en"), ("en", "zh")):
    if src in langs and dst in langs:
        tr = langs[src].get_translation(langs[dst])
        print(f"  {src}->{dst}: {'可用' if tr else '不可用'}")
