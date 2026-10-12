# -*- coding: utf-8 -*-
"""WhisperLiveKit 实测 v2 —— 字段名已确认，不再猜。

服务端真实协议（从原始消息读出，不是猜的）：
    {"type":"config", "useAudioWorklet":true, "mode":"full"}
    {"status":"no_audio_detected"|..., "lines":[...],
     "buffer_transcription":"...", "buffer_translation":"...",
     "remaining_time_transcription":..., ...}

  · lines              已定稿的行（list，元素是 dict）
  · buffer_transcription  正在识别、尚未定稿的文本
  · buffer_translation    翻译（服务端没开翻译时为空）

判据：**最终文本何时出现**。因为每 10ms 回一条消息
（1276 条 / 70 秒），不能按"消息到达"算延迟 ——
要按**文本内容变化**算，取最后一个 chunk 定稿的时刻。

对照（同素材、同机器、前面几轮实测）：
  我的分块路线（faster-whisper medium GPU）  落后中位 0.92s
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

import websockets


def lines_text(j):
    out = []
    for l in (j.get("lines") or []):
        if isinstance(l, dict):
            t = (l.get("text") or "").strip()
            if t:
                out.append(t)
    return " ".join(out)


async def run(dur):
    t0 = time.time()
    commits = []          # (时刻, 累计定稿文本, 新增)
    last_final = ""
    last_buf = ""
    async with websockets.connect("ws://127.0.0.1:8199/asr?language=en",
                                  max_size=None) as ws:
        async def reader():
            nonlocal last_final, last_buf
            async for msg in ws:
                now = time.time() - t0
                try:
                    j = json.loads(msg if isinstance(msg, str)
                                   else msg.decode("utf-8", "replace"))
                except Exception:
                    continue
                fin = lines_text(j)
                buf = (j.get("buffer_transcription") or "").strip()
                if fin != last_final and fin:
                    commits.append((now, fin, buf))
                    last_final = fin
                last_buf = buf
        task = asyncio.create_task(reader())
        step = int(0.02 * TARGET_RATE)
        sent = 0
        while time.time() - t0 < dur:
            pos = sent / TARGET_RATE
            now = time.time() - t0
            if pos <= now and sent < len(AUDIO):
                blk = AUDIO[sent:sent + step]
                await ws.send(np.clip(blk * 32767.0, -32768, 32767)
                              .astype(np.int16).tobytes())
                sent += step
            else:
                await asyncio.sleep(0.01)
        await asyncio.sleep(8.0)
        task.cancel()
    return commits, last_buf


async def main():
    print()
    print("=" * 84)
    print("WhisperLiveKit（--backend-policy localagreement，small 模型）")
    print("=" * 84)
    commits, lastbuf = await run(78.0)
    print("  定稿 %d 次" % len(commits))
    if not commits:
        print("  **没有定稿文本**")
        if lastbuf:
            print("  但 buffer 里有未定稿文本: %s" % lastbuf[:200])
        return
    print()
    print("  每次定稿（时刻 / 文本 / 当时未定稿缓冲）：")
    for now, fin, buf in commits:
        print("    t=%5.1fs  %s" % (now, fin[-72:]))
        if buf:
            print("              缓冲: %s" % buf[:60])
    print()
    # 最终文本 vs 参照
    final = commits[-1][1]
    REF = ("I never thought I would see you again. Get out of here before I "
           "call the police. The truth is, I have been lying to you since "
           "the day we met. Would you like a cup of coffee?")
    import difflib
    words = final.split()
    print("  最终文本 %d 词" % len(words))
    print("  %s" % final[:400])
    print()
    print("  与参照的相似度 %.3f（词级）"
          % difflib.SequenceMatcher(None, REF.lower().split(),
                                    final.lower().split()).ratio())

asyncio.run(main())
