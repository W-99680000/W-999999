# -*- coding: utf-8 -*-
"""抓流式 ASR 的完整 JSON 结构 + 把真实增量存盘供离线验证合并策略。

为什么要存盘：合并策略（哪些部分结果该显示、哪些该等）必须在**真实
增量序列**上离线验证，不能靠猜。存下来之后可以反复跑合并逻辑，
不用每次都连服务器、也不受网络波动影响。
"""
import io
import json
import os
import sys
import threading
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
print(f"  素材 {len(audio)/TARGET_RATE:.1f} 秒")

URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
FRAME = int(0.1 * TARGET_RATE)


def run(lang="en", throttle=False):
    task = {
        "header": {"action": "run-task", "task_id": "dsh-cap-1",
                   "streaming": "duplex"},
        "payload": {"task_group": "audio", "task": "asr",
                    "function": "recognition",
                    "model": "qwen-audio-3.0-asr-flash-streaming",
                    "parameters": {"format": "pcm", "sample_rate": 16000,
                                   "language_hints": [lang]},
                    "input": {}},
    }
    ws = websocket.create_connection(
        URL, header=[f"Authorization: bearer {KEY}"], timeout=20)
    ws.send(json.dumps(task, ensure_ascii=False))

    t0 = time.time()
    raw_msgs = []          # 原始 JSON 全存
    done = [False]

    def sender():
        i = 0
        while i < len(audio):
            try:
                ws.send_binary((np.clip(audio[i:i + FRAME], -1, 1) * 32767)
                               .astype("<i2").tobytes())
            except Exception:
                break
            i += FRAME
            if throttle:
                tgt = i / TARGET_RATE
                now = time.time() - t0
                if tgt > now:
                    time.sleep(tgt - now)
        try:
            ws.send(json.dumps({"header": {"action": "finish-task",
                                           "task_id": "dsh-cap-1"},
                                "payload": {"input": {}}},
                               ensure_ascii=False))
        except Exception:
            pass
        done[0] = True

    th = threading.Thread(target=sender, daemon=True)
    th.start()
    while time.time() - t0 < 50:
        try:
            ws.settimeout(1.0)
            m = ws.recv()
        except Exception:
            if done[0] and raw_msgs:
                break
            continue
        if isinstance(m, bytes):
            continue
        try:
            j = json.loads(m)
        except Exception:
            continue
        j["_t"] = round(time.time() - t0, 3)
        raw_msgs.append(j)
        if j.get("header", {}).get("event") == "task-finished":
            break
    try:
        ws.close()
    except Exception:
        pass
    return raw_msgs


print("\n=== 抓一次完整消息序列（不节流）===")
msgs = run(throttle=False)
print(f"  收到 {len(msgs)} 条消息")

# 存盘
out = os.path.join(HERE, "samples", "streaming_msgs.json")
try:
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(msgs, open(out, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"  已存 {out}（{os.path.getsize(out)} 字节）")
except Exception as e:
    print(f"  存盘失败: {e}")

# 打印结构
print("\n=== 各类消息的完整结构（去重后各一条）===")
seen = {}
for j in msgs:
    ev = j.get("header", {}).get("event", "?")
    if ev not in seen:
        seen[ev] = j
for ev, j in seen.items():
    print(f"\n--- event = {ev} ---")
    print(json.dumps(j, ensure_ascii=False, indent=2)[:1200])

# 把所有 result-generated 的 output 列出来，看句子字段
print("\n=== 所有 result-generated 的 payload.output ===")
print(f"  {'t':>6} {'type':>10} {'sent_id':>7} {'end':>5} {'begin':>6}  text")
print("  " + "-" * 74)
for j in msgs:
    if j.get("header", {}).get("event") != "result-generated":
        continue
    out = (j.get("payload") or {}).get("output") or {}
    s = out.get("sentence") or {}
    print(f"  {j['_t']:>6.2f} {str(s.get('sentence_type',''))[:10]:>10} "
          f"{str(s.get('sentence_id',''))[:7]:>7} "
          f"{str(s.get('end_time',''))[:5]:>5} "
          f"{str(s.get('begin_time',''))[:6]:>6}  "
          f"{str(s.get('text',''))[:40]}")
