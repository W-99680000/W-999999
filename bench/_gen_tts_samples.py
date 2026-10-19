# -*- coding: utf-8 -*-
"""bench/_gen_tts_samples.py —— 生成带参照文本的中/越 TTS 样本（POC 用）。

为什么用 TTS 而不是找真实标注数据：真实越南语/中文标注音频不好拿，
TTS 音频虽然干净（WER 会偏乐观），但**对两个模型是同一份输入**，
相对比较是公平的。输出：
  samples/tts_zh.wav + samples/tts_zh.txt
  samples/tts_vi.wav + samples/tts_vi.txt
"""
from __future__ import annotations

import asyncio
import io
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

ZH_TEXT = ("各位观众晚上好，欢迎收看今天的新闻节目。首先关注国内要闻："
           "今年以来，我国经济运行总体平稳，高质量发展扎实推进。"
           "农业农村部表示，秋粮丰收已成定局，全年粮食产量有望再创新高。"
           "下面请看详细报道。")

VI_TEXT = ("Xin chào quý vị và các bạn. Hôm nay chúng tôi sẽ đưa tin về "
           "tình hình kinh tế trong nước. Giá gạo xuất khẩu tiếp tục tăng "
           "cao trong tuần qua. Bộ Công Thương cho biết xuất khẩu nông sản "
           "đạt kỷ lục mới. Thời tiết miền Bắc chuyển lạnh, người dân cần "
           "chú ý giữ ấm cơ thể.")

VOICES = {"zh": "zh-CN-XiaoxiaoNeural", "vi": "vi-VN-HoaiMyNeural"}
TEXTS = {"zh": ZH_TEXT, "vi": VI_TEXT}


def mp3_to_wav(mp3_bytes: bytes, out_path: str) -> None:
    """PyAV 解码 mp3 → 16k 单声道 s16 wav（项目已装 PyAV，免 ffmpeg）。"""
    import wave

    import av
    import numpy as np
    from av.audio.resampler import AudioResampler

    resampler = AudioResampler(format="s16", layout="mono", rate=16000)
    chunks = []
    with av.open(io.BytesIO(mp3_bytes)) as c:
        for frame in c.decode(audio=0):
            for rf in resampler.resample(frame):
                chunks.append(rf.to_ndarray().reshape(-1))
    pcm = np.concatenate(chunks) if chunks else np.zeros(1, np.int16)
    with wave.open(out_path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm.astype(np.int16).tobytes())


async def gen(lang: str) -> None:
    import edge_tts

    mp3_path = os.path.join(HERE, "samples", f"tts_{lang}.mp3")
    await edge_tts.Communicate(TEXTS[lang], VOICES[lang]).save(mp3_path)
    mp3_to_wav(open(mp3_path, "rb").read(),
               os.path.join(HERE, "samples", f"tts_{lang}.wav"))
    with open(os.path.join(HERE, "samples", f"tts_{lang}.txt"),
              "w", encoding="utf-8") as f:
        f.write(TEXTS[lang])
    os.remove(mp3_path)
    print(f"tts_{lang}.wav 已生成（{len(TEXTS[lang])} 字符）")


if __name__ == "__main__":
    for lg in ("zh", "vi"):
        asyncio.run(gen(lg))
