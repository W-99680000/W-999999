# -*- coding: utf-8 -*-
"""tools/check_console_html.py —— 新消费级界面（console.html）静态体检。

检查：
  · 文件存在/大小合理/UTF-8
  · 五个导航页 + 关键 API 调用（engine/overlay/live/admin-config）
  · 经典后台入口 /admin
  · 内联 JS 语法（node --check）
  · 不引用外部网络资源（本地运行、离线可用）
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILE = os.path.join(HERE, "babel_admin", "console.html")

n_ok = n_bad = 0


def chk(name, ok, detail=""):
    global n_ok, n_bad
    if ok:
        n_ok += 1
        print(f"  ok   {name}")
    else:
        n_bad += 1
        print(f"  FAIL {name}  {detail}")


def main() -> int:
    print("=" * 56)
    print("新界面体检：babel_admin/console.html")
    if not os.path.isfile(FILE):
        print("  FAIL 文件不存在:", FILE)
        return 1
    s = open(FILE, encoding="utf-8").read()
    chk("文件存在且非空", len(s) > 5000, len(s))
    chk("UTF-8 中文标题", "双语字幕" in s)
    for page, label in (("home", "工作台"), ("live", "直播翻译"),
                        ("video", "视频翻译"), ("overlay", "浮窗"),
                        ("settings", "设置")):
        chk(f"导航页 {label}（{page}）",
            f'data-page="{page}"' in s, page)
    for api in ("/api/engine", "/api/overlay", "/api/live",
                "/api/admin-config"):
        chk(f"调用 {api}", api in s, api)
    chk("经典后台入口 /admin", 'href="/admin"' in s)
    chk("字幕排版方案（标准/双语/小说/极简）",
        s.count('data-ov-preset=') >= 4 and 'OV_PRESETS' in s)
    chk("模型页含管理/仓库子标签",
        'data-mtab="models"' in s and 'data-mtab="repo"' in s)
    chk("完整浮窗参数入口", 'data-goto="ovadv"' in s)
    chk("接口密钥入口", 'data-goto="keys"' in s
        and 'id="keyDashInput"' in s and 'id="keyHfInput"' in s)
    chk("高级设置入口", 'data-goto="cfgadv"' in s)
    chk("高级页内嵌经典后台（embed=1&view=）",
        "embed=1&view=" in s and 'class="advframe"' in s)
    _no_src = not re.search(r'src="https?://', s)
    _links = re.findall(r'href="(https?://[^"]+)"', s)
    _ok_links = all(("bailian.console.aliyun.com" in u
                     or "huggingface.co" in u) for u in _links)
    chk("无外部资源加载；外链仅申请页", _no_src and _ok_links, _links)

    scripts = re.findall(r"<script>(.*?)</script>", s, re.S)
    chk("有内联脚本", len(scripts) >= 1, len(scripts))
    for i, js in enumerate(scripts):
        tmp = os.path.join(HERE, "work", f"_console_check_{i}.js")
        try:
            os.makedirs(os.path.dirname(tmp), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(js)
            r = subprocess.run(["node", "--check", tmp],
                               capture_output=True, text=True, timeout=60)
            chk(f"JS 语法 #{i + 1}", r.returncode == 0,
                (r.stderr or "")[:200])
        except FileNotFoundError:
            print("  （跳过 JS 语法检查：未找到 node）")
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass

    print(f"\n界面体检：{n_ok} 项通过，{n_bad} 项失败")
    return 0 if n_bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
