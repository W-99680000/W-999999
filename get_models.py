# -*- coding: utf-8 -*-
"""下载并安装 argos 翻译模型（直连 argos-net.com，绕开 huggingface）

【重要】执行逻辑全部收在 main() 里，import 本模块**零副作用**。
为什么：这些代码原来在模块级 —— 联网 `update_package_index()` 加整个安装
循环。结果任何 `import get_models` 都会触发"检查并安装模型"，每次启动
程序都出现：

    已有 translate-en_zh-1_9.argosmodel
    安装 en->zh OK
    ...

而 live_subtitle_v2.py 里当初 import 它只是为了拿一个模型尺寸数字，
那个函数当时还不存在 —— 纯属白跑一趟联网。

用法：
    直接运行本文件 → 检查并安装缺失的 argos 模型
    import 本模块   → 什么都不做，只用 model_dir_mb()
"""
import glob
import os
import ssl
import sys
import time
import urllib.request

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")

WANT = [("en", "zh"), ("en", "ja"), ("ja", "zh"), ("ko", "zh")]

#: 找不到缓存时的保守尺寸（MB）。放置预算宁可高估，不要低估。
KNOWN_MB = {"tiny": 80, "base": 150, "small": 500, "medium": 1500,
            "large-v3": 3100, "large": 3100}


def model_dir_mb(model_name: str) -> float:
    """这个 whisper 模型在磁盘上占多少 MB。

    设备放置需要"识别模型多大"才能算显存放不放得下。原先没有这个函数，
    调用方 import 本模块去拿 —— 于是顺带触发了整段安装流程。
    现在：先查 HuggingFace 缓存，查不到再按 KNOWN_MB 给保守值。
    """
    base = os.path.expanduser("~/.cache/huggingface/hub")
    total = 0
    for pat in (f"models--*--faster-whisper-{model_name}",
                f"models--*--whisper-{model_name}"):
        for d in glob.glob(os.path.join(base, pat)):
            for root, _dirs, files in os.walk(d):
                for f in files:
                    if os.path.splitext(f)[1].lower() in (
                            ".bin", ".gguf", ".safetensors", ".pt"):
                        try:
                            total += os.path.getsize(os.path.join(root, f))
                        except OSError:
                            pass
    if total:
        return total / 2 ** 20
    return float(KNOWN_MB.get(model_name, 1500))


def fetch(fc, tc, out_dir=OUT, quiet=False):
    """下载并安装一对语言模型。返回 True/False。"""
    try:
        import argostranslate.package as P
    except ImportError:
        if not quiet:
            print("  没装 argostranslate，跳过")
        return False
    if not quiet:
        P.update_package_index()
    pk = [p for p in P.get_available_packages()
          if p.from_code == fc and p.to_code == tc]
    if not pk:
        if not quiet:
            print(f"  {fc}->{tc} 索引里没有")
        return False
    url = pk[0].links[0]
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, os.path.basename(url))
    if not os.path.exists(dest) or os.path.getsize(dest) < 1000:
        print(f"  下载 {fc}->{tc} …")
        t0 = time.time()
        r = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(r, timeout=300, context=CTX) as resp, \
                open(dest, "wb") as f:
            total = int(resp.headers.get("Content-Length") or 0)
            got = 0
            while True:
                b = resp.read(1 << 20)
                if not b:
                    break
                f.write(b)
                got += len(b)
                if total:
                    print(f"\r  {got/1e6:6.1f}/{total/1e6:.1f} MB"
                          f"  {got/total*100:5.1f}%", end="", flush=True)
        print(f"\n  下载完成 {os.path.getsize(dest)/1e6:.1f} MB"
              f"  {time.time()-t0:.0f}s")
    else:
        print(f"  已有 {os.path.basename(dest)}")
    try:
        P.install_from_path(dest)
        print(f"  安装 {fc}->{tc} OK")
        return True
    except Exception as e:
        print(f"  安装失败: {type(e).__name__} {str(e)[:100]}")
        return False


def main():
    """直接运行本文件时：检查并安装缺失的 argos 模型。"""
    try:
        import argostranslate.translate as T
    except ImportError:
        print("  没装 argostranslate：pip install argostranslate")
        return 1
    print("=== 检查 argos 翻译模型 ===")
    for fc, tc in WANT:
        fetch(fc, tc)
    print()
    langs = T.get_installed_languages()
    print("已装语言:", [l.code for l in langs])
    for l in langs:
        for t in l.translations_from:
            print(f"  {l.code} -> {t.to_lang.code}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
