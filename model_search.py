# -*- coding: utf-8 -*-
"""model_search.py —— 模型库检索：本地目录过滤 + 在线发现新模型

为什么单独一个模块
==================
`model_registry.py` 的设计前提是"**import 零副作用、不联网、不加载模型**"
—— 界面要秒开，不能被网络拖住。而"搜索各种新型本地小模型"必须联网。
两者放一起会破坏那个前提，所以本模块是**只读的检索引擎**：

    本地：注册表 asr/mt 池 + catalog 目录（含 VAD/分离/对齐/说话人部件）
    在线：HuggingFace 模型 API（无需 key），按任务/语言/更新时间过滤

它不写注册表 —— 搜到合适的模型，把元数据补进 catalog、把后端接线
是两步人工决策（写进 JSON 只是最后一步）。这里只负责"让你找到"。

用法
====
    python model_search.py                     # 全库概览（按任务计数）
    python model_search.py vi                  # 关键词（vi/越南语都能搜）
    python model_search.py --task asr --lang vi
    python model_search.py --task asr --max-mb 2000 --status catalog
    python model_search.py --online "vietnamese speech recognition"
    python model_search.py --online --task mt --lang vi --limit 30
    python model_search.py --json | ...

在线搜索说明
============
· HuggingFace API 不需要 token；国内网络默认走 huggingface.co，
  可用环境变量 HF_ENDPOINT=https://hf-mirror.com 换镜像（代码里
  失败会**自动**回退到镜像再试一次）。
· 在线结果的"本地已收录"标记按 repo id 比对 catalog —— 避免
  "搜到热门模型但其实是自己早就在用的那个"。
· 不返回文件大小（HF 列表接口不给，要逐个查 blobs 才有）——
  这是刻意的：为了一个数字发 20 个请求不值得。
"""
from __future__ import annotations
import model_home  # noqa: E402,F401  # 先把模型目录钉到 D 盘（务必在 HF/faster-whisper 之前）

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

#: 任务名 → 中文（与 catalog 的 task 字段一致）
TASKS = {
    "asr": "语音识别",
    "mt": "机器翻译",
    "vad": "语音活动检测",
    "diarization": "说话人分离",
    "align": "强制对齐",
    "separation": "人声分离",
    "streaming": "流式识别",
}

#: 任务 → HuggingFace pipeline_tag / 特征 tag（在线过滤用）
HF_TASK_FILTER = {
    "asr": {"kind": "pipeline", "value": "automatic-speech-recognition"},
    "mt": {"kind": "pipeline", "value": "translation"},
    "vad": {"kind": "tag", "value": "voice-activity-detection"},
    "diarization": {"kind": "tag", "value": "speaker-diarization"},
    "align": {"kind": "tag", "value": "forced-alignment"},
    "separation": {"kind": "tag", "value": "audio-source-separation"},
    "streaming": {"kind": "tag", "value": "streaming"},
}


# ── 本地目录 ───────────────────────────────────────────────

def _norm_pool_entry(x: dict, task: str) -> dict:
    """把池条目（asr/mt）规整成 catalog 的形状。

    池的字段是"运行口径"（size_key/vram_mb/cli），catalog 是"检索口径"
    （task/params/repo/license）—— 搜索要按后者过滤，所以补齐缺省值，
    不让前端/调用方对着两种形状写两套判断。
    """
    y = dict(x)
    y.setdefault("task", task)
    y.setdefault("status", "active")
    y.setdefault("params", x.get("size_key") or "")
    y.setdefault("size_mb", x.get("vram_mb") or 0)
    y.setdefault("license", "")
    y.setdefault("released", "")
    y.setdefault("repo", "")
    y.setdefault("tags", [])
    y.setdefault("wired", bool(x.get("wired")))
    return y


def load_catalog() -> list[dict]:
    """合并 asr/mt 池 + catalog 目录（同 id 时池优先）。"""
    import model_registry as R
    d = R.load()
    out: list[dict] = []
    seen: set[str] = set()
    for x in d.get("asr") or []:
        e = _norm_pool_entry(x, "asr")
        out.append(e)
        seen.add(str(e.get("id")))
    for x in d.get("mt") or []:
        e = _norm_pool_entry(x, "mt")
        out.append(e)
        seen.add(str(e.get("id")))
    for x in d.get("catalog") or []:
        if str(x.get("id")) in seen:
            continue
        out.append(dict(x))
        seen.add(str(x.get("id")))
    # wired 的唯一口径是 model_registry.WIRED（JSON 里的 wired 是探测时
    # 写进去的快照，重建后可能缺失 —— 读它会出现"接线 1 / 观察 38"这种
    # 和 probe 输出矛盾的计数）。
    for e in out:
        e["wired"] = bool(R.WIRED.get(str(e.get("id")), False))
        e.setdefault("status", "active" if e["wired"] else "catalog")
    return out


def _haystack(e: dict) -> str:
    parts = [str(e.get("id", "")), str(e.get("name", "")),
             str(e.get("vendor", "")), str(e.get("kind", "")),
             str(e.get("repo", "")), str(e.get("note", "")),
             " ".join(str(t) for t in (e.get("tags") or [])),
             " ".join(str(t) for t in (e.get("langs") or []))]
    return " ".join(parts).lower()


#: 中文关键词 → 检索同义词（用户搜"越南语"要能命中 langs 里的 vi）
_SYNONYMS = {
    "越南语": "vi vietnamese", "越南": "vi vietnamese",
    "中文": "zh chinese", "汉语": "zh chinese", "普通话": "zh chinese",
    "粤语": "yue cantonese", "日语": "ja japanese", "韩语": "ko korean",
    "泰语": "th thai", "英语": "en english", "俄语": "ru russian",
    "识别": "asr recognition", "转写": "asr transcribe",
    "翻译": "mt translation", "断句": "vad alignment",
    "人声分离": "separation demucs", "说话人": "diarization speaker",
    "流式": "streaming", "对齐": "align alignment",
}


def _query_groups(query: str) -> list[set[str]]:
    """查询词 → 若干**同义词组**：组内 OR、组间 AND。

    为什么不是"所有词都必须命中"：中文按词搜时，'越南语' 展开成
    {越南语, vi, vietnamese}，而 Dolphin 的 tags 只有英文、note 只有
    中文 —— 要求三个同时命中会把正确的条目筛掉。组内 OR 才是
    "同义词"该有的语义。
    """
    groups: list[set[str]] = []
    for tok in query.lower().replace(",", " ").split():
        alts = {tok}
        for k, v in _SYNONYMS.items():
            if k == tok or (len(k) >= 2 and k in query and tok == k):
                alts.update(v.split())
        groups.append(alts)
    # 整句中文（没有空格）：包含某个键就并进该组
    if len(groups) == 1 and " " not in query:
        for k, v in _SYNONYMS.items():
            if k in query:
                groups[0].update(v.split())
    return groups


def _expand_query(query: str) -> list[str]:
    """扁平化（保留给 --json / 外部调用看的调试形态）。"""
    out: list[str] = []
    for g in _query_groups(query):
        out.extend(sorted(g))
    return list(dict.fromkeys(out))


def search(query: str | None = None, task: str | None = None,
           lang: str | None = None, max_mb: float | None = None,
           status: str | None = None, wired: bool | None = None,
           source: str | None = None,
           entries: list[dict] | None = None) -> list[dict]:
    """本地检索。所有条件都是 AND；query 的各个词也要全部命中。"""
    rows = entries if entries is not None else load_catalog()
    groups = _query_groups(query) if query else []
    out = []
    for e in rows:
        if task and e.get("task") != task:
            continue
        if status and e.get("status") != status:
            continue
        if wired is not None and bool(e.get("wired")) != wired:
            continue
        if source and e.get("source") != source:
            continue
        if lang:
            ls = [str(x).lower() for x in (e.get("langs") or [])]
            # "*" = 全语种；"en→zh" 这种语言对按源侧匹配
            hit = any(l == "*" or l == lang.lower()
                      or l.split("→")[0] == lang.lower() for l in ls)
            if not hit:
                continue
        if max_mb is not None and float(e.get("size_mb") or 0) > max_mb:
            continue
        if groups:
            h = _haystack(e)
            if not all(any(a in h for a in g) for g in groups):
                continue
        out.append(e)
    return out


# ── 在线发现（HuggingFace）────────────────────────────────

def _hf_endpoints() -> list[str]:
    """端点顺序：显式 HF_ENDPOINT → 官方 → 国内镜像。"""
    eps = []
    env = os.environ.get("HF_ENDPOINT", "").strip().rstrip("/")
    if env:
        eps.append(env)
    for e in ("https://huggingface.co", "https://hf-mirror.com"):
        if e not in eps:
            eps.append(e)
    return eps


def _http_json(url: str, timeout: float = 15.0):
    req = urllib.request.Request(
        url, headers={"User-Agent": "live-subtitle-model-search/1.0",
                      "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def search_online(query: str = "", task: str = "asr", lang: str | None = None,
                  limit: int = 20,
                  known_repos: set[str] | None = None) -> tuple[list[dict], str]:
    """在线搜索 HuggingFace；返回 (结果, 实际用的端点)。

    网络失败会按 `_hf_endpoints()` 顺序逐个试；全挂则抛 RuntimeError ——
    不静默返回空列表（"搜不到"和"网断了"必须能区分）。
    """
    params = {"search": query or "", "limit": 100,
              "sort": "downloads", "direction": -1}
    flt = HF_TASK_FILTER.get(task)
    if lang:
        params["filter"] = f"language:{lang}"
    qs = urllib.parse.urlencode(params)

    data = None
    used = ""
    errs = []
    for ep in _hf_endpoints():
        try:
            data = _http_json(f"{ep}/api/models?{qs}")
            used = ep
            break
        except Exception as e:
            errs.append(f"{ep}: {type(e).__name__} {e}")
    if data is None:
        raise RuntimeError("在线搜索失败（端点全不可达）：\n  "
                           + "\n  ".join(errs))

    known = {r.lower() for r in (known_repos or set()) if r}
    out = []
    for m in data:
        pid = m.get("modelId") or m.get("id") or ""
        if not pid:
            continue
        tags = m.get("tags") or []
        pt = m.get("pipeline_tag") or ""
        if flt:
            if flt["kind"] == "pipeline" and pt != flt["value"]:
                continue
            if flt["kind"] == "tag" and flt["value"] not in tags:
                continue
        if lang and f"language:{lang}" not in tags:
            # 有的仓库不打 language tag —— 退回 id/名称匹配，但不放进
            # "严格语言命中"里；这里选择信任服务端 filter，再兜一道
            low = pid.lower()
            name_hint = {"vi": ("viet", "vi-"), "zh": ("chinese", "zh-", "yue"),
                         "ja": ("japan", "ja-"), "ko": ("korean", "ko-")}
            if not any(h in low for h in name_hint.get(lang, ())):
                continue
        out.append({
            "id": pid,
            "downloads": int(m.get("downloads") or 0),
            "likes": int(m.get("likes") or 0),
            "pipeline": pt,
            "created": (m.get("createdAt") or "")[:10],
            "tags": [t for t in tags
                     if not t.startswith(("arxiv:", "doi:", "region:"))][:8],
            "known": pid.lower() in known,
        })
        if len(out) >= limit:
            break
    return out, used


# ── 安装 / 自检 ────────────────────────────────────────────

def install(model_id: str) -> str:
    """把目录里的模型下载到 models/<名字>（ModelScope 优先，HF 兜底）。

    "搜得到"和"装得上"是同一件事的两半：只给 repo id 让人自己敲命令，
    搜索就只是个花名册。qwen3-asr 的落点必须与后端约定一致
    （models/Qwen3-ASR-0.6B），否则装完 load_asr 还是找不到。
    """
    import model_registry as R
    entries = {e["id"]: e for e in load_catalog()}
    e = R.get(model_id) or entries.get(model_id)
    if not e:
        raise SystemExit(f"目录里没有 {model_id!r}（先 python model_search.py <关键词> 找）")
    repo = str(e.get("repo") or "").strip()
    if not repo:
        raise SystemExit(f"{model_id} 没有 repo 字段（远端/离线端点不是可下载模型）")

    # 落点：qwen3-asr 走后端约定目录；其余用模型名（去掉空格/斜杠）
    name = str(e.get("name") or model_id.split(":", 1)[-1])
    if "qwen3-asr" in model_id.lower():
        from qwen_asr_backend import MODEL_DIRS
        size = "1.7b" if "1.7" in model_id else "0.6b"
        local = MODEL_DIRS[f"qwen3-asr-{size}"]
    else:
        local = "".join(c if c.isalnum() or c in "-_." else "_"
                        for c in name)
    dest = os.path.join(HERE, "models", local)
    print(f"下载 {repo}\n  → {dest}")
    try:
        from modelscope import snapshot_download
        p = snapshot_download(repo, local_dir=dest)
        print(f"完成（ModelScope）：{p}")
        return str(p)
    except Exception as ex:
        print(f"ModelScope 失败（{type(ex).__name__}: {ex}），试 HuggingFace…")
    try:
        from huggingface_hub import snapshot_download
        p = snapshot_download(repo, local_dir=dest)
        print(f"完成（HuggingFace）：{p}")
        return str(p)
    except Exception as ex:
        raise SystemExit(f"下载失败：{type(ex).__name__}: {ex}")


def check() -> int:
    """自检：catalog 完整性 + 检索/过滤真的生效（不联网、不加载模型）。"""
    ok = bad = 0

    def chk(name, cond, extra=""):
        nonlocal ok, bad
        if cond:
            ok += 1
        else:
            bad += 1
            print(f"  ✗ {name} {extra}")

    rows = load_catalog()
    chk("目录非空且 id 唯一",
        len(rows) >= 20 and len({e["id"] for e in rows}) == len(rows),
        f"{len(rows)} 项")
    chk("每条都有 task/name/langs",
        all(e.get("task") and e.get("name") and e.get("langs")
            for e in rows))
    chk("wired 与注册表一致",
        all(e["wired"] == bool(__import__("model_registry").WIRED.get(e["id"]))
            for e in rows))
    chk("任务过滤", all(x["task"] == "vad" for x in search(task="vad")))
    chk("语言过滤（vi，含语言对源侧）", all(
        any(str(l) in ("*", "vi") or str(l).split("→")[0] == "vi"
            for l in x["langs"])
        for x in search(lang="vi")))
    chk("体积过滤", all(float(x.get("size_mb") or 0) <= 500
                        for x in search(max_mb=500)))
    chk("中文关键词命中（越南语）", bool(search("越南语", task="asr")))
    # 两条入口（中文词 / 语言码）都要能落到核心模型上；**不要求结果集相等**
    # —— '越南语' 会命中备注里写"无越南语"的条目（负面提及也算命中），
    # 'vi' 会命中备注里出现 vi 的条目，两者本来就不该一样。
    _a = {x["id"] for x in search("越南语", task="asr")}
    _b = {x["id"] for x in search("vi", task="asr")}
    chk("中文词与语言码都能命中 Dolphin（vi 核心项）",
        "local:dolphin-small" in _a and "local:dolphin-small" in _b,
        f"{sorted(_a)} / {sorted(_b)}")
    chk("已接线过滤非空", len(search(wired=True)) >= 10,
        str(len(search(wired=True))))
    print(f"model_search 自检：{ok} 项通过，{bad} 项失败")
    return 1 if bad else 0


# ── 输出 ───────────────────────────────────────────────────

def _stars(e: dict) -> str:
    if e.get("wired"):
        return "√接线"
    if e.get("status") == "catalog":
        return "·观察"
    return "—"


def _dw(s: str) -> int:
    """终端显示宽度（CJK 算 2）。不这么做，中文列会把表挤成锯齿。"""
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1
               for c in str(s))


def _pad(s, n: int, right: bool = False) -> str:
    s = str(s)
    gap = max(0, n - _dw(s))
    return (" " * gap + s) if right else (s + " " * gap)


def _cut(s, n: int) -> str:
    """按显示宽度截断（不切坏中文）。"""
    s = str(s or "")
    out, w = "", 0
    for c in s:
        cw = 2 if _dw(c) == 2 else 1
        if w + cw > n:
            return out
        out += c
        w += cw
    return out


def print_local(rows: list[dict]) -> None:
    if not rows:
        print("（没有匹配项。去掉一些条件，或用 --online 在线搜）")
        return
    print(_pad("接", 6) + _pad("任务", 7) + _pad("规格", 9)
          + _pad("大小", 8, True) + "  " + _pad("语言", 20)
          + _pad("许可", 14) + "名称")
    print("-" * 112)
    for e in rows:
        langs = _cut(",".join(str(x) for x in (e.get("langs") or [])), 19)
        size = float(e.get("size_mb") or 0)
        size_s = (f"{size / 1024:.1f}G" if size >= 1024
                  else f"{size:.0f}M" if size else "—")
        print(_pad(_stars(e), 6) + _pad(e.get("task", ""), 7)
              + _pad(_cut(e.get("params") or "—", 8), 9)
              + _pad(size_s, 8, True) + "  " + _pad(langs, 20)
              + _pad(_cut(e.get("license") or "—", 13), 14)
              + str(e.get("name")))
        line = "      " + str(e.get("id")) + "  ·  " + str(e.get("vendor", ""))
        if e.get("repo"):
            line += "  ·  " + str(e["repo"])
        print(line)
        if e.get("released"):
            print(f"      发布 {e['released']}", end="")
            print(f"　{e['note']}" if e.get("note") else "")
        elif e.get("note"):
            print(f"      {e['note']}")
    print(f"\n共 {len(rows)} 项")


def print_online(rows: list[dict], endpoint: str) -> None:
    print(f"在线结果（{endpoint}，按下载量排序）：\n")
    if not rows:
        print("（没有匹配项 —— 换个关键词，或去掉 --lang/--task 放宽）")
        return
    for r in rows:
        mark = "  ★已收录" if r["known"] else ""
        print(f"  {r['id']}{mark}")
        print(f"    ↓{r['downloads']:,}  ♥{r['likes']}  "
              f"{r['pipeline'] or '—'}  {r['created']}  "
              f"{' '.join(r['tags'][:5])}")
    print("\n下载：modelscope download --model <id>   "
          "或  huggingface-cli download <id> --local-dir models/<名字>")
    print("接线：把元数据补进 model_registry.json 的 catalog，"
          "再给 kind 加后端分支（见 qwen_asr_backend.py）")


def print_overview(rows: list[dict]) -> None:
    from collections import Counter
    c = Counter(e.get("task") for e in rows)
    wired = sum(1 for e in rows if e.get("wired"))
    print(f"模型库共 {len(rows)} 项（接线 {wired} / 观察 {len(rows) - wired}）：")
    for t, n in sorted(c.items(), key=lambda kv: -kv[1]):
        print(f"  {t:<12}{TASKS.get(t, ''):<10}{n:>3} 项")
    print("\n按任务查：python model_search.py --task asr")
    print("按语言查：python model_search.py --lang vi")
    print("在线找新：python model_search.py --online \"vietnamese asr\"")


# ── CLI ────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if args and args[0] == "check":
        return check()
    if args and args[0] == "install":
        if len(args) < 2:
            print("用法: python model_search.py install <模型 id>")
            return 2
        try:
            install(args[1])
        except SystemExit as e:
            print(e)
            return 1
        return 0

    ap = argparse.ArgumentParser(
        description="模型库检索：本地目录过滤 + 在线发现新模型"
                    "（子命令：check / install <id>）")
    ap.add_argument("query", nargs="*", help="关键词（中英文都行）")
    ap.add_argument("--task", choices=sorted(TASKS), help="任务类型")
    ap.add_argument("--lang", help="语言码（vi/zh/ja/ko/yue…）")
    ap.add_argument("--max-mb", type=float, default=None,
                    help="体积上限（MB，只对本地目录有效）")
    ap.add_argument("--status", choices=("active", "catalog"),
                    help="active=能直接选；catalog=观察项")
    ap.add_argument("--wired", action="store_true", help="只看已接线")
    ap.add_argument("--unwired", action="store_true", help="只看未接线")
    ap.add_argument("--source", help="local / remote / offline")
    ap.add_argument("--online", action="store_true",
                    help="在线搜 HuggingFace（发现新模型）")
    ap.add_argument("--limit", type=int, default=20, help="在线结果条数上限")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    a = ap.parse_args(argv)

    q = " ".join(a.query).strip() or None

    if a.online:
        task = a.task or "asr"
        cat = load_catalog()
        known = {str(e.get("repo")).lower() for e in cat if e.get("repo")}
        try:
            rows, ep = search_online(q or "", task=task, lang=a.lang,
                                     limit=a.limit, known_repos=known)
        except Exception as e:
            print(f"！{e}")
            return 1
        if a.json:
            print(json.dumps(rows, ensure_ascii=False, indent=1))
        else:
            print_online(rows, ep)
        return 0

    wired = True if a.wired else (False if a.unwired else None)
    rows = search(q, task=a.task, lang=a.lang, max_mb=a.max_mb,
                  status=a.status, wired=wired, source=a.source)
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    elif not (q or a.task or a.lang or a.max_mb or a.status
              or wired is not None or a.source):
        print_overview(rows)
    else:
        print_local(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
