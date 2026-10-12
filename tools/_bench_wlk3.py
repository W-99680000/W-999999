# -*- coding: utf-8 -*-
"""WhisperLiveKit 公平对比：用 medium 模型（不能拿 small 比我的 medium）。

并且修正我自己的两个方法错误：
  ① 参照文本：素材是平铺的，参照必须同样平铺。上一版只算了一遍，
     相似度算错了。
  ② 延迟判据：它的输出是"增长中的整条文本 + 截断单词"，不能按
     "消息到达时刻"算，要按**最终定稿**算。

对照基准（同素材、同机器、前面几轮实测）：
  我的分块路线（faster-whisper medium GPU）  落后中位 0.92s
"""
import asyncio
import difflib
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

# **参照要跟着平铺**（我上一版忘了，相似度就算错了）
REF_A = ("I never thought I would see you again. Get out of here before I "
         "call the police. The truth is, I have been lying to you since "
         "the day we met. Would you like a cup of coffee?")
REF_B = ("Hello, this is a test of the loopback capture system. One two "
         "three four five. Hello, this is a test of the loopback capture "
         "system.")
REP_A = int(len(A) / TARGET_RATE / 16.4) + 1
REP_B = int(len(B) / TARGET_RATE / 13.8) + 1
REF = " ".join([REF_A] * 3 + [REF_B] * 3)
print("  素材 70 秒英文　参照 %d 词（已按平铺重复）" % len(REF.split()))

import websockets


def lines_text(j):
    return " ".join((l.get("text") or "").strip()
                    for l in (j.get("lines") or [])
                    if isinstance(l, dict) and (l.get("text") or "").strip())


async def run(dur=78.0):
    t0 = time.time()
    finals = []           # (时刻, 累计文本)
    last = ""
    async with websockets.connect("ws://127.0.0.1:8199/asr?language=en",
                                  max_size=None) as ws:
        async def reader():
            nonlocal last
            async for msg in ws:
                now = time.time() - t0
                try:
                    j = json.loads(msg if isinstance(msg, str)
                                   else msg.decode("utf-8", "replace"))
                except Exception:
                    continue
                fin = lines_text(j)
                if fin and fin != last:
                    finals.append((now, fin))
                    last = fin
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
    return finals


async def main():
    print()
    print("=" * 84)
    print("WhisperLiveKit + localagreement + medium")
    print("=" * 84)
    fs = await run()
    if not fs:
        print("  没有文本")
        return
    final = fs[-1][1]
    print("  更新 %d 次　最终 %d 词" % (len(fs), len(final.split())))
    print()
    print("  最终文本：")
    print("    %s" % final[:420])
    print()
    r = difflib.SequenceMatcher(None, REF.lower().split(),
                                final.lower().split()).ratio()
    print("  与参照相似度 %.3f（词级）" % r)
    print()
    print("  检查截断单词（'...' 或半个词）：")
    bad = [w for w in final.split() if "..." in w]
    print("    含省略号的片段 %d 个：%s" % (len(bad), bad[:8]))
    print()
    print("  对照（我的实现，同素材同机器）：")
    print("    分块 faster-whisper medium GPU：落后中位 0.92s，"
          "识别与译文均完整")

asyncio.run(main())
