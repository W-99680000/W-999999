# -*- coding: utf-8 -*-
"""第③项：stableId 持久化。

要解决的问题：Vulkan / DirectML 的设备**序号不稳定** —— 这次是
`Vulkan1`，换驱动或插拔显卡后可能变成 `Vulkan0`，而它指向的可能是
**另一张卡**。把这种序号存进设置文件，下次照着它加载，就会加载到错的设备上。

Nola 的原话（compute.py）：
    llama's backend index is not a stable physical ID; preserve it as a
    named choice, and **refuse the choice if the name/index no longer
    exists on the next probe**.

所以规则：
  · 存的是**名字**（可读、可迁移），同时记住它当时稳不稳定
  · 加载时按名字重新匹配；**多个同名设备 → 拒绝**（分不清就是分不清）
  · 名字匹配不到 → 当作没配过，回退 auto，而不是硬用一个序号
"""
from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DEVICE_PREF = os.path.join(HERE, "device_pref.json")


def save_preference(recog_id: str, trans_id: str, devices) -> None:
    """把选中的设备存下来（写名字，不写可能失效的序号）。"""
    by_id = {d.id: d for d in devices}

    def rec(did):
        d = by_id.get(did)
        if d is None:
            return {"id": did, "name": "", "stable": False}
        return {"id": d.id, "name": d.name, "stable": bool(d.stable_id)}

    try:
        json.dump({"recognition": rec(recog_id), "translation": rec(trans_id)},
                  open(DEVICE_PREF, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
    except OSError:
        pass


def load_preference():
    """读回设备偏好。返回 (识别id|null, 翻译id|null, 说明列表)。"""
    notes: list[str] = []
    try:
        d = json.load(open(DEVICE_PREF, encoding="utf-8"))
    except (OSError, ValueError):
        return None, None, notes
    return d.get("recognition"), d.get("translation"), notes


def resolve(pref: dict | None, devices) -> tuple[str | None, list[str]]:
    """把一个存下来的设备记录，解析成当前设备表里的 id。

    返回 (解析出的 id 或 None, 说明)。
    **解析不出来就返回 None** —— 调用方据此回退 auto，
    绝不能拿一个可能指错的序号去加载。
    """
    notes: list[str] = []
    if not pref:
        return None, notes
    want_id = pref.get("id") or ""
    want_name = (pref.get("name") or "").strip()
    stable = bool(pref.get("stable"))

    # ① id 稳定 → 直接按 id 找
    if stable and want_id:
        hit = next((d for d in devices if d.id == want_id), None)
        if hit is not None:
            return hit.id, notes
        notes.append(f"存下的设备 {want_id} 这次没出现，改用名字匹配")

    # ② 按名字找；**多个同名就拒绝**
    if want_name:
        same = [d for d in devices
                if d.name == want_name or want_name in d.name]
        if len(same) == 1:
            notes.append(f"按名字匹配到 {same[0].id}")
            return same[0].id, notes
        if len(same) > 1:
            notes.append(f"名字 {want_name!r} 匹配到 {len(same)} 台设备，"
                         f"分不清 → 不采用，回退自动")
            return None, notes

    # ③ 序号不稳定又没名字 → 拒绝
    if not stable:
        notes.append(f"存下的 id {want_id!r} 不是稳定标识，且名字匹配不到 "
                     f"→ 拒绝使用（序号可能已指向别的卡）")
    else:
        notes.append(f"存下的设备 {want_id!r} 这次找不到 → 回退自动")
    return None, notes


def describe_pref(pref: dict | None) -> str:
    if not pref:
        return "（没有保存过设备偏好）"
    did = pref.get("id") or "?"
    nm = pref.get("name") or "?"
    st = "稳定" if pref.get("stable") else "**不稳定**"
    return f"{nm}（{did}，{st}）"
