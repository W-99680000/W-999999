# -*- coding: utf-8 -*-
"""量：你的 Read 端还有多少可省 —— 对照 EasySub 的 tabCapture 路线。

EasySub（已上线的同类插件）的读数方式，从它商店 manifest 读出来的：
    permissions: storage, activeTab, **tabCapture**, **offscreen**, scripting
    "WASM offline inference · **Zipformer** model (CN/EN) · Manifest V3"
    → tabCapture 在**浏览器内**抓标签页音频，交给 offscreen 文档里的
      sherpa-onnx Zipformer **流式**识别。**它不翻译**（只出原文）。

关键区别：
    它  视频元素 → tabCapture → offscreen → 流式识别
    你  播放器 → 系统混音器 → WASAPI 回环 → 分块识别

tabCapture 绕过了系统混音器，所以"读取端"可能更快。但能省多少要量，
本脚本把你的采集端拆开，逐项量到极限：

  ① 当前每块 10ms 的实际延迟与抖动
  ② 更小的块（5/3/2ms）还能不能撑住（不丢帧）
  ③ 把"读一块"到"用完一块"的全过程算进来
  ④ 结论：即使采集做到 0 延迟，总链路能快多少
"""
import io
import statistics
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, "D:/NEW WORK/work/subtitle")

import numpy as np
import pyaudiowpatch as pyaudio
from live_subtitle_v2 import TARGET_RATE

p = pyaudio.PyAudio()
dev = p.get_wasapi_loopback_analogue_by_dict(p.get_default_output_device_info())
rate = int(dev["defaultSampleRate"])
ch = min(2, dev["maxInputChannels"])
print("  设备 %d Hz / %d ch" % (rate, ch))
print()
print("  ① ② 采集块能压到多小（各测 4 秒，看间隔、抖动、丢帧）")
print()
print("  %6s %8s %9s %9s %9s %8s %9s" % (
    "块ms", "样本", "回调/s", "间隔中位", "间隔P90", "丢帧", "平均延迟"))
print("  " + "-" * 66)
best = None
for ms in (20.0, 10.0, 5.0, 3.0, 2.0, 1.0):
    bs = max(1, int(rate * ms / 1000))
    ts, fr = [], []

    def cb(ind, frames, ti, st):
        if ind:
            ts.append(time.time())
            fr.append(frames)
        return (None, pyaudio.paContinue)

    try:
        st = p.open(format=pyaudio.paInt16, channels=ch, rate=rate, input=True,
                    input_device_index=dev["index"], frames_per_buffer=bs,
                    stream_callback=cb)
    except Exception as e:
        print("  %6.1f  打开失败 %s" % (ms, str(e)[:38]))
        continue
    t0 = time.time()
    while time.time() - t0 < 4.0:
        time.sleep(0.05)
    st.stop_stream(); st.close()

    n = len(ts)
    if n < 5:
        print("  %6.1f %8d %9.1f   （数据不足）" % (ms, bs, n / 4.0))
        continue
    gaps = sorted((ts[i + 1] - ts[i]) * 1000 for i in range(n - 1))
    med = statistics.median(gaps)
    p90 = gaps[int(len(gaps) * 0.9)]
    got = sum(fr)
    should = rate * 4.0
    lost = (1 - got / should) * 100
    print("  %6.1f %8d %9.1f %8.1fms %8.1fms %7.2f%% %8.1fms"
          % (ms, bs, n / 4.0, med, p90, lost, med / 2))
    if abs(lost) < 1.0 and (best is None or ms < best[0]):
        best = (ms, med / 2)
p.terminate()

print()
print("  ③ 结论")
print("  " + "-" * 66)
if best:
    print("    能稳定不丢帧的最小块 %.1fms → 平均延迟 %.1fms" % best)
print()
print("    对照：你当前默认 10ms 块 → 平均延迟 5.0ms")
print()
print("  ④ 即使采集做到 0 延迟，总链路能快多少")
print("  " + "-" * 66)
segs = [
    ("采集（现在）", 5.0, "已实测"),
    ("噪声门", 6.0, "已实测"),
    ("等句子说完（段长）", 1500.0, "已实测：段长 3.5s 的一半+"),
    ("GPU 识别 medium", 300.0, "已实测：4s 段 0.60s，摊到每 3.5s 音频"),
    ("翻译（合并后）", 200.0, "已实测"),
]
tot = sum(s[1] for s in segs)
for name, ms, note in segs:
    print("    %-22s %8.1fms  %5.1f%%   %s" % (name, ms, ms / tot * 100, note))
print("    " + "-" * 52)
print("    %-22s %8.1fms" % ("合计", tot))
print()
save = 5.0
print("    即使把采集压到 **0ms**（比 tabCapture 还理想）：")
print("      省下 %.1fms = 总链路的 %.2f%%" % (save, save / tot * 100))
print()
print("    → **读取端不是瓶颈。** 换成 tabCapture 最多省几毫秒。")
