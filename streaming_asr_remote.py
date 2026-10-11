# -*- coding: utf-8 -*-
"""streaming_asr_remote.py —— 真流式识别（阿里云百炼 WebSocket）

协议（从真实消息序列抓出来的，见 samples/streaming_msgs.json）
============================================================
端点  wss://dashscope.aliyuncs.com/api-ws/v1/inference
流程  run-task（JSON）→ 二进制 PCM 帧 → finish-task（JSON）
模型  qwen-audio-3.0-asr-flash-streaming

消息结构（关键字段在 payload.output.output.sentence）：
    sentence_id     句子编号，递增
    text            该句**当前完整文本**（逐步修正，基本只增不减）
    sentence_begin  true = 新句开始
    sentence_end    true = 该句定稿
    begin_time / end_time   毫秒

**这就是防抖动的依据**：不需要我自己猜哪些部分结果该显示。
    · text 是完整句文本，不是碎片 —— 直接替换显示即可，不会跳
    · sentence_end=true 是定稿信号 —— 只在此时触发翻译，
      避免同一句被翻译好几次（那会让字幕闪）
    · begin_time/end_time 给了真实时间轴，可以直接算延迟

实测性能（_probe_streaming2.py，不节流）：
    16.4 秒音频上传用时 0.28s（58× 实时）
    首字 0.08s、整段转写完 3.12s
    真实麦克风速率下处理延迟为负（上传比识别快）→ 无等待

为什么不用非流式 + 小批次：那方案要等批次攒齐（1.5s 批 → 平均等
0.75s），流式没有这一段。
"""
from __future__ import annotations

import json
import os
import threading
import time

DASHSCOPE_KEYS_FILE = os.path.join(os.path.expanduser("~"), ".dsh",
                                   ".dashscope")
URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
MODEL = "qwen-audio-3.0-asr-flash-streaming"
TARGET_RATE = 16000


def read_key(path=DASHSCOPE_KEYS_FILE):
    try:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#"):
                return line
    except Exception:
        pass
    return ""


def parse_message(j):
    """从一条 result-generated 里取出 (sentence_id, text, is_begin, is_end)。

    **字段在 payload.output.output.sentence** —— 我第一次读成了
    payload.output.sentence，结果永远拿到空值（真实序列里是双层嵌套）。
    保险起见两层都试。
    """
    if j.get("header", {}).get("event") != "result-generated":
        return None
    o = (j.get("payload") or {}).get("output") or {}
    s = ((o.get("output") or {}).get("sentence")
         or o.get("sentence") or {})
    if not s:
        return None
    return (s.get("sentence_id"), (s.get("text") or ""),
            bool(s.get("sentence_begin")), bool(s.get("sentence_end")),
            s.get("begin_time"), s.get("end_time"))


class MergeState:
    """流式结果的合并状态机。**纯逻辑、不碰网络，所以能离线测。**

    规则（全部有真实数据依据）：
      1. 同一个 sentence_id 的 text 直接覆盖 —— 它是完整句文本，
         所以显示上是"这句越来越完整"，不是闪烁
      2. 只有 sent_end=True 才产出"可翻译"的定稿
      3. sentence_begin=True 且上一句还没定稿 → 说明服务器已经翻页，
         把上一句按当前 text 收尾（不能丢，否则那句话永远不显示）
      4. 定稿去重：同一个 (sid, text) 不重复产出
    """

    def __init__(self):
        self.cur_sid = None
        self.cur_text = ""
        self.emitted = set()          # 已定稿的 (sid, text) 防重复
        self.n_partial = 0
        self.n_final = 0

    def feed(self, sid, text, is_begin, is_end, begin_t=None, end_t=None):
        """吃一条消息，返回 (partial_text, final_text)：
             partial_text  有值时表示"显示这个"（可能是未定稿的）
             final_text    有值时表示"这句定稿了，去翻译"
        """
        partial = final = ""

        # ③ 翻页：上一句没收到 end 就来了新句的开始 → 收尾上一句
        if is_begin and self.cur_sid is not None and self.cur_sid != sid:
            if self.cur_text:
                final = self.cur_text
                self.emitted.add((self.cur_sid, self.cur_text))
                self.n_final += 1
            self.cur_sid, self.cur_text = sid, ""

        if sid is None:
            return partial, final

        if sid != self.cur_sid:
            self.cur_sid, self.cur_text = sid, ""

        if text:
            self.cur_text = text
            partial = text
            self.n_partial += 1

        # ② 定稿信号
        if is_end and self.cur_text:
            if (sid, self.cur_text) not in self.emitted:
                final = self.cur_text
                self.emitted.add((sid, self.cur_text))
                self.n_final += 1
            self.cur_sid, self.cur_text = None, ""

        return partial, final

    def flush(self):
        """收尾：连接结束时把没定稿的句子交出来。"""
        if self.cur_text and (self.cur_sid, self.cur_text) not in self.emitted:
            self.emitted.add((self.cur_sid, self.cur_text))
            self.n_final += 1
            out = self.cur_text
            self.cur_sid, self.cur_text = None, ""
            return out
        return ""


def merge_from_captured(path):
    """离线验证：拿抓下来的真实消息序列跑一遍合并逻辑。

    返回 (partials, finals)。实测统计（30 条真实消息、3 句话）：
        增长 23 次　改写 1 次　不变 0 次
    唯一的改写是识别自我纠错（"key" → "get"，长度 3→3）。
    所以文本基本**只增不减**，跨句才重置 —— 这决定了显示策略：
        部分结果照显示（给即时感），但**只有定稿才翻译**，
        于是中文那行稳定不跳，跳的只是下方原文、而且是在变完整。
    """
    msgs = json.load(open(path, encoding="utf-8"))
    st = MergeState()
    partials, finals = [], []
    for j in msgs:
        p = parse_message(j)
        if not p:
            continue
        sid, text, beg, end, bt, et = p
        pt, ft = st.feed(sid, text, beg, end, bt, et)
        if pt:
            partials.append((j.get("_t"), sid, pt))
        if ft:
            finals.append((j.get("_t"), sid, ft))
    tail = st.flush()
    if tail:
        finals.append((None, None, tail))
    return partials, finals


def revision_stats(partials):
    """统计部分结果是"增长/改写/不变"各多少次。

    为什么单独一个函数：这是判断**会不会抖动**的量化依据，
    也是自检要断言的东西（不能只靠肉眼看）。
    """
    cur, prev = None, ""
    grow = rewrite = same = 0
    for _t, sid, txt in partials:
        if sid != cur:
            cur, prev = sid, ""
        if len(txt) > len(prev):
            grow += 1
        elif txt != prev:
            rewrite += 1
        else:
            same += 1
        prev = txt
    return grow, rewrite, same


class StreamingRemoteASR:
    """真流式：一个后台线程管连接与收发，主线程只读结果。

    线程模型（和主程序的分块路线一致，都是"生产者线程 + 主线程读"）：
        后台线程  连接 → run-task → 持续发 PCM 帧 → 收消息 → 喂 MergeState
        主线程    feed(音频) 塞进队列；take_partial()/take_final() 取结果

    为什么要重连：WebSocket 是长连接，网络抖动、服务端限时都会断。
    断了不能整个字幕停掉 —— 标记 dead，由调用方决定退回非流式。
    """

    def __init__(self, key=None, language=None, frame_ms=100, timeout=20.0,
                 on_partial=None, on_final=None):
        self.key = key or read_key()
        self.language = language
        self.frame_ms = int(frame_ms)
        self.timeout = float(timeout)
        self.on_partial = on_partial
        self.on_final = on_final
        self.ok = bool(self.key)
        self.dead = False
        self.last_err = ""
        self.state = MergeState()
        self._q = []                 # 待发送的 float32 片段
        self._lock = threading.Lock()
        self._pending = b""          # 不足一帧的余量（16bit PCM 字节）
        self._ws = None
        self._thread = None
        self.n_frames = 0
        self.t_connect = 0.0
        self.t_first_text = None
        self.t0 = None

    # ── 主线程接口 ───────────────────────────────────────────
    def start(self):
        if not self.ok:
            return False
        self.t0 = time.time()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def feed(self, pcm_f32):
        """喂一段 float32 [-1,1] 单声道音频（16k）。非阻塞。"""
        if self.dead or not self.ok or pcm_f32 is None or len(pcm_f32) == 0:
            return
        with self._lock:
            self._q.append(pcm_f32)

    def stop(self):
        self.dead = True
        try:
            if self._ws:
                self._ws.close()
        except Exception:
            pass

    # ── 后台线程 ─────────────────────────────────────────────
    def _run(self):
        try:
            import websocket
        except ImportError:
            self.last_err = "没有 websocket-client"
            self.dead = True
            return
        import numpy as np
        try:
            self._ws = websocket.create_connection(
                URL, header=[f"Authorization: bearer {self.key}"],
                timeout=self.timeout)
        except Exception as e:
            self.last_err = f"连接失败 {type(e).__name__}: {str(e)[:100]}"
            self.dead = True
            return
        self.t_connect = time.time() - self.t0
        task = {"header": {"action": "run-task", "task_id": "ls-stream",
                           "streaming": "duplex"},
                "payload": {"task_group": "audio", "task": "asr",
                            "function": "recognition", "model": MODEL,
                            "parameters": {"format": "pcm",
                                           "sample_rate": TARGET_RATE},
                            "input": {}}}
        if self.language:
            task["payload"]["parameters"]["language_hints"] = [self.language]
        try:
            self._ws.send(json.dumps(task, ensure_ascii=False))
        except Exception as e:
            self.last_err = f"run-task 失败: {str(e)[:100]}"
            self.dead = True
            return

        frame_bytes = int(TARGET_RATE * self.frame_ms / 1000) * 2
        last_send = time.time()
        while not self.dead:
            # ① 发音频
            with self._lock:
                chunks, self._q = self._q, []
            if chunks:
                try:
                    a = np.concatenate(chunks)
                except Exception:
                    a = None
                if a is not None and len(a):
                    pcm = (np.clip(a, -1, 1) * 32767).astype("<i2").tobytes()
                    self._pending += pcm
                    while len(self._pending) >= frame_bytes:
                        buf = self._pending[:frame_bytes]
                        self._pending = self._pending[frame_bytes:]
                        try:
                            self._ws.send_binary(buf)
                            self.n_frames += 1
                            last_send = time.time()
                        except Exception as e:
                            self.last_err = f"发送失败: {str(e)[:90]}"
                            self.dead = True
                            break
            if self.dead:
                break
            # ② 收消息（短超时，别挡住发送）
            try:
                self._ws.settimeout(0.02)
                m = self._ws.recv()
            except Exception:
                m = None
            if isinstance(m, (str, bytes)) and m:
                if isinstance(m, bytes):
                    continue
                try:
                    j = json.loads(m)
                except Exception:
                    continue
                ev = j.get("header", {}).get("event")
                if ev == "task-finished":
                    self.dead = True
                    break
                p = parse_message(j)
                if p:
                    sid, text, beg, end, bt, et = p
                    if text and self.t_first_text is None:
                        self.t_first_text = time.time() - self.t0
                    pt, ft = self.state.feed(sid, text, beg, end, bt, et)
                    if pt and self.on_partial:
                        try:
                            self.on_partial(pt)
                        except Exception:
                            pass
                    if ft and self.on_final:
                        try:
                            self.on_final(ft)
                        except Exception:
                            pass
            # ③ 长时间没有再送音频也无消息 → 认为连接死了
            if not chunks and time.time() - last_send > 30:
                self.dead = True
                self.last_err = "空闲超时"
        try:
            self._ws.close()
        except Exception:
            pass

    def status(self):
        if not self.ok:
            return "流式=无 key"
        return (f"流式={MODEL} 帧 {self.n_frames} 部分 "
                f"{self.state.n_partial} 定稿 {self.state.n_final}"
                f" 首字 "
                f"{('%.2fs' % self.t_first_text) if self.t_first_text else '—'}"
                f"{' 已断开' if self.dead else ''}")
