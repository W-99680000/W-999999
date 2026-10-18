# -*- coding: utf-8 -*-
"""remote_asr.py —— 远端语音识别（阿里云百炼 qwen3-asr-flash）

为什么做这个
============
本地 CPU 上 whisper 的延迟已经量到天花板（见 _bench_bottleneck.py）：

    一次 transcribe 的固定成本 ≈ 2.0 秒，**与音频长短无关**
        0.2 秒音频 → 2.078s
        0.5 秒音频 → 1.996s
        8.0 秒音频 → 2.288s
    参数 / 线程 / compute_type 全动不了它（±3%）
    beam 5→1、关 VAD、换 compute_type 都是噪声级
    唯一有效的是换小模型：small 2.363s → base 0.741s，但
    越南语一致率只有 0.53（把字幕变成胡话，不能接受）

所以本地延迟下限 = 2.0s（模型前向） + 1.2s（等句子说完） ≈ 3.2s，
实测中位 3.91s。

远端实测（同一批素材）：
    qwen3-asr-flash  越南语 12 秒 → 1.12s
    qwen3-asr-flash  英语  16 秒 → 0.93s
    → 比本地快一倍，**而且一次能吃 12~16 秒音频**，
      于是"等句子说完"那段也能省掉。

协议细节（都是实测出来的，不是查文档猜的）
==========================================
本脚本只用一个形态：**OpenAI 兼容 /chat/completions，音频以
data:audio/wav;base64 放进 content**。实测过的其它形态：
    ① OpenAI 兼容 /audio/transcriptions (multipart)  → 404 / 连接被重置
    ③ 原生 multimodal-generation                     → 能用，1.06s
    ④ 原生 /services/audio/asr/transcription         → 400 要 url
形态③ 也通，但形态② 更通用（同一份代码能对接 OpenAI 官方 whisper-1、
Groq、以及各种中转），所以选 ②。

返回值里带两个有用的东西：
    annotations[0].language   → 自动判出的语种（vi / en / ru ...）
    annotations[0].emotion    → 情绪（neutral / angry ...），留着没用但有
"""
from __future__ import annotations

import base64
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.request

DASHSCOPE_KEYS_FILE = os.path.join(os.path.expanduser("~"), ".dsh",
                                   ".dashscope")
DASHSCOPE_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL = "qwen3-asr-flash"

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


def read_key(path=DASHSCOPE_KEYS_FILE):
    # ① 环境变量（app 启动时 key_store.apply_env() 会灌进来）
    k = (os.environ.get("DASHSCOPE_API_KEY") or "").strip()
    if k:
        return k
    # ② 后台「接口密钥」页保存的 key_store.json
    try:
        import key_store as _ak
        k = _ak.get("dashscope")
        if k:
            return k
    except Exception:
        pass
    # ③ 老位置 ~/.dsh/.dashscope（兼容已配置的用户）
    try:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#"):
                return line
    except Exception:
        pass
    return ""


# ── 错误分类 ────────────────────────────────────────────────────
# 借鉴华为 AICaptionComponent 的做法：它把失败分成不同错误码，
# 每种含义不同 ——
#     1012900010  AI caption service is busy       服务忙
#     1012900011  controller initialized failed    初始化失败
#     1012900012  Audio recognition failed         识别失败
# **我们原来四种错误一律当"无结果"**：欠费、限流、网络断、这段音频
# 真的没内容，全都走同一条路（丢掉这段字幕），既不重试也不回退。
# 那是"字幕消失"类故障的温床。
#
# 现在分三类，处理方式不同：
#   transient  临时故障（限流/网络/服务忙）→ 值得重试，重试不成继续试
#   permanent  不会自愈（欠费/鉴权/余额）→ 重试无用，**停用远端改走本地**
#   none       调用成功但这段没内容（静音/噪声）→ 正常情况，不该告警
TRANSIENT_CODES = {429, 500, 502, 503, 504}
PERMANENT_CODES = {401, 403}
PERMANENT_WORDS = ("arrearage", "insufficient", "balance", "quota",
                   "invalid api key", "incorrect api key", "not authorized",
                   "authentication")


def classify_error(err, http_code=None):
    """把错误归到 transient / permanent / none。

    err: last_err 字符串（形如 "HTTP 429: ..." 或 "URLError: ..."）
    """
    e = (err or "").lower()
    code = http_code
    if code is None:
        m = re.match(r"http\s+(\d+)", e)
        if m:
            code = int(m.group(1))
    if code in PERMANENT_CODES or any(w in e for w in PERMANENT_WORDS):
        return "permanent"
    if code in TRANSIENT_CODES:
        return "transient"
    if not e:
        return "none"            # 没有错误 = 调用成功但没内容
    # 没识别出 HTTP 码的网络类错误都算临时
    return "transient"


def pcm_to_wav_bytes(pcm_f32, rate=16000):
    """float32 [-1,1] 单声道 → 16bit PCM 的 wav 字节。

    远端 API 要文件头；本地 numpy 数组得先包成 wav。
    """
    import struct
    import numpy as np
    a = np.clip(np.asarray(pcm_f32, dtype=np.float32), -1.0, 1.0)
    pcm = (a * 32767.0).astype("<i2").tobytes()
    n = len(pcm)
    hdr = (b"RIFF" + struct.pack("<I", 36 + n) + b"WAVEfmt " +
           struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16) +
           b"data" + struct.pack("<I", n))
    return hdr + pcm


class RemoteASR:
    """一次调用转写一段音频。接口与本地 transcriber 对齐。"""

    def __init__(self, key=None, base=DASHSCOPE_BASE, model=MODEL,
                 timeout=30.0):
        self.key = key or read_key()
        self.base = (base or DASHSCOPE_BASE).rstrip("/")
        self.model = model
        self.timeout = float(timeout)
        self.ok = bool(self.key)
        self.calls = self.fails = 0
        self.t_last = 0.0
        self.last_lang = None          # 供主程序做语言路由
        self.last_emotion = None

    def __call__(self, audio, rate=16000):
        """audio: float32 numpy 数组 → (文本, 语种)。

        失败返回 ("", None)，并设置 self.last_err / last_kind：
            last_kind = "transient" | "permanent" | "none"
        调用方（BatchTranscriber）按类别决定**重试还是回退本地**。
        临时故障在这里内部重试一次（限流与网络抖动很常见，重试往往就成了）；
        只重试一次，再失败交给上层，不在这里拖时间。
        """
        if not self.ok or audio is None or len(audio) == 0:
            return "", None
        txt, lang = self._attempt(audio, rate)
        if txt or self.last_kind != "transient":
            return txt, lang
        time.sleep(0.25)          # 限流的恢复窗口通常很短
        self.retries += 1
        return self._attempt(audio, rate)

    def _attempt(self, audio, rate=16000):
        try:
            wav = pcm_to_wav_bytes(audio, rate)
        except Exception:
            self.last_kind = "none"
            return "", None
        uri = "data:audio/wav;base64," + base64.b64encode(wav).decode()
        body = json.dumps({
            "model": self.model,
            "messages": [{"role": "user", "content": [
                {"type": "input_audio",
                 "input_audio": {"data": uri, "format": "wav"}}]}],
            "stream": False,
        }, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.base + "/chat/completions", data=body,
            headers={"Authorization": "Bearer " + self.key,
                     "Content-Type": "application/json"})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout,
                                        context=_CTX) as r:
                j = json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            self.fails += 1
            self.last_code = e.code
            self.last_err = (f"HTTP {e.code}: "
                             f"{e.read().decode('utf-8', 'replace')[:150]}")
            self.last_kind = classify_error(self.last_err, e.code)
            return "", None
        except Exception as e:
            self.fails += 1
            self.last_code = None
            self.last_err = f"{type(e).__name__}: {str(e)[:120]}"
            self.last_kind = classify_error(self.last_err)
            return "", None
        self.calls += 1
        self.t_last = time.time() - t0
        self.last_err = ""
        self.last_kind = "none"       # 调用成功；没内容也算 none
        try:
            msg = j["choices"][0]["message"]
            text = (msg.get("content") or "").strip()
            ann = msg.get("annotations") or []
            if ann:
                self.last_lang = ann[0].get("language")
                self.last_emotion = ann[0].get("emotion")
        except Exception:
            return "", None
        return text, self.last_lang

    def status(self):
        if not self.ok:
            return "远端ASR=无 key"
        return (f"远端ASR={self.model}　调用 {self.calls}　失败 {self.fails}"
                f"　上次 {self.t_last:.2f}s")


def from_env_or_key(**kw):
    """有 key 就返回实例，没有就 None（调用方退回本地 whisper）。"""
    key = os.environ.get("DASHSCOPE_API_KEY") or read_key()
    if not key:
        return None
    return RemoteASR(key=key, **kw)


if __name__ == "__main__":
    # 自检：拿真实素材跑一遍，量延迟
    import io as _io
    import sys
    import wave

    sys.stdout = _io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    import numpy as np
    HERE = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, HERE)
    from live_subtitle_v2 import to_mono16k, TARGET_RATE

    r = from_env_or_key()
    if r is None:
        print("  没有 key，跳过")
        sys.exit(0)
    print(f"  {r.status()}")
    ok = fail = 0
    for name, fn, need in (
        ("越南语样本", "samples/vi_sample.wav", None),
        ("英语样本", "e2e_speech.wav", "never"),
    ):
        p = os.path.join(HERE, fn)
        if not os.path.exists(p):
            p = os.path.join(HERE, "samples", os.path.basename(fn))
        if not os.path.exists(p):
            print(f"  {name}: 素材不存在，跳过")
            continue
        w = wave.open(p, "rb")
        raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        a = to_mono16k(raw, w.getframerate(), w.getnchannels())
        dur = len(a) / TARGET_RATE
        txt, lang = r(a)
        if txt:
            ok += 1
            print(f"  {name} {dur:.1f}s → {r.t_last:.2f}s  [{lang}] {txt[:56]}")
            if need:
                if need in txt.lower():
                    print(f"     含关键词 {need!r} ✓")
                else:
                    print(f"     ** 缺关键词 {need!r} **")
        else:
            fail += 1
            print(f"  {name} 失败: {getattr(r, 'last_err', '?')}")
    print(f"\n  合计 {ok} 成功 / {fail} 失败")
