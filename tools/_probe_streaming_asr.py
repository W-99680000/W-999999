# -*- coding: utf-8 -*-
"""探 qwen-audio-3.0-asr-flash-streaming 的 WebSocket 接口。

目的：判断"真流式"是否值得接。
    非流式（现在用的 qwen3-asr-flash）固定开销 0.427s，
    批次缩到 1.5s 后端到端中位 0.98s（含翻译 0.3-0.4s）。
    流式的理论收益 = 省掉"等这一批音频攒齐"的那部分。
    但流式要走 WebSocket，协议复杂、要维护长连接、要处理重连。

先只做一件事：确认端点可达、协议形态、以及在多长音频后出第一个字。
不做完整接入 —— 先拿到数据再决定值不值得。

DashScope 流式 ASR 一般是：
  wss://dashscope.aliyuncs.com/api-ws/v1/inference
  先发 run-task，再发二进制音频帧，最后 finish-task
"""
import io
import json
import os
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
if not KEY:
    print("  没有 key")
    sys.exit(1)

try:
    import websocket            # websocket-client
    print("  websocket-client 已装")
except ImportError:
    print("  没有 websocket-client，尝试安装…")
    import subprocess
    r = subprocess.run([sys.executable, "-m", "pip", "install", "-q",
                        "websocket-client"], capture_output=True, text=True)
    print(f"  pip 返回 {r.returncode} {r.stdout[-200:]} {r.stderr[-300:]}")
    try:
        import websocket
        print("  装好了")
    except ImportError:
        print("  装不上，跳过 WebSocket 探测")
        sys.exit(0)

import numpy as np
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
print(f"  素材 {len(audio)/TARGET_RATE:.1f} 秒")

URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
TASK = {
    "header": {"action": "run-task", "task_id": "dsh-probe-1",
               "streaming": "duplex"},
    "payload": {
        "task_group": "audio",
        "task": "asr",
        "function": "recognition",
        "model": "qwen-audio-3.0-asr-flash-streaming",
        "parameters": {
            "format": "pcm",
            "sample_rate": 16000,
            "language_hints": ["en"],
        },
        "input": {},
    },
}

print(f"\n  连接 {URL}")
try:
    ws = websocket.create_connection(
        URL, header=[f"Authorization: bearer {KEY}",
                     "X-DashScope-DataInspection: enable"],
        timeout=15)
    print("  ✓ WebSocket 连上了")
except Exception as e:
    print(f"  ✗ 连不上: {type(e).__name__}: {str(e)[:200]}")
    sys.exit(0)

events = []
t0 = time.time()


def drain(tag, limit=6):
    """收消息（非阻塞式，最多 limit 条）。"""
    got = 0
    while got < limit:
        try:
            ws.settimeout(0.4)
            m = ws.recv()
        except Exception:
            break
        if isinstance(m, bytes):
            events.append((time.time() - t0, tag, f"<binary {len(m)}B>"))
        else:
            try:
                j = json.loads(m)
                ev = j.get("header", {}).get("event", "?")
                payload = j.get("payload", {})
                out = payload.get("output", {})
                sent = out.get("sentence", {}).get("text", "") if out else ""
                events.append((time.time() - t0, tag,
                               f"{ev} {str(sent)[:60]}"))
            except Exception:
                events.append((time.time() - t0, tag, str(m)[:100]))
        got += 1


print("\n  发 run-task")
ws.send(json.dumps(TASK, ensure_ascii=False))
drain("after-run-task")
for ts, tag, msg in events[-4:]:
    print(f"    {ts:6.2f}s {msg}")
if not any("started" in m or "task-started" in m for _, _, m in events):
    print("  （没看到 task-started，可能协议不对，仍继续试发音频）")

# 按真实速率发音频（100ms 一帧）
print("\n  按真实速率发音频帧（每帧 100ms）...")
frame = int(0.1 * TARGET_RATE)
first_text_at = None
i = 0
sent_secs = 0.0
while i < len(audio) and (time.time() - t0) < 22:
    ws.send_binary((np.clip(audio[i:i + frame], -1, 1) * 32767)
                   .astype("<i2").tobytes())
    i += frame
    sent_secs += frame / TARGET_RATE
    before = len(events)
    drain("stream")
    for ts, tag, msg in events[before:]:
        if first_text_at is None and "sentence" in msg and len(msg) > 12:
            first_text_at = ts
        if len(events) - before > 0:
            print(f"    {ts:6.2f}s  已送音频 {sent_secs:5.1f}s  {msg}")
    target = i / TARGET_RATE
    now = time.time() - t0
    if target > now:
        time.sleep(target - now)

print("\n  发 finish-task")
try:
    ws.send(json.dumps({"header": {"action": "finish-task",
                                   "task_id": "dsh-probe-1"},
                        "payload": {"input": {}}}, ensure_ascii=False))
    time.sleep(1.5)
    drain("finish", 20)
except Exception as e:
    print(f"    finish 失败: {type(e).__name__} {str(e)[:120]}")

try:
    ws.close()
except Exception:
    pass

print("\n" + "=" * 74)
print("结果")
print("=" * 74)
print(f"  共收到 {len(events)} 条消息")
for ts, tag, msg in events[:24]:
    print(f"    {ts:6.2f}s [{tag}] {msg}")
if first_text_at:
    print(f"\n  首字时刻 {first_text_at:.2f}s（音频已送 "
          f"{first_text_at:.1f}s 左右）")
else:
    print("\n  ** 没有收到任何识别文本 **")
