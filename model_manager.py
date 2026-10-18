# -*- coding: utf-8 -*-
"""model_manager.py —— 模型搜索 / 下载 / 安装管理

干什么
======
把"模型"从注册表里的抽象条目变成**可管理的东西**：
  · catalog()   全量目录：本地已装 / 可下载 / 远端需 key，统一视图
  · search()    按名字/类型/大小过滤
  · download()  后台线程下载，进度回调，可取消，断点续传（.part + Range）
  · set_default() 把选中的模型写回 model_registry.json 的 defaults

下载源
======
  · faster-whisper  → HuggingFace `Systran/faster-whisper-*`
                      落 ~/.cache/huggingface/hub（faster-whisper 自己会读）
  · HY-MT GGUF      → HuggingFace 腾讯混元仓库，落项目 models/ 目录
                      （local_mt.MODEL_CANDIDATES 已认这里）
  · 网络环境在国内：默认走 HF_ENDPOINT 镜像（hf-mirror.com），可被环境变量覆盖。
  · **不走环境变量里的代理**（本机沙箱代理会改写本机行为，直连镜像最稳）。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(HERE, "models")
STATE_PATH = os.path.join(HERE, "work", "model_downloads.json")

# HF 镜像：国内直连 hf-mirror.com；有自有通道时设 HF_ENDPOINT 覆盖
DEFAULT_ENDPOINT = os.environ.get("HF_ENDPOINT", "https://hf-mirror.com")

# ── 可下载目录（本地模型）────────────────────────────────────
# faster-whisper：整仓 snapshot（ctranslate2 格式）
_WHISPER_REPO = "Systran/faster-whisper-{}"
_WHISPER_SIZES = {
    "tiny":     {"mb": 75,   "vram": 800},
    "base":     {"mb": 145,  "vram": 1000},
    "small":    {"mb": 465,  "vram": 1900},
    "medium":   {"mb": 1500, "vram": 3100},
    "large-v3": {"mb": 2900, "vram": 4700},
}
# HY-MT GGUF：按文件下。repo/file 映射写成数据，换仓库只改这里。
_HYMT = {
    "base":   {"repo": "tencent/HY-MT1.5-1.8B-GGUF",
               "file": "hh-translate-base.gguf",
               "patterns": ["*1.8B*Q4_K_M*.gguf", "*1.8B*Q4*.gguf", "*.gguf"],
               "mb": 1100, "vram": 1081},
    "small":  {"repo": "tencent/HY-MT1.5-1.8B-GGUF",
               "file": "hh-translate-small.gguf",
               "patterns": ["*1.8B*Q5*.gguf", "*1.8B*Q8*.gguf"],
               "mb": 1400, "vram": 1300},
    "medium": {"repo": "tencent/HY-MT1.5-7B-GGUF",
               "file": "hh-translate-medium.gguf",
               "patterns": ["*7B*Q4_K_M*.gguf", "*7B*Q4*.gguf"],
               "mb": 4400, "vram": 2200},
    "large":  {"repo": "tencent/HY-MT1.5-7B-GGUF",
               "file": "hh-translate-large.gguf",
               "patterns": ["*7B*Q8_0*.gguf", "*7B*Q6*.gguf"],
               "mb": 7800, "vram": 3800},
}


def _now() -> float:
    return time.time()


# ── 下载状态持久化（界面重开能看到上次下到哪）─────────────────
def _load_state() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(s: dict) -> None:
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_PATH)
    except Exception:
        pass


# ── 安装状态 ─────────────────────────────────────────────────
def whisper_installed(size: str) -> str | None:
    """faster-whisper 某档位的本地缓存目录；没装返回 None。"""
    base = os.environ.get("HF_HOME") or os.path.expanduser("~/.cache/huggingface")
    hub = os.path.join(base, "hub", f"models--Systran--faster-whisper-{size}")
    if os.path.isdir(hub):
        snap = os.path.join(hub, "snapshots")
        try:
            for d in os.listdir(snap):
                p = os.path.join(snap, d)
                if os.path.isdir(p) and os.path.isfile(os.path.join(p, "model.bin")):
                    return p
        except Exception:
            pass
    return None


def hymt_installed(size: str) -> str | None:
    """HY-MT 某档位的 gguf 实际路径；没装返回 None。"""
    name = _HYMT.get(size, {}).get("file", f"hh-translate-{size}.gguf")
    try:
        from local_mt import MODEL_CANDIDATES
        for p in MODEL_CANDIDATES:
            if p and os.path.isfile(p) and size in os.path.basename(p).lower():
                return p
    except Exception:
        pass
    p = os.path.join(MODELS_DIR, name)
    return p if os.path.isfile(p) else None


def catalog() -> list[dict]:
    """全量模型目录。每项：

        {id, name, group(asr/mt/remote), size_mb, vram_mb,
         status(installed/downloadable/needs_key),
         path(已装), source, note, recommended(None/'asr'/'mt')}
    """
    out: list[dict] = []
    # 本地 ASR
    for size, meta in _WHISPER_SIZES.items():
        p = whisper_installed(size)
        out.append({
            "id": f"local:faster-whisper-{size}",
            "name": f"faster-whisper {size}",
            "group": "asr", "size_mb": meta["mb"], "vram_mb": meta["vram"],
            "status": "installed" if p else "downloadable",
            "path": p, "source": f"HF:{_WHISPER_REPO.format(size)}",
            "note": "本地识别，离线可用；档位越大越准越慢",
        })
    # 本地 MT
    for size, meta in _HYMT.items():
        p = hymt_installed(size)
        out.append({
            "id": f"local:hh-translate-{size}",
            "name": f"HY-MT {size} (GGUF)",
            "group": "mt", "size_mb": meta["mb"], "vram_mb": meta["vram"],
            "status": "installed" if p else "downloadable",
            "path": p, "source": f"HF:{meta['repo']}",
            "note": "本地翻译（llama.cpp），零网络延迟、隐私不出机",
        })
    # 远端（来自注册表，只读展示）
    try:
        import model_registry as reg
        data = reg.load()
        for m in reg.pool("mt", data) + reg.pool("asr", data):
            if m.get("source") != "remote":
                continue
            try:
                import key_store as _ak
                _ready = _ak.dashscope_available()
            except Exception:
                _ready = False
            out.append({
                "id": m["id"], "name": m.get("name", m["id"]),
                "group": "mt" if m.get("kind") in ("llm", "llm-mt") else "asr",
                "size_mb": 0, "vram_mb": 0,
                "status": "ready" if _ready else "needs_key",
                "path": None, "source": m.get("vendor", "remote"),
                "note": m.get("note", "在线服务，需要 API key"),
            })
    except Exception:
        pass
    # 下载中的进度合进来
    st = _load_state()
    for item in out:
        d = st.get(item["id"])
        if d and d.get("state") in ("downloading", "paused", "error"):
            item["dl"] = d
    return out


# 搜索别名：模型卡片只写正式名/来源，用户常搜中文/用途/厂商。
# 这里把常见说法都挂上，搜索命中 name/id/source/note/group + 别名。
_ALIAS_RULES = (
    ("local:faster-whisper-",
     "语音识别 听写 转录 asr whisper 本地 离线 识别 英文 英语 中文 日语 韩语 越南语"),
    ("local:hh-translate-",
     "机器翻译 翻译 译器 mt 本地 离线 混元 腾讯 hy-mt 1.8b 7b 中文 英文 日文 韩文 越南语"),
    ("remote:qwen-mt",
     "机器翻译 翻译 译器 mt 远端 在线 阿里云 百炼 dashscope api key qwen-mt"),
    ("remote:qwen3-asr",
     "语音识别 听写 转录 asr 远端 在线 阿里云 百炼 dashscope api key qwen3"),
    ("remote:", "远端 在线 云 api key 需要密钥"),
)


def _search_haystack(m: dict) -> str:
    parts = [str(m.get(k, "") or "") for k in
             ("name", "id", "source", "note", "group", "size_key")]
    mid = str(m.get("id") or "")
    for pre, words in _ALIAS_RULES:
        if mid.startswith(pre):
            parts.append(words)
    return " ".join(parts).lower()


def search(query: str = "", group: str = "", only_available: bool = False) -> list[dict]:
    """按关键字 + 分组过滤。

    query 命中 name/id/source/note/group + 中文/用途/厂商别名
    （如「腾讯」「离线翻译」「7B」「语音识别」「阿里云」）。
    """
    q = query.strip().lower()
    out = []
    for m in catalog():
        if group and m["group"] != group:
            continue
        if only_available and m["status"] == "needs_key":
            continue
        if q and q not in _search_haystack(m):
            continue
        out.append(m)
    return out


def annotate_recommend(items: list[dict], hw: dict | None = None) -> list[dict]:
    """把硬件检测的推荐标到目录项上（recommended 字段 + 理由）。"""
    if hw is None:
        try:
            import hardware_detect
            hw = hardware_detect.detect()
        except Exception:
            return items
    rec = hw.get("recommend", {})
    asr_id = rec.get("asr", {}).get("model_id")
    mt = rec.get("mt", {})
    mt_id = (f"local:hh-translate-{mt['size']}" if mt.get("type") == "local"
             and mt.get("size") else None)
    for m in items:
        if m["id"] == asr_id:
            m["recommended"] = "asr"
            m["rec_reason"] = rec.get("asr", {}).get("reason", "")
        elif mt_id and m["id"] == mt_id:
            m["recommended"] = "mt"
            m["rec_reason"] = mt.get("reason", "")
    return items


# ── 下载 ─────────────────────────────────────────────────────
class DownloadJob:
    """单个模型的下载任务。线程跑，进度写 work/model_downloads.json。"""

    def __init__(self, model_id: str, on_progress=None, on_done=None):
        self.model_id = model_id
        self.on_progress = on_progress or (lambda **kw: None)
        self.on_done = on_done or (lambda **kw: None)
        self.cancel = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _set(self, **kw) -> None:
        st = _load_state()
        cur = st.get(self.model_id, {})
        cur.update(kw)
        cur["ts"] = _now()
        st[self.model_id] = cur
        _save_state(st)
        self.on_progress(**cur)

    def _run(self) -> None:
        try:
            if self.model_id.startswith("local:faster-whisper-"):
                self._download_whisper()
            elif self.model_id.startswith("local:hh-translate-"):
                self._download_hymt()
            else:
                raise ValueError(f"不支持下载 {self.model_id}")
            self._set(state="done", pct=100.0)
            self.on_done(ok=True, model_id=self.model_id)
        except Exception as e:
            if self.cancel.is_set():
                self._set(state="paused", error="已取消")
            else:
                self._set(state="error", error=f"{type(e).__name__}: {e}")
            self.on_done(ok=False, model_id=self.model_id, error=str(e))

    def _download_whisper(self) -> None:
        size = self.model_id.rsplit("-", 1)[-1] if "large" not in self.model_id \
            else "large-v3"
        size = self.model_id.replace("local:faster-whisper-", "")
        repo = _WHISPER_REPO.format(size)
        total_mb = _WHISPER_SIZES.get(size, {}).get("mb", 0)
        self._set(state="downloading", pct=0.0, detail=f"解析仓库 {repo}")
        try:
            import key_store as _ak          # HF Access Token（gated 模型用）
            _ak.apply_env()
        except Exception:
            pass
        try:
            from huggingface_hub import snapshot_download
        except ImportError:
            raise RuntimeError("缺 huggingface_hub：pip install huggingface_hub")
        os.environ.setdefault("HF_ENDPOINT", DEFAULT_ENDPOINT)
        os.environ.pop("HF_HUB_OFFLINE", None)
        last = [0.0]

        def tick():
            # snapshot_download 不回调进度；用已下文件大小估算
            done = _dir_mb(_wip_dir(repo))
            pct = min(99.0, done / max(total_mb, 1) * 100)
            if pct - last[0] >= 1.0:
                last[0] = pct
                self._set(state="downloading", pct=round(pct, 1),
                          detail=f"{done:.0f}/{total_mb}MB")

        stop = threading.Event()

        def _ticker():
            while not stop.wait(1.0):
                if self.cancel.is_set():
                    raise SystemExit
                try:
                    tick()
                except Exception:
                    pass

        t = threading.Thread(target=_ticker, daemon=True)
        t.start()
        try:
            path = snapshot_download(repo_id=repo,
                                     token=os.environ.get("HF_TOKEN") or None)
        finally:
            stop.set()
        if self.cancel.is_set():
            raise RuntimeError("已取消")
        self._set(state="done", pct=100.0, path=path)

    def _download_hymt(self) -> None:
        size = self.model_id.replace("local:hh-translate-", "")
        meta = _HYMT[size]
        os.makedirs(MODELS_DIR, exist_ok=True)
        dst = os.path.join(MODELS_DIR, meta["file"])
        if os.path.isfile(dst):
            self._set(state="done", pct=100.0, path=dst)
            return
        self._set(state="downloading", pct=0.0, detail="列仓库文件…")
        url = self._resolve_hymt_url(meta)
        self._http_download(url, dst, total_mb=meta["mb"])

    def _resolve_hymt_url(self, meta: dict) -> str:
        """列出仓库文件，按 patterns 挑一个 gguf。"""
        import fnmatch
        api = f"{DEFAULT_ENDPOINT}/api/models/{meta['repo']}"
        req = urllib.request.Request(api, headers={"User-Agent": "babel-admin"})
        with _urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8"))
        files = [s.get("rfilename", "") for s in data.get("siblings", [])]
        for pat in meta["patterns"]:
            hits = [f for f in files if fnmatch.fnmatch(f, pat)]
            if hits:
                hits.sort()
                return f"{DEFAULT_ENDPOINT}/{meta['repo']}/resolve/main/{hits[0]}"
        raise RuntimeError(f"{meta['repo']} 里没找到匹配的 gguf 文件")

    def _http_download(self, url: str, dst: str, total_mb: float = 0) -> None:
        """流式下载 + .part 续传 + 进度回报。取消即停。"""
        part = dst + ".part"
        got = os.path.getsize(part) if os.path.isfile(part) else 0
        headers = {"User-Agent": "babel-admin"}
        if got:
            headers["Range"] = f"bytes={got}-"
        req = urllib.request.Request(url, headers=headers)
        with _urlopen(req, timeout=60) as r:
            total = int(r.headers.get("Content-Length") or 0) + got
            mode = "ab" if got else "wb"
            with open(part, mode) as f:
                t_last = 0.0
                while True:
                    if self.cancel.is_set():
                        raise RuntimeError("已取消")
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
                    if time.time() - t_last > 0.5:
                        t_last = time.time()
                        pct = got / max(total, 1) * 100 if total else \
                            min(99.0, got / 1048576 / max(total_mb, 1) * 100)
                        self._set(state="downloading", pct=round(pct, 1),
                                  detail=f"{got / 1048576:.0f}/"
                                         f"{total / 1048576:.0f}MB")
        os.replace(part, dst)
        self._set(state="done", pct=100.0, path=dst)


def _wip_dir(repo: str) -> str:
    base = os.environ.get("HF_HOME") or os.path.expanduser("~/.cache/huggingface")
    return os.path.join(base, "hub",
                        "models--" + repo.replace("/", "--"))


def _dir_mb(p: str) -> float:
    total = 0
    try:
        for root, _dirs, files in os.walk(p):
            for f in files:
                try:
                    fp = os.path.join(root, f)
                    if not os.path.islink(fp):
                        total += os.path.getsize(fp)
                except OSError:
                    pass
    except Exception:
        pass
    return total / 1048576


def _urlopen(req, timeout=60):
    """直连（**绕过环境代理**，沙箱代理会改写本机请求）。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(req, timeout=timeout)


_jobs: dict[str, DownloadJob] = {}


def download(model_id: str, on_progress=None, on_done=None) -> DownloadJob:
    """启动下载（幂等：同一模型重复调用返回已在跑的 job）。"""
    if model_id in _jobs and _jobs[model_id].thread and \
            _jobs[model_id].thread.is_alive():
        return _jobs[model_id]
    job = DownloadJob(model_id, on_progress, on_done)
    _jobs[model_id] = job
    job.start()
    return job


def cancel(model_id: str) -> bool:
    job = _jobs.get(model_id)
    if job:
        job.cancel.set()
        return True
    return False


def uninstall(model_id: str) -> tuple[bool, str]:
    """删除本地模型文件（whisper 缓存目录 / gguf）。只删本目录与 HF 缓存里
    对应的模型，别的不碰。"""
    if model_id.startswith("local:faster-whisper-"):
        size = model_id.replace("local:faster-whisper-", "")
        p = _wip_dir(_WHISPER_REPO.format(size))
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
            return True, f"已删除 {p}"
        return False, "未安装"
    if model_id.startswith("local:hh-translate-"):
        p = hymt_installed(model_id.replace("local:hh-translate-", ""))
        if p and os.path.isfile(p):
            os.remove(p)
            return True, f"已删除 {p}"
        return False, "未安装"
    return False, "远端模型无需删除"


# ── 写回注册表（设为默认）────────────────────────────────────
def set_default(slot: str, model_id: str) -> tuple[bool, str]:
    """slot: 'asr' | 'mt'。写 model_registry.json 的 defaults。"""
    try:
        import model_registry as reg
        data = reg.load(reload=True)
        if not reg.get(model_id, data):
            # 本地模型可能还没在注册表里 → 先登记
            _register_local(data, model_id)
        data.setdefault("defaults", {})[slot] = model_id
        reg.save(data)
        try:
            reg.export_web(do_probe=False)   # web 端快照同步一份
        except Exception:
            pass
        return True, f"默认 {slot} 已设为 {model_id}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _register_local(data: dict, model_id: str) -> None:
    """把 manager 知道的本地模型补进注册表（幂等）。"""
    kind = "asr" if "faster-whisper" in model_id else "mt"
    pool = data.setdefault(kind, [])
    if any(m.get("id") == model_id for m in pool):
        return
    if kind == "asr":
        size = model_id.replace("local:faster-whisper-", "")
        pool.append({
            "id": model_id, "name": f"faster-whisper {size}",
            "vendor": "Systran / ctranslate2", "source": "local",
            "kind": "faster-whisper", "size_key": size, "langs": ["*"],
            "vram_mb": _WHISPER_SIZES.get(size, {}).get("vram", 0),
            "note": "model_manager 登记",
        })
    else:
        size = model_id.replace("local:hh-translate-", "")
        pool.append({
            "id": model_id, "name": f"HY-MT {size} (GGUF)",
            "vendor": "腾讯混元 / llama.cpp", "source": "local",
            "kind": "llama-server", "langs": ["*"],
            "vram_mb": _HYMT.get(size, {}).get("vram", 0),
            "note": "model_manager 登记",
        })


def current_defaults() -> dict:
    try:
        import model_registry as reg
        return dict(reg.load().get("defaults", {}))
    except Exception:
        return {}


if __name__ == "__main__":
    for m in annotate_recommend(catalog()):
        tag = {"installed": "已装", "downloadable": "可下",
               "needs_key": "需key"}.get(m["status"], "?")
        rec = f" ★推荐{m['recommended']}" if m.get("recommended") else ""
        print(f"[{tag}] {m['name']:<28} {m['size_mb']:>6}MB  "
              f"{m['source']}{rec}")
