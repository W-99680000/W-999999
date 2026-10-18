# -*- coding: utf-8 -*-
"""tools/check_admin_html.py —— 后台界面 HTML 的结构体检（不依赖浏览器）

为什么单独一个检查：`babel_admin/index.html` 是**双击即用**的交付物，
它一旦有个没闭合的 <style> 或一条外链，表现是"页面白屏"或"断网就废"，
而这两种问题在代码审阅里都看不出来。所以用正则做最低限度但确定的检查：

  ① 标签配平（html/head/body/style/script/aside/main/nav/header）
  ② 零外链（无 http(s):// 资源、无 //cdn、无 @import）—— 断网也能开
  ③ 关键文案在（巴别塔计划 / 直播翻译和视频翻译 / 两个模式 / 模型管理 / 设置）
  ④ 内联数据契约存在（BABEL_MODELS 或 BABEL_REGISTRY 注入点）
  ⑤ <script> 里的 JS 用 node --check 做语法校验（能抓到拼错导致的整页失效）
  ⑥ 文件大小下限（防止交付了一个被截断的半个文件）

用法: python tools/check_admin_html.py [路径]
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT = os.path.join(ROOT, "babel_admin", "index.html")

OK = BAD = 0


def chk(name: str, cond: bool, extra: str = "") -> None:
    global OK, BAD
    if cond:
        OK += 1
        print(f"  ok   {name}")
    else:
        BAD += 1
        print(f"  FAIL {name}   {extra}")


def main(argv=None) -> int:
    path = (argv or sys.argv[1:] or [DEFAULT])[0]
    if not os.path.isfile(path):
        print(f"找不到 {path}")
        return 2
    s = open(path, encoding="utf-8").read()
    print(f"检查 {path}")
    print(f"  大小 {len(s)} 字节 / {s.count(chr(10)) + 1} 行")

    chk("文件大小 > 25KB（没被截断）", len(s) > 25_000, f"{len(s)}")

    print("一、标签配平")
    for tag in ("html", "head", "body", "style", "script", "aside", "main",
                "nav", "header", "section"):
        o = len(re.findall(r"<" + tag + r"[\s>]", s))
        c = len(re.findall(r"</" + tag + r">", s))
        if o == 0 and c == 0:
            continue
        chk(f"<{tag}> {o}/{c}", o == c, f"开 {o} 闭 {c}")

    print("二、零外链（断网可用）")
    refs = re.findall(r'(?:src|href)\s*=\s*["\']([^"\']+)["\']', s)
    remote = [u for u in refs
              if u.startswith(("http://", "https://", "//")) or "cdn" in u.lower()]
    chk("没有远端资源引用", not remote, str(remote[:5]))
    chk("没有 @import 远端样式", "@import" not in s)
    chk("没有 fetch 远端域名",
        not re.search(r"fetch\(\s*['\"]https?://", s))

    print("三、关键文案")
    for word in ("巴别塔计划", "直播翻译和视频翻译", "直播翻译",
                 "视频翻译", "同传记录", "模型管理", "设置"):
        chk(f"含「{word}」", word in s)

    print("四、数据契约注入点")
    has_contract = ("BABEL_MODELS" in s) or ("BABEL_REGISTRY" in s)
    chk("有模型数据常量（BABEL_MODELS / BABEL_REGISTRY）", has_contract)
    chk("模型数据里有识别与翻译两类",
        bool(re.search(r"\basr\s*:", s)) and bool(re.search(r"\bmt\s*:", s)))
    chk("有模式绑定（loopback / cache）", "loopback" in s and "cache" in s)
    chk("引用了 /api/models 或 model_registry_web.json（可接后端）",
        ("/api/models" in s) or ("model_registry_web.json" in s))

    chk("模型管理按引擎当前模式初始化", "syncModeFromEngine" in s)
    chk("应用只提交改过的槽位（不串模式）",
        "state.dirty.asr" in s and "payload.asr" in s and "payload.mt" in s)
    chk("应用后切到目标模式", "state.mode = which" in s)

    print("五、JS 语法（node --check）")
    node = None
    for cand in (r"C:\Users\w\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe",
                 "node"):
        try:
            r = subprocess.run([cand, "--version"], capture_output=True, text=True,
                               timeout=20)
            if r.returncode == 0:
                node = cand
                break
        except Exception:
            continue
    if node is None:
        print("  --   本机没有 node，跳过 JS 语法校验（未验证项）")
    else:
        blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", s,
                            re.S | re.I)
        chk("至少有一个内联 <script>", bool(blocks), str(len(blocks)))
        for i, b in enumerate(blocks):
            if not b.strip():
                continue
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                             encoding="utf-8") as f:
                f.write(b)
                tmp = f.name
            try:
                r = subprocess.run([node, "--check", tmp], capture_output=True,
                                   text=True, timeout=60)
                chk(f"第 {i + 1} 段 JS 语法通过（{len(b)} 字符）",
                    r.returncode == 0, (r.stderr or "")[:300])
            finally:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    print(f"\n界面体检：{OK} 项通过，{BAD} 项失败")
    return 1 if BAD else 0


if __name__ == "__main__":
    raise SystemExit(main())
