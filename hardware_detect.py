# -*- coding: utf-8 -*-
"""hardware_detect.py —— 本机性能检测 + 模型推荐

干什么
======
1. 探测 GPU（型号 / 显存总量 / 当前空闲）、CPU、内存、磁盘剩余。
2. 按实测结果给出一个 0~100 的算力分。
3. 根据分数推荐 ASR（faster-whisper 档位）与 MT（本地 HY-MT 档位或在线），
   每条推荐都带理由，最终选择权在用户（本模块只算不写）。

设计原则
========
- 全部只读，不改任何配置；推荐结果由调用方（admin_qt / model_manager）消费。
- 每一步探测都有兜底：nvidia-smi 不在 → WMI/PowerShell → 拿不到就给 None，
  绝不因为缺工具让检测整体失败。
- 结果带 ttl 缓存（默认 30s），界面轮询不会反复跑外部命令。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(HERE, "work", "hardware_report.json")

# ── ASR 档位实测显存（MB，faster-whisper float16，与 model_registry 对齐）──
ASR_VRAM = {
    "tiny": 800, "base": 1000, "small": 1900, "medium": 3100, "large-v3": 4700,
}
# HY-MT GGUF 本地翻译档位 → 约估显存/内存占用（MB）
MT_LOCAL = {
    "base":   {"mb": 700,  "note": "最快，日常短句够用"},
    "small":  {"mb": 1200, "note": "质量/速度平衡"},
    "medium": {"mb": 2200, "note": "质量更好，长句更稳"},
    "large":  {"mb": 3800, "note": "最高质量，吃显存"},
}

_cache: dict | None = None
_cache_ts = 0.0


def _run(cmd: list[str], timeout: float = 8.0) -> str:
    """跑外部命令拿输出；失败返回空串。静默，不抛。"""
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=timeout, creationflags=flags)
        return (out.stdout or "") + (out.stderr or "")
    except Exception:
        return ""


def detect_gpu() -> dict:
    """GPU 探测。优先 nvidia-smi（最准），退回 PowerShell CIM。"""
    g = {"name": None, "vendor": None, "vram_total_mb": None,
         "vram_free_mb": None, "driver": None, "cuda": None, "source": None}
    smi = shutil.which("nvidia-smi")
    if smi:
        out = _run([smi, "--query-gpu=name,memory.total,memory.free,driver_version",
                    "--format=csv,noheader,nounits"])
        line = (out.strip().splitlines() or [""])[0]
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 4 and parts[0]:
            g.update(name=parts[0], vendor="NVIDIA",
                     vram_total_mb=_f(parts[1]), vram_free_mb=_f(parts[2]),
                     driver=parts[3], source="nvidia-smi")
            v = _run([smi])  # 从完整输出里抠 CUDA Version
            m = re.search(r"CUDA Version:\s*([\d.]+)", v)
            if m:
                g["cuda"] = m.group(1)
            return g
    # 非 N 卡 / 无 smi：PowerShell 拿型号与显存（核显/AMD 也能看个大概）
    out = _run(["powershell", "-NoProfile", "-Command",
                "Get-CimInstance Win32_VideoController | "
                "Select-Object Name,AdapterRAM,DriverVersion | "
                "ConvertTo-Json -Compress"], timeout=12.0)
    try:
        data = json.loads(out)
        if isinstance(data, dict):
            data = [data]
        # 多张卡时挑显存最大的那张
        best = None
        for c in data or []:
            ram = int(c.get("AdapterRAM") or 0)
            if best is None or ram > best[1]:
                best = (c, ram)
        if best:
            c, ram = best
            name = c.get("Name") or ""
            g.update(name=name,
                     vendor=("NVIDIA" if "nvidia" in name.lower() else
                             "AMD" if ("amd" in name.lower() or "radeon" in name.lower())
                             else "Intel" if "intel" in name.lower() else None),
                     vram_total_mb=round(ram / 1048576) if ram else None,
                     driver=c.get("DriverVersion"), source="cim")
    except Exception:
        pass
    return g


def _f(s) -> float | None:
    try:
        return float(s)
    except Exception:
        return None


def detect_cpu() -> dict:
    import platform
    c = {"brand": platform.processor() or "", "physical": None,
         "logical": None, "freq_mhz": None}
    try:
        import psutil
        c["physical"] = psutil.cpu_count(logical=False)
        c["logical"] = psutil.cpu_count(logical=True)
        fq = psutil.cpu_freq()
        if fq:
            c["freq_mhz"] = round(fq.max or fq.current or 0)
    except Exception:
        c["logical"] = os.cpu_count()
        # 无 psutil 时用 WMI 补物理核数
        out = _run(["powershell", "-NoProfile", "-Command",
                    "(Get-CimInstance Win32_Processor | Measure-Object "
                    "NumberOfCores -Sum).Sum"], timeout=10.0)
        m = re.search(r"(\d+)", out)
        if m:
            c["physical"] = int(m.group(1))
    if not c.get("brand") or "Family" in (c.get("brand") or ""):
        out = _run(["powershell", "-NoProfile", "-Command",
                    "(Get-CimInstance Win32_Processor).Name"], timeout=10.0)
        name = out.strip().splitlines()
        if name and name[0].strip():
            c["brand"] = name[0].strip()
    return c


def detect_mem() -> dict:
    m = {"total_mb": None, "free_mb": None}
    try:
        import psutil
        vm = psutil.virtual_memory()
        m["total_mb"] = round(vm.total / 1048576)
        m["free_mb"] = round(vm.available / 1048576)
    except Exception:
        pass
    if m["total_mb"] is None and os.name == "nt":
        # ctypes 兜底：GlobalMemoryStatusEx
        try:
            import ctypes

            class _MS(ctypes.Structure):
                _fields_ = [("len", ctypes.c_ulong), ("load", ctypes.c_ulong),
                            ("total", ctypes.c_ulonglong), ("avail", ctypes.c_ulonglong),
                            ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong),
                            ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong),
                            ("ave", ctypes.c_ulonglong)]
            st = _MS()
            st.len = ctypes.sizeof(_MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
            m["total_mb"] = round(st.total / 1048576)
            m["free_mb"] = round(st.avail / 1048576)
        except Exception:
            pass
    return m


def detect_disk(path: str = HERE) -> dict:
    d = {"free_gb": None}
    try:
        usage = shutil.disk_usage(os.path.splitdrive(path)[0] + "\\"
                                  if os.name == "nt" else path)
        d["free_gb"] = round(usage.free / 1073741824, 1)
    except Exception:
        pass
    return d


def score(rep: dict) -> int:
    """0~100 算力分。GPU 显存权重最高（ASR+MT 都吃显存）。"""
    s = 0
    vram = (rep["gpu"].get("vram_total_mb") or 0)
    if vram >= 11000:
        s += 55
    elif vram >= 8000:
        s += 48
    elif vram >= 6000:
        s += 40
    elif vram >= 4000:
        s += 28
    elif vram >= 2000:
        s += 15
    ram = (rep["mem"].get("total_mb") or 0)
    s += 15 if ram >= 32768 else 12 if ram >= 16384 else 8 if ram >= 8192 else 3
    cpu = rep["cpu"].get("logical") or 0
    s += 15 if cpu >= 16 else 12 if cpu >= 12 else 9 if cpu >= 8 else 5 if cpu >= 4 else 2
    if rep["gpu"].get("cuda"):
        s += 15
    elif rep["gpu"].get("vendor") == "NVIDIA":
        s += 8
    return min(100, s)


def recommend(rep: dict) -> dict:
    """按检测结果给推荐。返回每条都带理由；用户自己决定装不装。"""
    vram = rep["gpu"].get("vram_total_mb") or 0
    free = rep["gpu"].get("vram_free_mb") or vram
    ram = rep["mem"].get("total_mb") or 0
    sc = rep.get("score", score(rep))

    # ── ASR 推荐：留 1.5GB 余量给 MT 与系统 ──
    budget = max(0, free - 1500)
    asr = "tiny"
    for size in ("tiny", "base", "small", "medium", "large-v3"):
        if ASR_VRAM[size] <= budget:
            asr = size
    if not rep["gpu"].get("name"):
        asr = "base" if (rep["cpu"].get("logical") or 0) >= 8 else "tiny"
    asr_reason = {
        "large-v3": "显存充裕，最高准确度",
        "medium": "准确度与速度的平衡点（本机当前默认）",
        "small": "显存有限时的稳妥档",
        "base": "无独显/CPU 推理的可用档",
        "tiny": "保底档，仅建议测试用",
    }[asr]

    # ── MT 推荐：本地 vs 在线 ──
    rest = budget - ASR_VRAM.get(asr, 800)
    mt_local = None
    for k in ("large", "medium", "small", "base"):
        if MT_LOCAL[k]["mb"] <= max(rest, 0 if vram else ram // 2):
            mt_local = k
            break
    if vram >= 6000 and mt_local in ("medium", "large", "small"):
        mt = {"type": "local", "size": mt_local,
              "reason": f"显存余量够，本地翻译零网络延迟、隐私不出机；"
                        f"{MT_LOCAL[mt_local]['note']}"}
    elif vram >= 3000:
        mt = {"type": "local", "size": "base",
              "reason": "显存偏紧，本地 base 档可用；想要更好质量可选在线"}
    else:
        mt = {"type": "remote", "size": None,
              "reason": "显存/算力不足，建议在线翻译（qwen-mt-flash 等），"
                        "把显存全留给 ASR"}

    grade = ("强劲" if sc >= 75 else "良好" if sc >= 55
             else "中等" if sc >= 35 else "入门")
    return {
        "grade": grade,
        "asr": {"size": asr, "model_id": f"local:faster-whisper-{asr}",
                "reason": asr_reason,
                "vram_mb": ASR_VRAM.get(asr)},
        "mt": mt,
        "notes": _notes(rep),
    }


def _notes(rep: dict) -> list[str]:
    out = []
    g = rep["gpu"]
    if g.get("name") and not g.get("cuda") and g.get("vendor") == "NVIDIA":
        out.append("检测到 N 卡但 CUDA 不可用：多半是解释器缺 cuBLAS/cuDNN，"
                   "见 babel_admin/dlls.py")
    vram = g.get("vram_total_mb") or 0
    free = g.get("vram_free_mb") or 0
    if vram and free and free < vram * 0.4:
        out.append(f"显存已被占用 {vram - free:.0f}/{vram:.0f}MB："
                   "关掉游戏/浏览器硬解再跑大模型，或降一档")
    if (rep["mem"].get("free_mb") or 99999) < 4096:
        out.append("内存余量不足 4GB，建议关掉多余程序再启动链路")
    if (rep["disk"].get("free_gb") or 999) < 10:
        out.append("磁盘剩余不足 10GB，下载 large 模型前请先清理")
    if not rep["gpu"].get("name"):
        out.append("未检测到独显：ASR 只能 CPU 推理（慢 5~10 倍）或用远端 ASR")
    return out


def detect(force: bool = False, ttl: float = 30.0) -> dict:
    """总入口。带缓存；force=True 强制重测。"""
    global _cache, _cache_ts
    if not force and _cache and time.time() - _cache_ts < ttl:
        return _cache
    rep = {"ts": time.time(),
           "gpu": detect_gpu(), "cpu": detect_cpu(),
           "mem": detect_mem(), "disk": detect_disk(),
           "python": sys.executable}
    rep["score"] = score(rep)
    rep["recommend"] = recommend(rep)
    _cache, _cache_ts = rep, time.time()
    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        tmp = CACHE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CACHE_PATH)
    except Exception:
        pass
    return rep


def fmt(rep: dict) -> str:
    """给人看的一屏摘要。"""
    g, c, m, d = rep["gpu"], rep["cpu"], rep["mem"], rep["disk"]
    r = rep["recommend"]
    lines = [
        f"算力分 {rep['score']}/100（{r['grade']}）",
        f"GPU  {g.get('name') or '未检测到独显'}"
        + (f"  显存 {g.get('vram_free_mb', '?')}/{g.get('vram_total_mb', '?')}MB 空闲"
           if g.get("vram_total_mb") else "")
        + (f"  CUDA {g['cuda']}" if g.get("cuda") else ""),
        f"CPU  {c.get('brand') or '?'}  {c.get('physical')}C/{c.get('logical')}T"
        + (f" @ {c['freq_mhz']}MHz" if c.get("freq_mhz") else ""),
        f"内存 {m.get('free_mb', '?')}/{m.get('total_mb', '?')}MB 可用",
        f"磁盘剩余 {d.get('free_gb', '?')}GB",
        "",
        f"推荐 ASR：faster-whisper {r['asr']['size']}（约 {r['asr']['vram_mb']}MB 显存）"
        f"—— {r['asr']['reason']}",
        f"推荐 MT ：{'本地 HY-MT ' + r['mt']['size'] if r['mt']['type'] == 'local' else '在线翻译'}"
        f"—— {r['mt']['reason']}",
    ]
    for n in r["notes"]:
        lines.append(f"注意：{n}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(fmt(detect(force=True)))
