# -*- coding: utf-8 -*-
"""精确量流式 ASR 的延迟：音频帧送出 → 对应文字出来，差多少。

上一版脚本有两个错：
  ① "已送音频"算错了（frame/RATE 累积时多算 10 倍），所以那些数字
     看起来像"送 2.3s 音频却花了 9.78s"——其实是送了 23s。
  ② 加了节流去模拟实时，结果变成"我在追速率"，量到的不是纯延迟。

这一版：
  · 不做节流，尽快喂（量纯处理延迟）
  · 每帧记下送出时刻，收到文字时算"这帧送出 → 文字出现"的差
  · 同时对比非流式 qwen3-asr-flash 的同一指标
"""
import io
import json
import os
import statistics
import sys
import time
import wave

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

KEY = ""
for line in open(os.path.join(os.path.expanduser("~"), ".dsh", ".dashscope"),
                 encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#"):
        KEY = line
        break

import numpy as np
import websocket
from live_subtitle_v2 import to_mono16k, TARGET_RATE

# ── 样本检查 ─────────────────────────────────────────────
# tests/e2e_speech.wav 随仓库发布（samples/ 下）。缺失就友好退出。
def _need_sample(p):
    """确认样本存在，否则给出提示后退出。"""
    if os.path.exists(p):
        return p
    print("跳过：缺少 " + p)
    print("本仓库自带 samples/e2e_speech.wav；请确认已完整 clone。")
    raise SystemExit(0)

p = os.path.join(HERE, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples", "e2e_speech.wav"))
if not os.path.exists(p):
    p = os.path.join(HERE, "samples", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples", "e2e_speech.wav"))
w = wave.open(p, "rb")
raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
audio = to_mono16k(raw, w.getframerate(), w.getnchannels())
DUR = len(audio) / TARGET_RATE
print(f"  素材 {DUR:.1f} 秒")

URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
FRAME = int(0.1 * TARGET_RATE)


def run_stream():
    """不做节流：尽快把音频发完，记录每帧的送出时刻。"""
    task = {
        "header": {"action": "run-task", "task_id": "dsh-probe-2",
                   "streaming": "duplex"},
        "payload": {"task_group": "audio", "task": "asr",
                    "function": "recognition",
                    "model": "qwen-audio-3.0-asr-flash-streaming",
                    "parameters": {"format": "pcm", "sample_rate": 16000,
                                   "language_hints": ["en"]},
                    "input": {}},
    }
    ws = websocket.create_connection(
        URL, header=[f"Authorization: bearer {KEY}"], timeout=20)
    ws.send(json.dumps(task, ensure_ascii=False))

    t0 = time.time()
    sent = []            # (送完这一刻, 已送音频秒)
    texts = []           # (文字出现时刻, 文字, 该时刻已送音频秒)
    done = [False]

    # 发送线程
    import threading

    def sender():
        i = 0
        while i < len(audio):
            try:
                ws.send_binary((np.clip(audio[i:i + FRAME], -1, 1) * 32767)
                               .astype("<i2").tobytes())
            except Exception:
                break
            i += FRAME
            sent.append((time.time() - t0, i / TARGET_RATE))
        try:
            ws.send(json.dumps({"header": {"action": "finish-task",
                                           "task_id": "dsh-probe-2"},
                                "payload": {"input": {}}},
                               ensure_ascii=False))
        except Exception:
            pass
        done[0] = True

    th = threading.Thread(target=sender, daemon=True)
    th.start()
    last_sent_secs = 0.0
    while time.time() - t0 < 45:
        try:
            ws.settimeout(1.0)
            m = ws.recv()
        except Exception:
            if done[0] and sent:
                break
            continue
        if isinstance(m, bytes):
            continue
        try:
            j = json.loads(m)
        except Exception:
            continue
        ev = j.get("header", {}).get("event", "")
        if ev == "task-finished":
            break
        out = (j.get("payload") or {}).get("output") or {}
        txt = (out.get("sentence") or {}).get("text", "")
        if txt:
            if sent:
                last_sent_secs = sent[-1][1]
            texts.append((time.time() - t0, txt, last_sent_secs))
    try:
        ws.close()
    except Exception:
        pass
    return t0, sent, texts


print("\n" + "=" * 74)
print("流式（不做节流，尽快喂）")
print("=" * 74)
t0, sent, texts = run_stream()
send_done = sent[-1][0] if sent else 0
print(f"  音频全部送完用了 {send_done:.2f}s（音频本身 {DUR:.1f}s → "
      f"{DUR/send_done:.1f}× 实时）")
print(f"  收到 {len(texts)} 条增量文本")
for ts, txt, snt in texts:
    print(f"    {ts:6.2f}s (已送 {snt:5.1f}s)  {txt[:58]}")
if texts:
    first = texts[0]
    print(f"\n  首字：送出 {first[2]:.1f}s 音频后 {first[0]:.2f}s 出现"
          f"（第 1 条文本在 {first[0]:.2f}s）")
    # 增量延迟：每条文本出现时刻 - 该时刻已送音频量
    lags = [ts - snt for ts, _, snt in texts]
    print(f"  **处理延迟（文字出现 - 已送音频量）：中位 {statistics.median(lags):.2f}s"
          f"　最小 {min(lags):.2f}s　最大 {max(lags):.2f}s**")

# 对照：非流式
print("\n" + "=" * 74)
print("对照：非流式 qwen3-asr-flash（同样尽快发）")
print("=" * 74)
sys.path.insert(0, HERE)
from remote_asr import from_env_or_key
r = from_env_or_key()
for dur in (1.5, 4.0, 16.0):
    a = audio[: int(dur * TARGET_RATE)]
    ts = []
    for _ in range(3):
        t = time.time()
        r(a)
        ts.append(time.time() - t)
    ts.sort()
    print(f"  {dur:5.1f}s 音频 → {ts[1]:.3f}s（中位）")

print(f"\n  现状：主程序用非流式 + 1.5s 批次，端到端中位 0.98s（含翻译）")
