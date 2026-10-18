# -*- coding: utf-8 -*-
"""tools/rewrite_srt_layout.py —— 把已有双语 SRT 重排成短 cue（可逆，自动备份）。

用途：新生成的 pretranslate 输出已经写盘前切短；旧缓存/旧 srt 仍是
8~16s、50 字一条，外部播放器会硬折行断字。本工具按 subtitle_layout
把它们切成 2~5.5s、每行约 24 字的 cue，原文件备份为 .bak。

用法：
    python tools/rewrite_srt_layout.py "pre_cache/xxx.zh.srt"
    python tools/rewrite_srt_layout.py "pre_cache/*.zh.srt"   # 支持通配
"""
from __future__ import annotations

import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from subtitle_layout import split_cue  # noqa: E402

_TS_RE = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*"
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})")


def _sec(g) -> float:
    return (int(g[0]) * 3600 + int(g[1]) * 60 + int(g[2])
            + int(g[3].ljust(3, "0")) / 1000.0)


def parse(path):
    s = open(path, encoding="utf-8-sig", errors="replace").read()
    out = []
    for block in re.split(r"\r?\n\s*\r?\n", s.strip()):
        lines = [x.strip() for x in block.strip().splitlines() if x.strip()]
        if len(lines) < 3:
            continue
        m = _TS_RE.search(lines[1]) or _TS_RE.search(lines[0])
        if not m:
            continue
        body = lines[2:] if _TS_RE.search(lines[1]) else lines[1:]
        zh = body[0] if len(body) >= 2 else ""
        src = body[1] if len(body) >= 2 else body[0]
        out.append((_sec(m.groups()[:4]), _sec(m.groups()[4:]),
                    zh, src))
    return out


def _ts(t):
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    ms = int(round((s - int(s)) * 1000))
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{ms:03d}"


def rewrite(path):
    cues = parse(path)
    if not cues:
        print(f"跳过（没有字幕）：{path}")
        return 0
    rows = []
    for st, en, zh, src in cues:
        for a, b, z, s in split_cue(st, en, zh, src):
            if z or s:
                rows.append((a, b, z.strip(), s.strip()))
    tmp = path + ".layout.tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="\n") as f:
        for i, (a, b, z, s) in enumerate(rows, 1):
            f.write(f"{i}\n{_ts(a)} --> {_ts(b)}\n")
            if z:
                f.write(z + "\n")
            f.write(s + "\n\n")
    bak = path + ".bak"
    if not os.path.exists(bak):
        os.replace(path, bak)
    else:
        os.remove(path)
    os.replace(tmp, path)
    print(f"{os.path.basename(path)}: {len(cues)} → {len(rows)} 条"
          f"（备份 {os.path.basename(bak)}）")
    return len(rows)


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    files = []
    for a in args:
        g = glob.glob(a)
        files.extend(g or [a])
    n = 0
    for p in files:
        if os.path.isfile(p):
            n += rewrite(p)
    print(f"合计写入 {n} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
