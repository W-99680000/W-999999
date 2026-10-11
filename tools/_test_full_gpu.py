import io, sys, os, time, wave, subprocess
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from live_subtitle_v2 import to_mono16k, TARGET_RATE
from faster_whisper import WhisperModel

def vram():
    r = subprocess.run(["nvidia-smi", "--query-gpu=memory.used",
                        "--format=csv,noheader,nounits"],
                       capture_output=True, text=True)
    return int((r.stdout or "0").strip().split("\n")[0] or 0)

print("=== 完整管线的显存与延迟（模拟主程序：4秒批次 + 翻译）===")
import live_subtitle_v2 as M
from llm_translate import from_env_or_args

print("  基线显存 %d MiB" % vram())
m = WhisperModel("large-v3", device="cuda", compute_type="float16")
print("  加载 large-v3 后 %d MiB（+%d）" % (vram(), vram() - 0) )
tr = from_env_or_args()
print("  翻译器: %s" % (tr.status() if tr else "无"))
print()
print("  模拟 20 个 4 秒批次（真实速率喂入），量端到端：")
import statistics
lags = []
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
t_start = time.time()
n = 0
for i in range(0, len(audio) - seg4 + 1, seg4):
    n += 1
    fed = (i + seg4) / TARGET_RATE          # 该段结束的音频位置
    # 按真实速率等待
    now = time.time() - t_start
    if fed > now:
        time.sleep(fed - now)
    t0 = time.time()
    segs, info = m.transcribe(audio[i:i+seg4], beam_size=5, vad_filter=True,
                              condition_on_previous_text=False, temperature=0.0)
    txt = " ".join(s.text.strip() for s in segs).strip()
    t1 = time.time()
    zh = tr(txt) if txt else ""
    t2 = time.time()
    lag = (t2 - t_start) - fed
    lags.append(lag)
    if n <= 6 or n % 4 == 0:
        print("    批次%-3d 识别%.3fs 翻译%.3fs 延迟%+.2fs  %s" %
              (n, t1-t0, t2-t1, lag, (zh or txt)[:40]))
lags.sort()
print()
print("  **端到端延迟：中位 %.2fs　P90 %.2fs　最大 %.2fs**" %
      (statistics.median(lags), lags[int(len(lags)*0.9)], max(lags)))
print("  对照：远端识别 + 远端翻译 = 中位 0.88s")
print()
print("  峰值显存 %d MiB / 8188 MiB" % vram())
