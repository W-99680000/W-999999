import io, sys, os, time, wave, statistics, subprocess
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from live_subtitle_v2 import to_mono16k, TARGET_RATE
from faster_whisper import WhisperModel
from llm_translate import from_env_or_args
from remote_asr import from_env_or_key

def vram():
    r = subprocess.run(["nvidia-smi","--query-gpu=memory.used",
                        "--format=csv,noheader,nounits"],capture_output=True,text=True)
    return int((r.stdout or "0").strip().split("\n")[0] or 0)

# ── 样本检查 ─────────────────────────────────────────────
# tests/e2e_speech.wav 随仓库发布（samples/ 下）。缺失就友好退出。
def _need_sample(p):
    """确认样本存在，否则给出提示后退出。"""
    if os.path.exists(p):
        return p
    print("跳过：缺少 " + p)
    print("本仓库自带 samples/e2e_speech.wav；请确认已完整 clone。")
    raise SystemExit(0)

p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples", "e2e_speech.wav"))
if not os.path.exists(p):
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples", "e2e_speech.wav"))
w = wave.open(p, "rb")
raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
base = to_mono16k(raw, w.getframerate(), w.getnchannels())
audio = np.tile(base, 3)[: int(48 * TARGET_RATE)]
seg4 = int(4 * TARGET_RATE)
tr = from_env_or_args()
rasr = from_env_or_key()

def run_local(size):
    m = WhisperModel(size, device="cuda", compute_type="float16")
    v = vram()
    lags, asr_t, mt_t = [], [], []
    t_start = time.time()
    for i in range(0, len(audio) - seg4 + 1, seg4):
        fed = (i + seg4) / TARGET_RATE
        now = time.time() - t_start
        if fed > now: time.sleep(fed - now)
        t0 = time.time()
        segs, info = m.transcribe(audio[i:i+seg4], beam_size=5, vad_filter=True,
                                  condition_on_previous_text=False, temperature=0.0)
        txt = " ".join(s.text.strip() for s in segs).strip()
        t1 = time.time()
        zh = tr(txt) if txt else ""
        t2 = time.time()
        asr_t.append(t1-t0); mt_t.append(t2-t1); lags.append((t2-t_start)-fed)
    del m
    lags.sort()
    return (statistics.median(lags), lags[int(len(lags)*0.9)], max(lags),
            statistics.median(asr_t), statistics.median(mt_t), v)

def run_remote():
    lags, asr_t, mt_t = [], [], []
    t_start = time.time()
    for i in range(0, len(audio) - seg4 + 1, seg4):
        fed = (i + seg4) / TARGET_RATE
        now = time.time() - t_start
        if fed > now: time.sleep(fed - now)
        t0 = time.time()
        txt, lg = rasr(audio[i:i+seg4])
        t1 = time.time()
        zh = tr(txt) if txt else ""
        t2 = time.time()
        asr_t.append(t1-t0); mt_t.append(t2-t1); lags.append((t2-t_start)-fed)
    lags.sort()
    return (statistics.median(lags), lags[int(len(lags)*0.9)], max(lags),
            statistics.median(asr_t), statistics.median(mt_t), 0)

print("=" * 88)
print("本地 GPU（medium / large-v3）vs 远端，各 12 个 4 秒批次，真实速率")
print("=" * 88)
print(f"  {'方案':<26} {'中位':>7} {'P90':>7} {'最大':>7} {'识别':>7} {'翻译':>7} {'显存':>8}")
print("  " + "-" * 76)
rows = []
for size in ("small", "medium", "large-v3"):
    try:
        med, p90, mx, ta, tm, v = run_local(size)
        rows.append((size, med))
        print(f"  {'本地 '+size+' (GPU)':<26} {med:>6.2f}s {p90:>6.2f}s {mx:>6.2f}s "
              f"{ta:>6.3f}s {tm:>6.3f}s {v:>6d}MB")
    except Exception as e:
        print(f"  本地 {size} 失败: {str(e)[:70]}")
med, p90, mx, ta, tm, v = run_remote()
print(f"  {'远端 qwen3-asr + qwen-mt':<26} {med:>6.2f}s {p90:>6.2f}s {mx:>6.2f}s "
      f"{ta:>6.3f}s {tm:>6.3f}s {'—':>8}")
print()
print("  注：翻译两边都用 qwen-mt（远端），所以差异只在识别。")
print("      翻译 0.6s 是共同成本；换成更快的翻译还能再降。")
