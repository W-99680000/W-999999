# -*- coding: utf-8 -*-
"""device_placement.py —— 设备盘点、显存预算与放置决策

参考 Nola-Translator 的 engine/nola_translator_engine/compute.py
（GPL-3.0，2026，https://github.com/CerealAxis/Nola-Translator）。
那是我这轮调研里最有参考价值的一份实现，因为**它处理的正是本机的形态**：
独显 + 核显、两个模型（识别 / 翻译）要同放。

我原来的 gpu_setup.py 做什么、缺什么
====================================
有：探测 GPU、加载 CUDA DLL、真跑一次前向验证。
缺（这份文件补上）：
  ① **显存预算** —— 只问"能不能用"，不问"放不放得下"
     实测教训：7B 模型 4809MB + whisper medium 1600MB = 6.4GB / 8188MB，
     虽然装下了但只剩 1.8GB，再放别的就 OOM。
  ② **核显的共享显存不能算进预算** —— 本机 AMD Radeon 780M 被
     llama.cpp 报成 "16277 MiB, 15463 MiB free"，那是**共享内存**，
     不是可用显存。Nola 的注释: "Shared memory is deliberately excluded
     from placement capacity."
  ③ **预留显存** —— 加载成功不等于运行时不 OOM。留 1GB 给上下文/临时张量。
  ④ **stableId** —— CUDA 有 UUID 是稳定的；Vulkan/DirectML 只有序号，
     下次枚举可能变（Vulkan1 → Vulkan0）。**不稳定的 ID 不能持久化。**
  ⑤ **只对资源类错误重试** —— "out of memory / device lost" 可以退 CPU；
     权重文件损坏不应该重试。
  ⑥ **识别与翻译分别放置** —— 现在只有一个 --device，两个模型只能一起走。

本文件只做**盘点与计划**，不加载任何模型权重。
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, replace

# ── 默认预算参数（可被调用方覆盖）───────────────────────────────
DEFAULT_RESERVED_MB = 1024        # 给上下文/临时张量留的余量
MIN_USABLE_MB = 256               # 低于这个数不值得往上放


@dataclass(frozen=True)
class Device:
    """一台可用于推理的设备。"""

    id: str                      # 稳定标识（见 stable_id）
    name: str
    backend: str                 # cuda / vulkan / cpu / rocm / directml
    total_mb: float = 0.0
    free_mb: float = 0.0
    integrated: bool = False     # 核显：显存是共享内存，不能算进预算
    stable_id: bool = True       # id 下次枚举还成立吗
    can_recognition: bool = False
    can_translation: bool = False
    llama_device: str | None = None   # llama.cpp 的设备名（Vulkan1 / CUDA0）
    reason: str = ""
    score: float = 0.0

    @property
    def budget_mb(self) -> float:
        """可真正用于放置模型的显存。

        **核显返回 0** —— 它的"显存"是系统内存借来的，算进来会误导放置。
        集成显卡仍然可以跑模型（走 CPU/共享内存），但不该被当成
        "有 16GB 显存可以放"。
        """
        if self.integrated:
            return 0.0
        return max(0.0, self.free_mb)


def _cpu_device() -> Device:
    return Device(id="cpu", name="CPU", backend="cpu",
                  can_recognition=True, can_translation=True,
                  reason="总是可用，兜底")


def probe_devices(llama_dir: str | None = None) -> tuple[list[Device], list[str]]:
    """盘点可用设备。**不加载任何模型权重。**

    两条来源：
      · torch  —— 拿 CUDA/ROCm 设备的 UUID、显存
      · llama.cpp --list-devices —— 拿 Vulkan/CUDA 设备的名字与显存
             本机的实际输出（实测）：
                Vulkan0: AMD Radeon 780M Graphics (16277 MiB, 15463 MiB free)
                Vulkan1: NVIDIA GeForce RTX 4070 Laptop GPU (7948 MiB, 7180 MiB free)
             **Vulkan0 那 16277 MiB 是共享内存**，必须排除。
    """
    devices = [_cpu_device()]
    notes: list[str] = []

    # ① torch（如果装了且有 CUDA 版）
    try:
        import torch
        if torch.cuda.is_available():
            for i in range(torch.cuda.device_count()):
                props = torch.cuda.get_device_properties(i)
                try:
                    free, total = torch.cuda.mem_get_info(i)
                except Exception:
                    free = total = getattr(props, "total_memory", 0)
                uuid = str(getattr(props, "uuid", "") or "")
                devices.append(Device(
                    id=f"cuda:{uuid}" if uuid else f"cuda:{props.name}:{i}",
                    name=props.name, backend="cuda",
                    total_mb=total / 2 ** 20, free_mb=free / 2 ** 20,
                    stable_id=bool(uuid),
                    can_recognition=True, can_translation=True,
                    score=float(getattr(props, "multi_processor_count", 1)) * 10
                          + props.major * 100,
                ))
        else:
            notes.append(f"torch {torch.__version__} 无 CUDA"
                         f"（CPU 版 torch 不代表机器没有 GPU —— "
                         f"faster-whisper 走 ctranslate2，不用 torch）")
    except ImportError:
        notes.append("没装 torch")

    # ② **ctranslate2** —— 识别走的是它，不是 torch
    # 这是我踩过的坑的另一面：torch 是不是 CUDA 版，**与识别能不能用 GPU 无关**。
    # faster-whisper → ctranslate2 → CUDA。所以必须单独问 ctranslate2。
    try:
        import ctranslate2
        cuda_types = ctranslate2.get_supported_compute_types("cuda")
        n = ctranslate2.get_cuda_device_count()
        if cuda_types and n > 0:
            # 已经有同名的 cuda 设备就只标能力，否则新建一条
            hit = next((d for d in devices
                        if d.backend == "cuda" and d.id.startswith("cuda:")), None)
            if hit is not None:
                i = devices.index(hit)
                devices[i] = replace(hit, can_recognition=True)
            else:
                # 没有 nvidia-smi 式信息，借用 llama.cpp 报的独显显存
                gpu = next((d for d in devices
                            if d.backend in ("vulkan", "cuda")
                            and not d.integrated and d.total_mb > 0), None)
                devices.append(Device(
                    id="cuda:ctranslate2",
                    name=(gpu.name if gpu else f"CUDA ×{n}"),
                    backend="cuda",
                    total_mb=gpu.total_mb if gpu else 0.0,
                    free_mb=gpu.free_mb if gpu else 0.0,
                    stable_id=False,
                    can_recognition=True, can_translation=False,
                    reason=f"ctranslate2 报 {n} 张 CUDA 卡，"
                           f"支持 {sorted(cuda_types)[:3]}",
                    score=110.0,
                ))
            notes.append(f"ctranslate2 支持 CUDA（{n} 张卡，"
                         f"{sorted(cuda_types)[:3]}）→ 识别可用 GPU")
        else:
            notes.append("ctranslate2 不支持 CUDA")
    except ImportError:
        notes.append("没装 ctranslate2")
    except Exception as e:
        notes.append(f"ctranslate2 探测失败：{type(e).__name__}: {str(e)[:100]}")

    # ③ llama.cpp
    exe = None
    if llama_dir:
        cand = os.path.join(llama_dir, "llama-server.exe")
        if os.path.isfile(cand):
            exe = cand
    if exe:
        try:
            r = subprocess.run([exe, "--list-devices"], capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               timeout=20,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            blob = (r.stdout or "") + (r.stderr or "")
            # 形如：  Vulkan1: NVIDIA GeForce RTX 4070 Laptop GPU (7948 MiB, 7180 MiB free)
            pat = re.compile(
                r"^\s*((?:CUDA|Vulkan|SYCL|HIP|ROCm|OpenVINO)\d+):\s*(.*?)\s*"
                r"\((\d+)\s*MiB,\s*(\d+)\s*MiB free\)", re.I | re.M)
            for m in pat.finditer(blob):
                ident, name, total, free = m.groups()
                total, free = float(total), float(free)
                # **核显判定**：名字里带 Radeon/Intel 核显特征，或显存
                # 明显超过独显合理范围。本机 780M 报 16277 MiB。
                low = name.lower()
                integrated = any(k in low for k in (
                    "radeon 7", "radeon graphics", "vega", "intel", "uhd",
                    "iris", "arc graphics"))
                devices.append(Device(
                    id=f"llama:{ident}",
                    name=name + ("（核显，共享内存）" if integrated else ""),
                    backend=re.sub(r"\d+$", "", ident).lower(),
                    total_mb=total, free_mb=free,
                    integrated=integrated,
                    # llama 的序号不是稳定物理 ID
                    stable_id=False,
                    can_recognition=False,   # 识别走 ctranslate2，不走 llama
                    can_translation=True,
                    llama_device=ident,
                    reason=("核显，显存为共享内存，不计入放置预算"
                            if integrated else ""),
                    score=100.0,
                ))
            if not pat.search(blob):
                notes.append(f"llama.cpp 没报出设备（返回码 {r.returncode}）")
        except Exception as e:
            notes.append(f"llama.cpp 探测失败：{type(e).__name__}: {str(e)[:120]}")
    else:
        notes.append("没找到 llama-server.exe（设 LLAMA_DIR）")

    # ── 按**物理卡**合并 ─────────────────────────────────────
    # 同一张 RTX 4070 会被三个来源各报一次：
    #     ctranslate2 → "CUDA x1"（**不报显存**）
    #     llama.cpp   → "Vulkan1: RTX..."（报显存）
    #     torch       → "cuda:..."（若装了 CUDA 版 torch）
    # 不合并的后果（实测踩到）：识别看到 ctranslate2 那条显存 0 ->
    # 判"放不下" -> 回退 CPU。**而它和翻译模型本来就在同一张卡上。**
    # 规则：没名字又没显存的那条，挂到**唯一那张有显存的独显**上；
    #       有多张独显时不猜（名字相同也分不清），保持独立。
    _named = [d for d in devices
              if not d.integrated and d.total_mb > 0 and d.id != "cpu"]
    _unnamed = [d for d in devices
                if d.total_mb == 0 and not d.integrated and d.id != "cpu"
                and d.backend in ("cuda", "rocm")]
    if len(_named) == 1 and _unnamed:
        _tgt = _named[0]
        _rec = _tgt.can_recognition or any(d.can_recognition for d in _unnamed)
        _tr = _tgt.can_translation or any(d.can_translation for d in _unnamed)
        _why = "，".join(d.reason for d in _unnamed if d.reason)
        devices[devices.index(_tgt)] = replace(
            _tgt, can_recognition=_rec, can_translation=_tr,
            reason=(_tgt.reason or "") + (
                ("；已合并 " + _why) if _why else ""))
        devices = [d for d in devices if d not in _unnamed]

    # 同一张卡可能被 CUDA 和 Vulkan 各枚举一次 —— 按名字合并，
    # 避免把它当成两块卡、把显存算两遍。
    merged: list[Device] = []
    for d in devices:
        hit = None
        for i, m in enumerate(merged):
            same_name = d.name and m.name and (
                d.name in m.name or m.name in d.name)
            if same_name and d.backend != m.backend and d.id != "cpu" \
                    and m.id != "cpu":
                hit = i
                break
        if hit is None:
            merged.append(d)
        else:
            m = merged[hit]
            merged[hit] = replace(
                m,
                llama_device=m.llama_device or d.llama_device,
                can_translation=m.can_translation or d.can_translation,
                free_mb=max(m.free_mb, d.free_mb),
                total_mb=max(m.total_mb, d.total_mb),
                reason=(m.reason or "") + (
                    "；CUDA 与 Vulkan 枚举到同一张卡，按一张算" if not m.reason
                    else ""),
            )
    return merged, notes


def model_size_mb(path: str) -> float:
    """模型文件占多少 MB。**是保守估算，不是显存占用保证。**"""
    if not os.path.exists(path):
        return 0.0
    if os.path.isfile(path):
        return os.path.getsize(path) / 2 ** 20
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            if os.path.splitext(f)[1].lower() in (
                    ".bin", ".gguf", ".safetensors", ".pt", ".onnx"):
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
    return total / 2 ** 20


def plan_placement(devices: list[Device], recog_mb: float, trans_mb: float,
                   reserved_mb: int = DEFAULT_RESERVED_MB,
                   recog_choice: str = "auto", trans_choice: str = "auto",
                   allow_cpu_fallback: bool = True):
    """给识别和翻译各挑一台设备。

    返回 (识别设备, 翻译设备, 原因列表)。

    规则（都来自实测教训）：
      · 核显的 budget_mb 是 0 → 不会被选来放模型
      · 每个候选组合都要 **放得下**：free >= 所需 + 预留
      · 两个模型放同一张卡时，**需求相加** 后再判
      · 优先独显；识别优先（它是主延迟来源）
      · 手动指定但不可用：可回退 CPU，但**不悄悄换到另一张 GPU**
    """
    by_id = {d.id: d for d in devices}
    reasons: list[str] = []
    cpu = by_id.get("cpu") or _cpu_device()

    def pick(choice: str, task: str, need_mb: float) -> list[Device]:
        ok = (lambda d: d.can_recognition) if task == "recognition" \
            else (lambda d: d.can_translation)
        if choice != "auto":
            d = by_id.get(choice)
            if d is not None and ok(d):
                return [d]
            if allow_cpu_fallback:
                reasons.append(f"{task} 指定的设备 {choice} 不可用，回退 CPU")
                return [cpu]
            raise ValueError(f"{task} 指定设备不可用：{choice}")
        return [d for d in devices if ok(d)]

    recog = pick(recog_choice, "recognition", recog_mb)
    trans = pick(trans_choice, "translation", trans_mb)

    plans = []
    for a in recog:
        for b in trans:
            # 同一张卡 → 需求相加
            need: dict[str, float] = {}
            if a.id != "cpu":
                need[a.id] = need.get(a.id, 0.0) + recog_mb
            if b.id != "cpu":
                need[b.id] = need.get(b.id, 0.0) + trans_mb
            fits = True
            for did, want in need.items():
                d = by_id.get(did)
                if d is None:
                    fits = False
                    break
                if d.budget_mb <= 0:
                    # 核显 / 未知显存：不当作能放
                    fits = False
                    reasons.append(
                        f"{d.name} 不计入放置预算（核显或显存未知），跳过")
                    break
                if d.budget_mb < want + reserved_mb:
                    fits = False
                    reasons.append(
                        f"{d.name} 放不下：需 {want:.0f}MB + 预留 "
                        f"{reserved_mb}MB > 可用 {d.budget_mb:.0f}MB")
                    break
            if not fits:
                continue
            # 打分：识别上独显权重最高，其次翻译
            score = (10000 if a.id != "cpu" else 0)
            score += (5000 if b.id != "cpu" else 0)
            score += a.score * 2 + b.score
            if a.id == b.id and a.id != "cpu":
                score -= min(a.score, b.score) * 0.5   # 同卡略降（争用）
            plans.append((score, a, b))

    if not plans:
        if allow_cpu_fallback:
            reasons.append("没有放得下的组合，全部回退 CPU")
            return cpu, cpu, reasons
        raise ValueError("所选设备的可用显存不足（放不下模型+预留）")

    _, a, b = max(plans, key=lambda p: p[0])
    return a, b, reasons


# ── 只对资源类错误重试 ──────────────────────────────────────────
RESOURCE_WORDS = (
    "out of memory", "cuda_error_out_of_memory", "cuda driver",
    "cuda-capable device", "device has been lost", "no kernel image is available",
    "invalid device function", "insufficient memory", "not enough memory",
    "cublas", "cudnn", "library cublas64", "cannot be loaded",
)


def is_resource_failure(err: BaseException) -> bool:
    """这个错误算"设备/资源问题"吗（值得退 CPU 重试）？

    **权重文件损坏、配置错误都不算** —— 那些重试多少次都一样，
    而且会把真正的错误信息淹没。Nola 的 compute.py 里叫 resource_failure。
    """
    seen = set()
    cur: BaseException | None = err
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        low = str(cur).lower()
        if any(w in low for w in RESOURCE_WORDS):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def release_failed_load(err: BaseException) -> None:
    """清掉异常回溯帧里抓着的张量。

    为什么需要：加载失败时，traceback 的帧还引用着已分配的大张量，
    不清就重试会**二次 OOM**（第一次的显存还没还）。
    """
    import traceback
    seen = set()
    cur: BaseException | None = err
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if cur.__traceback__:
            traceback.clear_frames(cur.__traceback__)
        cur = cur.__cause__ or cur.__context__


def describe(devices: list[Device]) -> str:
    """给人看的设备表。"""
    lines = []
    for d in devices:
        tag = []
        if d.integrated:
            tag.append("核显")
        if not d.stable_id:
            tag.append("ID不稳定")
        if d.can_recognition:
            tag.append("可识别")
        if d.can_translation:
            tag.append("可翻译")
        bud = (f"预算 {d.budget_mb:.0f}MB" if d.budget_mb > 0
               else "预算 0（不计入）")
        lines.append(f"  {d.id:<44} {d.name[:34]:<34} "
                     f"{d.total_mb:>7.0f}/{d.free_mb:>7.0f}MB {bud:<18} "
                     f"{' '.join(tag)}")
        if d.reason:
            lines.append(f"      说明: {d.reason}")
    return "\n".join(lines)
