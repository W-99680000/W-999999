import io, sys, os, time, wave, re, statistics
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from live_subtitle_v2 import to_mono16k, TARGET_RATE
from faster_whisper import WhisperModel
from remote_asr import from_env_or_key

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES = [("越南语", "samples/vi_sample.wav"), ("越南语长", "samples/vi_long.wav")]
print("=== 素材 ===")
loaded = []
for name, rel in SAMPLES:
    p = os.path.join(HERE, rel)
    if not os.path.exists(p):
        continue
    w = wave.open(p, "rb")
    raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    a = to_mono16k(raw, w.getframerate(), w.getnchannels())
    loaded.append((name, a))
    print("  %s  %.1f 秒" % (name, len(a) / TARGET_RATE))

def vram():
    import subprocess
    r = subprocess.run(["nvidia-smi", "--query-gpu=memory.used",
                        "--format=csv,noheader,nounits"],
                       capture_output=True, text=True)
    return int((r.stdout or "0").strip().split("\n")[0] or 0)

print()
print("=== GPU 上各模型：越南语 ===")
for size in ("small", "medium", "large-v3"):
    try:
        base_v = vram()
        t0 = time.time()
        m = WhisperModel(size, device="cuda", compute_type="float16")
        load = time.time() - t0
        v = vram() - base_v
        print("\n  【%s】加载 %.1fs　显存 +%d MiB" % (size, load, v))
        for name, a in loaded:
            # 按 4 秒逐段转写（模拟真实批次）
            seg4 = int(4 * TARGET_RATE)
            texts, ts = [], []
            for i in range(0, len(a) - seg4 + 1, seg4):
                t0 = time.time()
                segs, info = m.transcribe(a[i:i+seg4], beam_size=5,
                                          vad_filter=True,
                                          condition_on_previous_text=False,
                                          temperature=0.0)
                txt = " ".join(s.text.strip() for s in segs).strip()
                ts.append(time.time() - t0)
                if txt:
                    texts.append(txt)
            ts.sort()
            per = ts[len(ts)//2] if ts else 0
            print("    %s：%d 段　每段 %.3fs　语种=%s" % (name, len(ts), per, info.language))
            print("      首段: %s" % (texts[0][:64] if texts else "(空)"))
        del m
    except Exception as e:
        print("  %s 失败: %s" % (size, str(e)[:110]))

print()
print("=== 对照：远端 qwen3-asr-flash（当前默认）===")
r = from_env_or_key()
if r:
    for name, a in loaded:
        ts = []
        for _ in range(3):
            t0 = time.time()
            txt, lg = r(a[:int(8*TARGET_RATE)])
            ts.append(time.time() - t0)
        ts.sort()
        print("  %s 8秒 → %.3fs [%s] %s" % (name, ts[1], lg, (txt or "")[:56]))
