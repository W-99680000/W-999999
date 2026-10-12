# -*- coding: utf-8 -*-
"""看 WhisperLiveKit 到底回什么 JSON —— 不再猜字段名。

上一版我按猜的字段名取文本，结果 1276 条全是 "You"。
先把**原始消息**打出来看清结构，再说识别对不对。
"""
import asyncio
import io
import json
import os
import sys
import time
import wave

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, "D:/NEW WORK/work/subtitle")

import numpy as np
from live_subtitle_v2 import to_mono16k, TARGET_RATE

HERE = "D:/NEW WORK/work/subtitle"
# ── 样本检查 ─────────────────────────────────────────────
# tests/e2e_speech.wav 随仓库发布（samples/ 下）。缺失就友好退出。
def _need_sample(p):
    """确认样本存在，否则给出提示后退出。"""
    if os.path.exists(p):
        return p
    print("跳过：缺少 " + p)
    print("本仓库自带 samples/e2e_speech.wav；请确认已完整 clone。")
    raise SystemExit(0)

w = wave.open(os.path.join(HERE, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples", "e2e_speech.wav")), "rb")
raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
AUDIO = to_mono16k(raw, w.getframerate(), w.getnchannels())
print("  素材 %.1f 秒英文" % (len(AUDIO) / TARGET_RATE))

import websockets


async def main():
    url = "ws://127.0.0.1:8199/asr?language=en"
    t0 = time.time()
    seen = 0
    async with websockets.connect(url, max_size=None) as ws:
        async def reader():
            nonlocal seen
            async for msg in ws:
                if seen < 6:
                    seen += 1
                    s = msg if isinstance(msg, str) else msg.decode(
                        "utf-8", "replace")
                    print()
                    print("  --- 原始消息 %d (%.1fs, %d 字节) ---"
                          % (seen, time.time() - t0, len(s)))
                    try:
                        j = json.loads(s)
                        print("  顶层键: %s" % list(j.keys()))
                        for k, v in j.items():
                            if isinstance(v, str):
                                print("    %-24s str  %r" % (k, v[:100]))
                            elif isinstance(v, list):
                                print("    %-24s list len=%d" % (k, len(v)))
                                if v and isinstance(v[0], dict):
                                    print("        元素键: %s" % list(v[0].keys()))
                                    print("        首元素: %s"
                                          % json.dumps(v[0], ensure_ascii=False)[:220])
                            elif isinstance(v, dict):
                                print("    %-24s dict %s"
                                      % (k, json.dumps(v, ensure_ascii=False)[:160]))
                            else:
                                print("    %-24s %r" % (k, v))
                    except Exception as e:
                        print("  非 JSON (%s): %r" % (e, s[:200]))

        task = asyncio.create_task(reader())
        step = int(0.02 * TARGET_RATE)
        sent = 0
        while sent < len(AUDIO):
            pos = sent / TARGET_RATE
            now = time.time() - t0
            if pos > now:
                await asyncio.sleep(0.01)
                continue
            blk = AUDIO[sent:sent + step]
            # 发 int16（s16le）
            await ws.send(np.clip(blk * 32767.0, -32768, 32767)
                          .astype(np.int16).tobytes())
            sent += step
        await asyncio.sleep(8.0)
        task.cancel()

asyncio.run(main())
print()
print("  完成")
