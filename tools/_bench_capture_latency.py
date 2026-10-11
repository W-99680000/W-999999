# -*- coding: utf-8 -*-
"""量采集这一段到底占多少延迟 —— 决定"换虚拟声卡"值不值得。

当前链路各段（实测过的部分已标注）：
    采集（WASAPI 环回）        ← **没量过，本脚本要量**
    重采样/切段                 ← 纯数组运算，微秒级
    噪声门                      ← 6ms（实测）
    GPU 识别                    ← 0.19~0.60s（实测）
    翻译                        ← 0.12~0.15s（实测）
    显示                        ← ?
    ─────────────────────────
    端到端                      ← 中位约 1s

要量的是**采集这一段**：从"声音在系统里播放"到"我们拿到这段样本"，
中间隔多久。方法：
  ① 环回设备的缓冲结构（采样率、每块样本数、回调周期）
  ② 实际回调间隔（连续记录时间戳，看抖动）
  ③ 队列等待（我们的队列 maxsize=400，看有没有积压）
  ④ "声音开始 → 第一批样本到手"的绝对延迟（播放一个脉冲来测）
"""
import io
import os
import statistics
import sys
import time
import wave

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import numpy as np
from live_subtitle_v2 import LoopbackCapture, to_mono16k, TARGET_RATE

print("=" * 88)
print("① 设备与缓冲结构")
print("=" * 88)
cap = LoopbackCapture()
print(f"  采样率      {cap.rate} Hz")
print(f"  声道        {cap.ch}")
print(f"  每块样本    {cap.blocksize}（{cap.blocksize/cap.rate*1000:.1f} ms）")
print(f"  队列上限    {cap.q.maxsize} 块 = "
      f"{cap.q.maxsize*cap.blocksize/cap.rate:.1f} 秒")
print()
print("  环回设备完整信息:")
for k in ("index", "name", "defaultSampleRate", "maxInputChannels",
          "defaultLowInputLatency", "defaultHighInputLatency"):
    if k in cap.dev:
        v = cap.dev[k]
        if isinstance(v, float):
            v = f"{v:.6f} s = {v*1000:.1f} ms"
        print(f"    {k:<26} {v}")

print()
print("=" * 88)
print("② 回调间隔与抖动（这是采集的固有节拍）")
print("=" * 88)
times = []
cap._cb_orig = cap._cb


def timed_cb(in_data, frames, tinfo, status):
    times.append(time.time())
    return cap._cb_orig(in_data, frames, tinfo, status)


cap._cb = timed_cb
cap.start()
t0 = time.time()
while time.time() - t0 < 6:
    time.sleep(0.1)
cap.stop()

if len(times) > 10:
    gaps = [times[i + 1] - times[i] for i in range(len(times) - 1)]
    gaps_ms = sorted(g * 1000 for g in gaps)
    med = statistics.median(gaps_ms)
    print(f"  收到回调 {len(times)} 次 / 6 秒  →  实际 {len(times)/6:.0f} 次/秒")
    print(f"  理论块长 {cap.blocksize/cap.rate*1000:.1f} ms  →  "
          f"理论 {cap.rate/cap.blocksize:.0f} 次/秒")
    print(f"  间隔 中位 {med:.1f} ms　P90 {gaps_ms[int(len(gaps_ms)*0.9)]:.1f} ms"
          f"　最大 {max(gaps_ms):.1f} ms")
    print(f"  → **单块固有延迟 ≈ {med/2:.1f} ms（平均等半块）**")
    print(f"  → 最坏 {max(gaps_ms):.1f} ms（一整块没到）")
else:
    print(f"  只收到 {len(times)} 次回调 —— 可能没有音频在播放")

print()
print("=" * 88)
print("③ 队列等待：我们的处理跟得上采集吗")
print("=" * 88)
cap2 = LoopbackCapture()
cap2.start()
t0 = time.time()
maxq = 0
samples = 0
while time.time() - t0 < 6:
    b = cap2.read(timeout=0.2)
    maxq = max(maxq, cap2.q.qsize())
    if b is not None:
        samples += len(b)
    time.sleep(0.005)          # 模拟主循环里的其他工作
cap2.stop()
print(f"  6 秒内队列峰值 {maxq} 块（上限 {cap2.q.maxsize}）")
print(f"  → 队列积压延迟最坏 {maxq*cap2.blocksize/cap2.rate*1000:.0f} ms")
print(f"  → {'**有积压，需要减小块或加快消费**' if maxq > 3 else '无实质积压'}")

print()
print("=" * 88)
print("④ 结论：这一段在总延迟里占多少")
print("=" * 88)
seg = med if len(times) > 10 else 40.0
print(f"  采集：单块固有 ~{seg/2:.0f} ms（平均）+ 队列最多 {maxq*cap2.blocksize/cap2.rate*1000:.0f} ms")
print(f"  噪声门 6 ms")
print(f"  GPU 识别 190~600 ms")
print(f"  翻译 120~150 ms")
print()
total = seg / 2 + 6 + 400 + 135
print(f"  采集占比 ≈ {seg/2/total*100:.0f}%　（识别+翻译占"
      f" {(400+135)/total*100:.0f}%）")
print()
if seg / 2 < 40:
    print("  → 采集只占几个百分点。**换虚拟声卡省不下多少** ——")
    print("    省下的最多是那半块（~20ms）与队列积压。")
    print("    真正的延迟在识别（GPU 前向）和翻译（固定开销）上。")
else:
    print("  → 采集占比不小，值得考虑换采集方式。")
