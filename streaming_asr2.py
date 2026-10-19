# -*- coding: utf-8 -*-
"""streaming_asr2.py —— 滚动窗口 + 绝对时间戳的流式识别（替代方案）

为什么重写（原版 streaming_asr.py 的实测缺陷）
===============================================
原版按"当前缓冲"做 LocalAgreement，然后**裁掉缓冲头部**。实测（16.4 秒
素材，base 模型，连跑三次）关键词命中 4/8、4/8、5/8 —— **整句丢失，且丢哪
句不确定**。追踪到的最小复现：第一句已经确认、也进了 base_words，
但裁剪后的重新解码把它改写成了第二句。

根因是结构性的：LocalAgreement 的"确认"是相对当前缓冲做的；裁掉头部后
模型面对一个没有前文的中途片段，会重新切分并改写那块区域 ——
**之前那次确认就不作数了**。完全不裁又会让缓冲无限增长（实测 60 秒涨到
59s，max_buffer 因为整条裁剪路径被跳过而不触发）。

本文件的做法：滚动窗口 + 绝对时间戳
====================================
  · `self.buf` 是**窗口**，`self.win_start` 是它的绝对起始秒
  · 每次解码都对着**完整窗口**做，**绝不裁头**
  · 解码回来的词时间戳是窗口相对的 → 加 `win_start` 得到**绝对时间**
  · LocalAgreement 在**绝对时间**的词序列上做：已确认词 = 连续两轮
    假设的公共前缀。因为确认是绝对时间坐标，**滑窗口不会让它失效**
  · 滑窗口只在**足够靠后**的位置切：
        切点 <= 最小已确认词的起始绝对时间 - tail_seconds
    即被切走的音频，其内容**已被确认**，而且前面还留着 tail_seconds 的
    前文给模型做上下文 —— 所以重新解码不会改写它。

这样"模型看到的音频"永远是完整窗口（有前文），"确认"是绝对坐标，
丢句问题从结构上消失，而窗口长度由 max_window 管住，缓冲天然有界。
"""
from __future__ import annotations
import model_home  # noqa: E402,F401  # 先把模型目录钉到 D 盘（务必在 HF/faster-whisper 之前）

import re
import threading
import time

import numpy as np

RATE = 16000

SENT_END = re.compile(r"[.?!。！？…]['\")\]]*$")


def _text_words(s):
    """把文本切成可比较的词：小写、去掉尾部标点。"""
    out = []
    for w in re.findall(r"\S+", (s or "").lower()):
        c = w.strip(".,!?;:\"'()[]")
        if c:
            out.append(c)
    return out


def common_prefix_words(a, b):
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


class RollingStreamingTranscriber:
    """滚动窗口流式识别。接口与 StreamingTranscriber 保持一致：
       feed(x) / state() / poll() / flush() / stop() / stats
    """

    def __init__(self, model="small", threads=4, language="en",
                 update_interval=0.5, tail_seconds=3.0,
                 max_window=14.0, min_window=1.2, safety_margin=3.0,
                 on_update=None, vad_filter=True):
        from faster_whisper import WhisperModel
        self.m = WhisperModel(model, device="cpu", compute_type="int8",
                              cpu_threads=threads)
        self.language = language
        self.update_interval = float(update_interval)
        self.tail_seconds = float(tail_seconds)
        self.max_window = float(max_window)
        self.min_window = float(min_window)
        # 滑窗口时，切点还要比"最靠前的已确认词起点"再早这么多秒：
        # 被切走的音频内容已确认，前面再留 safety_margin 秒前文当上下文。
        self.safety_margin = float(safety_margin)
        self.vad_filter = bool(vad_filter)
        self.on_update = on_update

        self.lock = threading.RLock()
        self.buf = np.zeros(0, dtype=np.float32)   # 窗口音频
        self.win_start = 0.0                       # 窗口起始的绝对秒
        self.total_in = 0.0                        # 已喂入总音频秒
        # 已确认词（**绝对时间**）：[(word, start_abs, end_abs)]
        self.confirmed = []
        self.stable_text = ""
        self.out_everything = ""
        self.hyp_text = ""
        self.prev_words = []          # 上一轮"未确认部分"的词（绝对时间）
        self.out_queue = []
        self.running = True
        self.last_run = 0.0
        self.stats = {"updates": 0, "t_upd": 0.0, "chars": 0, "slides": 0,
                      "errors": [], "win_sec": 0.0, "win_max": 0.0,
                      "started": time.time(), "redecided": 0}
        self._th = threading.Thread(target=self._loop, daemon=True)
        self._th.start()

    # ── 音频入口 ─────────────────────────────────────────────
    def feed(self, x):
        if x is None or len(x) == 0:
            return
        x = np.asarray(x, dtype=np.float32)
        with self.lock:
            self.buf = np.concatenate([self.buf, x])
            self.total_in += len(x) / RATE

    # ── 输出 ─────────────────────────────────────────────────
    def state(self):
        with self.lock:
            stable, hyp, nconf = self.stable_text, self.hyp_text, len(self.confirmed)
        toks = re.findall(r"\S+", hyp)
        partial = " ".join(toks[nconf:]) if len(toks) > nconf else ""
        return stable, partial

    def poll(self):
        with self.lock:
            out, self.out_queue = self.out_queue, []
        return out

    def flush(self):
        """把窗口里剩下的音频最后解一次，交付能确认的部分。"""
        with self.lock:
            buf = self.buf
        if len(buf) / RATE >= 0.25:
            try:
                hyp, ws = self._run(buf)
                self._absorb(hyp, ws)
            except Exception as e:
                self.stats["errors"].append(f"flush {type(e).__name__}: {e}")
        return []

    def stop(self):
        self.running = False
        try:
            self._th.join(timeout=3)
        except Exception:
            pass

    close = stop

    # ── 内部 ─────────────────────────────────────────────────
    def _run(self, audio):
        """解码整窗。返回 (文本, [(词, 起, 止)])，**时间为窗口相对**。"""
        segs, info = self.m.transcribe(
            audio, language=self.language, beam_size=5,
            vad_filter=self.vad_filter, condition_on_previous_text=False,
            temperature=0.0, word_timestamps=True)
        segs = list(segs)
        txt = " ".join(s.text.strip() for s in segs).strip()
        ws = []
        for s in segs:
            for w in (getattr(s, "words", None) or []):
                ws.append((w.word.strip(), float(w.start), float(w.end)))
        return txt, ws

    def _to_abs(self, ws):
        """窗口相对时间戳 → 绝对时间。"""
        with self.lock:
            w0 = self.win_start
        return [(w, s + w0, e + w0) for (w, s, e) in ws]

    def _absorb(self, hyp, ws_rel):
        """把一轮假设吸收进来：做绝对时间对齐的 LocalAgreement，交付新词。"""
        hw = _text_words(hyp)                 # 文本词（比对用）
        ws_abs = self._to_abs(ws_rel)         # 词时间戳（滑窗口用）
        with self.lock:
            nconf = len(self.confirmed)
            win_start = self.win_start

        # 已确认词在绝对时间上覆盖到哪里 → 本轮假设里对应位置的词跳过
        cur = hw[nconf:] if len(hw) > nconf else []
        # 时间戳按顺序对齐到 cur（时间戳可能比文本词多/少，取前 len(cur) 个）
        # 已确认词数对应的时间戳偏移：取 ws_abs 中起点 >= 最后确认词结束时间的部分
        if nconf:
            last_end = self.confirmed[-1][2]
            ws_cur = [w for w in ws_abs if w[1] >= last_end - 0.05]
        else:
            ws_cur = ws_abs
        ws_cur = ws_cur[:len(cur)]

        n = common_prefix_words(cur, self.prev_words)
        self.prev_words = cur
        newly = cur[:n]
        newly_ws = ws_cur[:n]

        with self.lock:
            self.hyp_text = hyp
            if not newly:
                return
            self.confirmed.extend(newly_ws)
            grew = " ".join(newly)
            self.out_queue.append(grew)
            self.out_everything = (self.out_everything + " " + grew).strip()
            self.stats["chars"] += len(grew)
            # 稳定文本 = 已确认词（按绝对时间顺序），保持单调增长
            joined = " ".join(w for (w, _s, _e) in self.confirmed)
            if len(joined) > len(self.stable_text):
                self.stable_text = joined

        if self.on_update:
            try:
                self.on_update(*self.state())
            except Exception:
                pass
        self._maybe_slide()

    def _maybe_slide(self):
        """滑窗口：只在**已确认内容足够靠后**时切，绝不裁未确认音频。

        切点 = 最新已确认词的结束时间 - tail_seconds - safety_margin

        **必须用"最新"已确认词，不能用最早那个。** 我第一版写成
        `self.confirmed[0][1]`（最靠前的已确认词），结果切点永远停在
        视频开头那个词上、条件永不成立 —— 实测 75 秒素材窗口涨到 73s、
        滑动 0 次（完全失控）。已确认词是**累计列表**，第一个词永远不变，
        所以拿它算切点等于算了一个常数。
        """
        with self.lock:
            if len(self.buf) / RATE <= self.max_window:
                return
            if not self.confirmed:
                return
            last_conf_end = self.confirmed[-1][2]
        cut = last_conf_end - self.tail_seconds - self.safety_margin
        # **硬上限兜底**：如果确认速度跟不上音频流入，窗口会一直涨
        # （实测 75 秒素材涨到 47.5s，上限 14s）。这时就不能再等了 ——
        # 直接切到"已确认末尾 - tail"，宁可少留一点前文，也要把窗口压住。
        # 代价：前文变短，模型改写该区域的风险上升（可能丢这份内容）。
        # 但窗口无限增长会让单次解码越来越慢，最终跟不上实时 —— 那更糟。
        with self.lock:
            win = len(self.buf) / RATE
        if cut <= 0.2 and win > self.max_window * 1.5:
            cut = last_conf_end - self.tail_seconds
            if cut <= 0.2:
                # 连"已确认末尾 - tail"都 <= 0：说明确认几乎没进展。
                # 只能按窗口上限强切（丢未确认音频，但保住实时性）。
                cut = win - self.max_window
        if cut <= 0.2:
            return
        n = int(cut * RATE)
        with self.lock:
            if not (0 < n < len(self.buf)):
                return
            self.buf = self.buf[n:]
            self.win_start += n / RATE
            self.stats["slides"] += 1
            # 切走的音频对应的已确认词时间戳已经交付过，保留在 confirmed 里
            # （它们是绝对坐标，滑窗口不影响）。

    def _loop(self):
        while self.running:
            wait = self.update_interval - (time.time() - self.last_run)
            if wait > 0:
                time.sleep(min(0.05, wait))
                continue
            with self.lock:
                buf = self.buf
                win_sec = len(buf) / RATE
            if win_sec < self.min_window:
                time.sleep(0.02)
                continue

            self.last_run = time.time()
            t0 = time.time()
            try:
                hyp, ws = self._run(buf)
            except Exception as e:
                self.stats["errors"].append(f"{type(e).__name__}: {e}")
                time.sleep(0.2)
                continue
            el = time.time() - t0
            self.stats["updates"] += 1
            self.stats["t_upd"] += el
            self.stats["win_sec"] += win_sec
            if win_sec > self.stats["win_max"]:
                self.stats["win_max"] = win_sec

            self._absorb(hyp, ws)
