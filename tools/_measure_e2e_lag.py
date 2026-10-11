# -*- coding: utf-8 -*-
"""量真实端到端延迟：从"说完一句话"到"字幕出现在屏幕上"。

为什么这样量（不靠估算）
    日志里的 "转写2.4s 译1.8s" 只是管线内部耗时，不含：
      · 等句子说完（Segmenter 的 max_len / silence 判定）
      · 攒批（max_batch / max_wait）
      · 排队与浮窗刷新
    真实的"字幕延迟"必须从**音频流入的时刻**算到**文本可显示的时刻**。

做法
    跟主程序用完全相同的组件与参数，按真实速度喂环回音频，记录：
      · 每个音频块被喂入的墙钟时刻（音频时间轴 ↔ 墙钟的映射）
      · 每次文本产出的墙钟时刻
    某段文本对应的音频结束时刻 → 与它产出的墙钟时刻之差 = 该段字幕延迟。
    同时用音频时间轴与墙钟之差，得出"积压量"（是否越跑越落后）。
"""
import io
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
MAX_BATCH = float(sys.argv[2]) if len(sys.argv) > 2 else 4.0
MAX_WAIT = float(sys.argv[3]) if len(sys.argv) > 3 else 1.2

import numpy as np
from live_subtitle_v2 import (LoopbackCapture, to_mono16k, Segmenter,
                             BatchTranscriber, Translator, TARGET_RATE)

print(f"跟主程序同参数：分块 small　max_batch={MAX_BATCH}s　max_wait={MAX_WAIT}s")
print("加载模型与翻译器...")
th = BatchTranscriber("small", 8, max_batch=MAX_BATCH, max_wait=MAX_WAIT)
tr = Translator("auto", "zh")

import concurrent.futures as _cf
_pool = _cf.ThreadPoolExecutor(max_workers=2)

# 远端识别（可选）：设 USE_REMOTE=1 时走 qwen3-asr-flash
import os as _os
_rasr = None
if _os.environ.get('USE_REMOTE') == '1':
    from remote_asr import from_env_or_key
    _rasr = from_env_or_key()
    print('  远端识别: %s' % (_rasr.status() if _rasr else '无 key'))

seg = Segmenter(min_len=0.6, max_len=6.0, silence=0.5, thresh=0.008)
cap = LoopbackCapture()
cap.start()
print(f"采集: [{cap.dev['index']}] {cap.dev['name'][:50]}")
print(f"\n跑 {SECONDS:.0f} 秒（让视频持续出声）...\n")

t0 = time.time()
fed = 0.0                      # 已喂入的音频秒
pending = []
pend_since = None
events = []                    # (产出墙钟, 该段音频结束时刻, 原文, 译文, 转写耗时, 翻译耗时)
n_seg = 0

while time.time() - t0 < SECONDS:
    blk = cap.read(timeout=0.25)
    if blk is None:
        continue
    mono = to_mono16k(blk, cap.rate, cap.ch)
    fed += len(mono) / TARGET_RATE
    for s in seg.push(mono):
        pending.append(s)
        if pend_since is None:
            pend_since = time.time()

    batch_dur = sum(len(x) for x in pending) / TARGET_RATE
    waited = (time.time() - pend_since) if pend_since else 0
    if not (pending and (batch_dur >= MAX_BATCH or waited >= MAX_WAIT)):
        continue

    segs_in, pending, pend_since = pending, [], None
    # 这批音频结束时的"音频时间轴位置"
    audio_end = fed
    t_a = time.time()
    if _rasr is not None:
        _txt, _lg = _rasr(__import__('numpy').concatenate(segs_in))
        if _lg: th.last_lang = _lg
        texts = [_txt] + [''] * (len(segs_in) - 1) if _txt else [''] * len(segs_in)
    else:
        texts = th.transcribe_batch(segs_in)
    t_b = time.time()
    # **与主程序一致的并行翻译**：译文在后台算，显示时刻以"译好"为准，
    # 这样量出来的才是并行版本的延迟（串行版会把翻译加在关键路径上）。
    tr_local = tr

    def _do(_list):
        return [tr_local(x) for x in _list]

    fut = _pool.submit(_do, [t for t in texts if t])
    try:
        zh = fut.result(timeout=30)
    except Exception:
        zh = ["" for t in texts if t]
    t_c = time.time()
    k = 0
    for txt in texts:
        if not txt:
            continue
        z = zh[k] if k < len(zh) else ""
        k += 1
        n_seg += 1
        events.append((t_c - t0, audio_end, txt, z, t_b - t_a, t_c - t_b))

cap.stop()

print(f"{'产出时刻':>8} {'音频位置':>8} {'落后':>6} {'转写':>6} {'翻译':>6}  {'原文':<34} 译文")
print("-" * 118)
lags = []
for wall, aend, txt, z, tasr, ttr in events:
    lag = wall - aend                    # 产出墙钟 - 该段音频结束的音频时间轴
    lags.append(lag)
    print(f"{wall:8.1f} {aend:8.1f} {lag:+6.2f} {tasr:6.2f} {ttr:6.2f}  "
          f"{txt[:34]:<34} {z[:26]}")

print("-" * 118)
if lags:
    lags_sorted = sorted(lags)
    n = len(lags_sorted)
    print(f"\n  段数 {n}")
    print(f"  延迟 中位 {lags_sorted[n//2]:5.2f}s　"
          f"P90 {lags_sorted[min(n-1, int(n*0.9))]:5.2f}s　"
          f"最小 {lags_sorted[0]:5.2f}s　最大 {lags_sorted[-1]:5.2f}s")
    print(f"  平均 {sum(lags)/n:5.2f}s")
    print(f"\n  **字幕平均落后 {sum(lags)/n:.1f} 秒**")
    # 是否越跑越落后
    if n >= 4:
        h = n // 2
        f_half = sum(lags[:h]) / h
        s_half = sum(lags[h:]) / (n - h)
        print(f"  前半 {f_half:.2f}s　后半 {s_half:.2f}s　→ "
              f"{'越跑越落后（积压）' if s_half > f_half * 1.3 else '稳定（无积压）'}")
else:
    print("\n  没抓到事件 —— 可能现在没有声音（环回静音时不产出）")
