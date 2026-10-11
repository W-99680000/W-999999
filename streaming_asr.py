#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
streaming_asr —— 流式识别（LocalAgreement-2），按同传论文的做法
==================================================================

来历
----
操作者要求解决实时性。去查同传 / 实时字幕的做法，找到
[Macháček et al. 2023, "Turning Whisper into Real-Time Transcription System"](https://aclanthology.org/2023.ijcnlp-demo.3.pdf)
（Whisper-Streaming，CUNI，3.3 秒延迟）。

机制 **LocalAgreement-2**（Liu et al. 2020）：
    每次更新把新音频接到缓冲末尾，重跑整个缓冲；
    取「本次全文」与「上次全文」的**最长公共前缀** —— 那部分才"确认"。
    确认过的部分下次不再重复输出。

本机实测与由此推出的设计约束
----------------------------
【实测一】每次更新耗时（bench_localagree.py）
    缓冲 0.5s → 1.46s      缓冲 8.0s → 1.61s
    缓冲 2.0s → 1.38s      缓冲 12.0s → 1.80s
  → **每次更新耗时几乎恒定（1.3–1.9s），与缓冲长短几乎无关**（whisper 1.08s 固定开销主导）

【实测二】**CPU 上处理能力追不上实时**
    1.9s / 次  →  0.53 音频秒/秒
    而音频按 1.0 秒/秒 进来  →  **缓冲必然持续增长**
  论文能到 3.3 秒延迟，是因为它跑在 **NVIDIA A40 GPU（RTF≈0.1）**；
  本机无 GPU（cuBLAS 缺失，实测），只能吃这个约束。
  **所以本实现不假装能追平实时，而是把"追不上"的后果限制在一个固定窗口内。**

【据此定的三条设计】
  ① **更新按"距上次多久"触发，不按"攒够多少音频"触发。**
     第一版按 MinChunkSize 攒音频触发，实测 16.4s 音频只跑 3 次更新、
     墙钟 6.5s 就结束 —— 大量音频被丢。这是真 bug。
  ② **已确认词单独保存，不随缓冲裁剪丢失。**
     用字级时间戳把音频裁到"最后已确认词"之后；已确认的文本留在
     stable_words 里，下一轮比对只针对未确认部分。
     （第二版没保存，裁完就把首句丢了 —— 实测 "never thought" 消失。）
  ③ **未确认部分留一个固定观察窗（tail_seconds）。**
     因为处理追不上输入，未确认尾巴会一直涨；给它设上限，
     超出的部分交给"近似已确认"兜底，避免缓冲无限增长。

显示分两级：**稳定（已确认）** + **局部（本次全文里尚未确认的尾部）**。
首字延迟 ≈ 一次更新耗时，不需要等两次一致。

与原文的差异（如实标注）
------------------------
· 原文：large-v2 + A40 GPU + 30 秒缓冲 + 1~5 gram 跳过匹配
· 本实现：small + CPU + 固定窗口缓冲 + 按词位置对齐
· 原文能达到 3.3 秒延迟；本机受 CPU 限制，延迟下限 ≈ 一次更新（1.4–1.9 秒），
  但**尾部会落后于实时**，这是硬件决定的，不是实现问题
"""
from __future__ import annotations

import re
import threading
import time

import numpy as np

RATE = 16000

SENT_END = re.compile(r"[.!?。！？…]['\"”’)]?\s*$")
NORM = re.compile(r"[^\w\s']", re.UNICODE)


def _words(s):
    return [w for w in NORM.sub(" ", (s or "").lower()).split() if w]


def common_prefix_words(a, b):
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


class StreamingTranscriber:
    """LocalAgreement-2 流式识别（CPU 友好版）。

        st = StreamingTranscriber(language="en")
        st.feed(chunk)                  # 随时喂 float32/16k
        stable, partial = st.state()    # 稳定文本 / 局部文本
        for inc in st.poll(): ...       # 新确认的增量
    """

    def __init__(self, model="small", threads=4, language="en",
                 update_interval=1.2, tail_seconds=3.0,
                 max_buffer=12.0, min_buffer=1.2,
                 on_update=None, vad_filter=False):
        from faster_whisper import WhisperModel
        self.m = WhisperModel(model, device="cpu", compute_type="int8",
                              cpu_threads=threads)
        self.language = language
        self.update_interval = float(update_interval)
        self.tail_seconds = float(tail_seconds)   # 未确认部分保留多久音频
        self.max_buffer = float(max_buffer)
        self.min_buffer = float(min_buffer)
        self.on_update = on_update
        self.vad_filter = vad_filter

        self.lock = threading.Lock()
        self.buf = np.zeros(0, dtype=np.float32)
        # 已确认（跨缓冲裁剪保留）
        self.base_words = []            # 交付给下一轮的「已确认」词（比对基线）
        self.buf_stable = []            # **当前缓冲内**已确认的词（裁剪时用它算切点）
        self.stable_text = ""           # 累计已确认文本（带标点）
        self.out_everything = ""        # 全部输出（调试用）
        # 本次
        self.hyp_text = ""
        self.hyp_words = []
        self.prev_words = []            # 上轮"未确认部分"的词
        self.last_run = 0.0
        self.out_queue = []
        self.running = True
        self.stats = {"updates": 0, "t_upd": 0.0, "chars": 0, "trims": 0,
                      "errors": [], "buf_sec": 0.0, "started": time.time()}
        self._th = threading.Thread(target=self._loop, daemon=True)
        self._th.start()

    # ── 音频入口 ─────────────────────────────────────────────
    def feed(self, x):
        if x is None or len(x) == 0:
            return
        x = np.asarray(x, dtype=np.float32)
        with self.lock:
            self.buf = np.concatenate([self.buf, x])

    # ── 输出 ─────────────────────────────────────────────────
    def state(self):
        """(稳定文本, 局部文本)。局部 = 本次全文里尚未确认的尾部。"""
        with self.lock:
            stable, hyp = self.stable_text, self.hyp_text
        hw = self.hyp_words
        sw = len(self.base_words) + len(self.buf_stable)
        toks = re.findall(r"\S+", hyp)
        partial = " ".join(toks[sw:]) if len(toks) > sw else ""
        return stable, partial

    def poll(self):
        with self.lock:
            out, self.out_queue = self.out_queue, []
        return out

    def flush(self):
        with self.lock:
            buf = self.buf
        if len(buf) / RATE >= 0.25:
            try:
                hyp, words = self._run(buf)
                hw = _words(hyp)
                with self.lock:
                    self.hyp_text, self.hyp_words = hyp, hw
                    base_n = len(self.base_words) + len(self.buf_stable)
                    new = " ".join(hw[base_n:])
                    if new:
                        self.buf_stable = hw[base_n:]
                        self.stable_text = (self.stable_text + " " + new).strip()
                        self.out_everything = (self.out_everything + " " + new).strip()
                        self.out_queue.append(new)
            except Exception as e:
                self.stats["errors"].append(f"flush {type(e).__name__}: {e}")
        return self.poll()

    def stop(self):
        self.running = False
        try:
            self._th.join(timeout=3)
        except Exception:
            pass

    close = stop

    # ── 内部 ─────────────────────────────────────────────────
    def _run(self, audio):
        """开字级时间戳：论文用它精确裁缓冲。"""
        segs, info = self.m.transcribe(
            audio, language=self.language, beam_size=5,
            vad_filter=self.vad_filter, condition_on_previous_text=False,
            word_timestamps=True)
        segs = list(segs)
        txt = " ".join(s.text.strip() for s in segs).strip()
        ws = []
        for s in segs:
            for w in (getattr(s, "words", None) or []):
                ws.append((w.word.strip(), float(w.start), float(w.end)))
        return txt, ws

    def _cut_buffer(self, cut_sec, n_confirmed_in_buf=0):
        """切掉缓冲头部的 cut_sec 秒。

        **必须同时处理已确认词的归属**：被切掉的那部分音频里已经确认过的词，
        要从 buf_stable 移到 base_words。否则下一轮的比对基线会错位
        （我第三版就是这里错：全局累计数大于缓冲词数，裁剪条件永远不成立，
         实测缓冲从 2.6s 一路涨到 15.3s）。
        """
        n = int(max(0.0, cut_sec) * RATE)
        with self.lock:
            if not (0 < n < len(self.buf)):
                return False
            self.buf = self.buf[n:]
            self.stats["trims"] += 1
        # 把已确认的词移进 base（这些词对应的音频已被切走）
        moved = self.buf_stable[:n_confirmed_in_buf]
        rest = self.buf_stable[n_confirmed_in_buf:]
        with self.lock:
            self.base_words = self.base_words + moved
            self.buf_stable = rest
        return True

    def _loop(self):
        while self.running:
            wait = self.update_interval - (time.time() - self.last_run)
            if wait > 0:
                time.sleep(min(0.05, wait))
                continue
            with self.lock:
                buf = self.buf
                buf_sec = len(buf) / RATE
            if buf_sec < self.min_buffer:
                time.sleep(0.02)
                continue

            self.last_run = time.time()
            t0 = time.time()
            try:
                hyp, words = self._run(buf)
            except Exception as e:
                self.stats["errors"].append(f"{type(e).__name__}: {e}")
                time.sleep(0.2)
                continue
            el = time.time() - t0
            self.stats["updates"] += 1
            self.stats["t_upd"] += el
            self.stats["buf_sec"] += buf_sec

            hw = _words(hyp)
            with self.lock:
                self.hyp_text, self.hyp_words = hyp, hw

            # ── LocalAgreement-2，只针对**当前缓冲内未确认的部分** ──
            # 基线 = 上轮交付的已确认词 + 本缓冲已确认词
            with self.lock:
                base_n = len(self.base_words) + len(self.buf_stable)
            cur_tail = hw[base_n:] if len(hw) > base_n else []
            n = common_prefix_words(cur_tail, self.prev_words)
            self.prev_words = cur_tail
            newly = cur_tail[:n] if n else []

            if newly:
                with self.lock:
                    self.buf_stable = self.buf_stable + newly
                    grew = " ".join(newly)
                    self.out_queue.append(grew)
                    self.out_everything = (self.out_everything + " " + grew).strip()
                    self.stats["chars"] += len(grew)
                    # 累计稳定文本：从本次全文按词数取
                    toks_all = re.findall(r"\S+", hyp)
                    keep = len(self.base_words) + len(self.buf_stable)
                    new_stable = " ".join(toks_all[:keep])
                    if len(new_stable) > len(self.stable_text):
                        self.stable_text = new_stable
                if self.on_update:
                    try:
                        self.on_update(*self.state())
                    except Exception:
                        pass

                # ── 裁缓冲：把「当前缓冲内已确认的词」之前的音频切掉 ──
                # 关键：用 **buf_stable**（当前缓冲内的已确认词数），
                # 不能用全局累计 —— 裁剪后全局数会大于缓冲词数，条件永远不成立。
                nconf = len(self.buf_stable)
                if words and 0 < nconf <= len(words):
                    end_t = words[nconf - 1][2]
                    cut = end_t - self.tail_seconds
                    if cut > 0:
                        self._cut_buffer(cut, nconf)

            # ── 硬上限：保证延迟有界 ─────────────────────────────
            # 实测（bench_trend.py，60 秒长跑，每 2 秒采样）缓冲呈锯齿：
            #   tiny: 8 12 17 24 31 →10 18     base: 8 13 18 26 29 35 →12
            # 裁剪是在工作，但阈值宽松时缓冲会长到 30 秒，每次更新也随之变慢。
            # 这里强制压回 tail_seconds 以内。
            # 代价：可能切掉还没确认的音频（漏字）—— 所以上限设得比 tail 大，
            # 只有"确认跟不上"时才真正触发。
            with self.lock:
                cur = len(self.buf) / RATE
            if cur > self.max_buffer:
                self._cut_buffer(cur - self.tail_seconds,
                                 min(len(self.buf_stable), 10 ** 9))

    def trim_to(self, seconds):
        with self.lock:
            n = int(max(0.5, seconds) * RATE)
            if len(self.buf) > n:
                self.buf = self.buf[-n:]
                self.stats["trims"] += 1


def selftest():
    import os
    import sys
    import wave
    ok = fail = 0

    def chk(n, c):
        nonlocal ok, fail
        if c:
            ok += 1; print(f"  ok   {n}")
        else:
            fail += 1; print(f"  FAIL {n}")

    print("一、纯函数")
    chk("最长公共前缀", common_prefix_words(["a", "b", "c"], ["a", "b", "d"]) == 2)
    chk("无公共前缀", common_prefix_words(["a"], ["b"]) == 0)
    chk("一边为空", common_prefix_words([], ["a"]) == 0)
    chk("归一化去标点", _words("Hello, world!") == ["hello", "world"])
    chk("标点不影响比对", common_prefix_words(_words("I never,"), _words("I never.")) == 2)
    chk("句末标点识别", bool(SENT_END.search("see you again.")))
    chk("中文句末标点", bool(SENT_END.search("再见到你。")))
    chk("句中不误判", not SENT_END.search("I never thought"))

    HERE = os.path.dirname(os.path.abspath(__file__))
    wav = os.path.join(HERE, "e2e_speech.wav")
    if not os.path.exists(wav):
        print("     （没有 e2e_speech.wav，跳过）")
        print(f"\n合计 {ok} 通过 / {fail} 失败")
        return 0 if fail == 0 else 1

    sys.path.insert(0, HERE)
    from live_subtitle_v2 import to_mono16k
    w = wave.open(wav, "rb")
    raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    audio = to_mono16k(raw, w.getframerate(), w.getnchannels())
    adur = len(audio) / RATE

    print("\n二、流式识别（按真实速度喂，不是 sleep 攒批）")
    st = StreamingTranscriber(model="small", threads=4, language="en",
                              update_interval=1.0, tail_seconds=3.0,
                              min_buffer=1.2, max_buffer=12.0)
    t0 = time.time()
    i = 0
    CH = int(RATE * 0.2)
    events = []
    while i < len(audio):
        target = t0 + i / RATE
        if time.time() < target:
            time.sleep(min(0.05, target - time.time()))
        st.feed(audio[i:i + CH])
        i += CH
        for x in st.poll():
            events.append((time.time() - t0, x))
    for x in st.flush():
        events.append((time.time() - t0, x))
    el = time.time() - t0
    st.stop()

    avg = st.stats["t_upd"] / max(1, st.stats["updates"])
    avgbuf = st.stats["buf_sec"] / max(1, st.stats["updates"])
    print(f"     音频 {adur:.1f}s，墙钟 {el:.1f}s")
    print(f"     更新 {st.stats['updates']} 次，平均 {avg:.2f}s/次，"
          f"平均缓冲 {avgbuf:.1f}s，裁剪 {st.stats['trims']} 次")
    for ts, x in events[:8]:
        print(f"       [{ts:5.1f}s] + {x[:74]}")
    full = st.stable_text
    print(f"     稳定全文：{full[:150]}")

    chk("无异常", not st.stats["errors"])
    if st.stats["errors"]:
        for e in st.stats["errors"][:3]:
            print("        ", e)
    chk("更新次数 >= 4", st.stats["updates"] >= 4)
    chk("有多次增量输出（是流式）", len(events) >= 2)
    chk("平均每次更新 < 2.5s", avg < 2.5)
    chk("缓冲没无限增长（平均 < 8s）", avgbuf < 8.0)
    for kw in ("never", "thought", "again", "coffee"):
        chk(f"含关键词 {kw}", kw in full.lower())

    print(f"\n合计 {ok} 通过 / {fail} 失败")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    import sys
    sys.exit(selftest())
