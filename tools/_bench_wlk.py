# -*- coding: utf-8 -*-
"""实测 WhisperLiveKit：接 WebSocket，按真实速率喂音频，量落后多少。

服务已在 127.0.0.1:8199 跑着（--pcm-input，所以喂原始 PCM）。

协议（从它的 web UI/API 文档推断，实测确认）：
  ws://127.0.0.1:8199/asr?language=en
  二进制帧 = float32 单声道 16kHz 原始 PCM
  服务端回 JSON：{"lines":[...], "buffer_transcription":"...", ...}

判据：**同一个音频位置，多久之后字幕文本出现**（和之前几个脚本一致，
才能直接对比）。

对照基准（前面几轮实测，同素材、同机器）：
  我的分块路线（faster-whisper medium GPU）  落后中位 0.92s
  我的流式路线（LocalAgreement-2 GPU）        落后中位 0.30s（但译文碎）
"""
import asyncio
import io
import json
import os
import statistics
import sys
import time
import wave

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, "D:/NEW WORK/work/subtitle")

import numpy as np
from live_subtitle_v2 import to_mono16k, TARGET_RATE

HERE = "D:/NEW WORK/work/subtitle"


def load(rel, n):
    p = os.path.join(HERE, rel)
    w = wave.open(p, "rb")
    raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return np.tile(to_mono16k(raw, w.getframerate(), w.getnchannels()), n)


# ── 样本检查 ─────────────────────────────────────────────
# tests/e2e_speech.wav 随仓库发布（samples/ 下）。缺失就友好退出。
def _need_sample(p):
    """确认样本存在，否则给出提示后退出。"""
    if os.path.exists(p):
        return p
    print("跳过：缺少 " + p)
    print("本仓库自带 samples/e2e_speech.wav；请确认已完整 clone。")
    raise SystemExit(0)

A = load(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples", "e2e_speech.wav"), 3)
B = load("loopback_test.wav", 3)
n = min(len(A), len(B))
AUDIO = np.concatenate([A[:n], B[:n]])[: int(70 * TARGET_RATE)]
print("  素材 70 秒英文（识别干净）")
print("  AUDIO float32 %d 样本 = %.1f 秒" % (len(AUDIO), len(AUDIO) / TARGET_RATE))

import websockets


async def run(url, dur=70.0):
    events = []            # (音频位置, 文本, 到达时刻-音频位置)
    t0 = time.time()
    async with websockets.connect(url, max_size=None) as ws:
        async def reader():
            try:
                async for msg in ws:
                    now = time.time() - t0
                    try:
                        j = json.loads(msg)
                    except Exception:
                        continue
                    txt = ""
                    for key in ("buffer_transcription", "text", "transcription"):
                        v = j.get(key)
                        if isinstance(v, str) and v.strip():
                            txt = v.strip()
                            break
                    if not txt:
                        lines = j.get("lines") or []
                        parts = [l.get("text", "") for l in lines
                                 if isinstance(l, dict)]
                        txt = " ".join(p for p in parts if p).strip()
                    if txt:
                        events.append((now, txt, now))
            except Exception:
                pass

        task = asyncio.create_task(reader())
        step = int(0.02 * TARGET_RATE)      # 20ms 一块
        sent = 0
        while sent < len(AUDIO):
            pos = sent / TARGET_RATE
            now = time.time() - t0
            if pos > now:
                await asyncio.sleep(min(0.01, pos - now))
                continue
            blk = AUDIO[sent:sent + step]
            await ws.send(blk.astype(np.float32).tobytes())
            sent += step
        # 让服务端把剩余缓冲吐出来
        await asyncio.sleep(6.0)
        task.cancel()
    return events


async def main():
    url = "ws://127.0.0.1:8199/asr?language=en"
    print()
    print("=" * 80)
    print("WhisperLiveKit 实测")
    print("=" * 80)
    try:
        ev = await run(url)
    except Exception as e:
        print("  连接失败: %s: %s" % (type(e).__name__, str(e)[:120]))
        return
    print("  收到 %d 条文本更新" % len(ev))
    if not ev:
        print("  **没有收到文本**")
        return
    lags = [now for now, _t, _n in ev]
    print("  落后（相对音频位置）：首次 %.2fs　中位 %.2fs　最大 %.2fs"
          % (lags[0], statistics.median(lags), max(lags)))
    print()
    print("  前 12 条：")
    for now, txt, _ in ev[:12]:
        print("    t=%5.1fs  %s" % (now, txt[:70]))
    print()
    print("  最后 4 条：")
    for now, txt, _ in ev[-4:]:
        print("    t=%5.1fs  %s" % (now, txt[:70]))

asyncio.run(main())
