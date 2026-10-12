# -*- coding: utf-8 -*-
"""sync_release.py —— 把开发目录同步成可发布的仓库。

为什么有这个脚本
----------------
我曾经手工"打补丁"式地整理发布目录，连续翻了 15 轮，越改越乱，
甚至把主程序改出 IndentationError。
所以改成**一条命令、可重复执行**：每次从开发目录重新生成，
不在发布目录上做增量修改。

它做四件事（都是确定性的字符串处理，不做语法重排）：
  ① 复制白名单文件到 release/（根目录 / bench/ / tools/ / samples/）
  ② 把本机绝对路径换成相对推导
  ③ 子目录脚本的 sys.path / 样本路径按"多退一级"修正
  ④ 生成 LICENSE / .gitignore / requirements.txt

用法
----
    python sync_release.py <开发目录> <发布目录>
    # 例：python sync_release.py "D:/NEW WORK/work/subtitle" "D:/NEW WORK/work/live-subtitle"
"""
from __future__ import annotations

import os
import re
import shutil
import sys

ROOT_FILES = ["live_subtitle_v2.py", "overlay_qt.py", "streaming_asr.py",
              "streaming_asr2.py", "llm_translate.py", "remote_asr.py", "gpu_setup.py", "device_placement.py", "local_mt.py", "streaming_asr_remote.py", "control_panel.py",
              "get_models.py", "get_vi_models.py", "README.md",
              "start.bat", "stream.bat", "panel.bat",
              "publish_github.py", "sync_release.py"]
BENCH = ["bench_latency.py", "bench_localagree.py", "bench_fastest.py",
         "bench_realtime.py", "bench_depth.py", "bench_trend.py",
         "bench_final.py"]
TOOLS = ["test_pipeline.py", "test_borrowed.py", "test_drag2.py", "test_loopback3.py", "test_vietnamese.py",
         "test_mymemory.py", "test_google_free.py", "test_google_sustained.py",
         "compare_vi_paths.py", "compare_vi_models.py", "compare_vi3.py",
         "compare_vi4.py", "probe_free_apis.py", "probe_free2.py",
         "probe_bing.py", "probe_google.py",
         # 延迟诊断与基准。注意：TOOLS 里必须写**精确文件名** ——
         # 带下划线前缀的脚本默认会被丢掉，只写去前缀的名字匹配不上。
         "_bench_bottleneck.py",      # 定位到本地固定开销 2.0s
         "_bench_models.py",          # small/base/tiny 速度与准确度
         "_bench_qwen_mt.py",         # qwen-mt vs deepseek
         "_bench_chunk_size.py",      # 批次大小 vs 端到端延迟
         "_probe_streaming_asr.py",   # 流式 WebSocket 探测
         "_probe_streaming2.py",      # 流式精确延迟（修正版）
         "_measure_e2e_lag.py", "_bench_wlk.py", "_bench_wlk2.py", "_bench_wlk3.py", "_probe_wlk.py", "_bench_readside.py", "_bench_stream_vs_chunk.py", "_bench_stream_clean.py", "_bench_hybrid.py", "_bench_hybrid2.py", "_bench_stream_accuracy.py", "_eye3.py", "_watch.py", "_bench_unit_size.py", "_probe_units.py", "_bench_capture_latency.py", "_bench_7b_v2.py", "_bench_7b.py", "_bench_hymt_official.py", "_inspect_gguf.py", "_inspect_hymt_iface.py", "_bench_local_mt.py", "_bench_hymt_prompt.py", "_bench_hymt_template.py", "_bench_vi_local_vs_remote.py", "_bench_dsp_gate.py", "_bench_mt_batch.py", "_bench_mt_cost.py", "_test_full_gpu.py", "_test_multilang.py", "_test_compare.py", "_bench_gpu.py", "_bench_ja.py", "_diag_ja.py", "_diag_ja2.py", "_bench_punct_split.py", "_bench_punct2.py", "_bench_merge_translate.py", "_bench_decouple.py", "_bench_params.py",       # 端到端延迟主测量工具
         "_measure_lag.py", "_trace_drop.py", "_capture_streaming.py"]
# 样本：只放跑得动的小文件。vi_long.wav / vi_sample.wav 是几 MB 的
# 真实素材，属于本地测试资产，不进仓库。
# streaming_msgs.json 是流式协议的真实消息序列（96KB），
# streaming_asr_remote 的离线合并验证要用它 —— README 里也引用了。
SAMPLES = ["e2e_speech.wav", "streaming_msgs.json", "loopback_test.wav"]

SUBDIRS = ("bench", "tools", "samples")

LICENSE = """MIT License

Copyright (c) 2026 live-subtitle contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

----------------------------------------------------------------------
Third-party notes
----------------------------------------------------------------------
* The optional `google-free` translator uses an undocumented public
  Google endpoint. It is not part of this project and carries no SLA.
* PyQt5 is GPLv3 / commercial dual-licensed. Comply with its terms if
  you redistribute it.
* argostranslate, faster-whisper, CTranslate2, PyAudioWPatch each carry
  their own licenses.
"""

GITIGNORE = """# 模型与下载物
models/
*.argosmodel
*.pt
*.bin

# 运行期产物
run.log
live.srt
overlay_qt.json
overlay_settings.json
control_cmd.json
overlay_state.json
*.log
__pycache__/
*.py[cod]

# ── 凭据：绝不进仓库 ──────────────────────────────────────
# key 实际存在 ~/.dsh/.dashscope（在仓库之外），这里再加一道，
# 防止有人图省事把 key 放进项目目录然后被 add 进来。
.dashscope
.dashscope*
*.dashscope
.env
.env.*
credentials*
*_credentials*
secrets*
*.key
*.pem
api_key*
apikey*
*dashscope_key*

# 编辑器 / 系统
.vscode/
.idea/
.DS_Store
Thumbs.db
"""

REQUIREMENTS = """# live-subtitle dependencies
#   pip install -r requirements.txt
#
# 实测环境无可用 GPU（cuBLAS 缺失），全部按 CPU 调优。
# 需要 GPU 推理请自行安装对应 cuBLAS / cuDNN。

numpy>=1.24
PyAudioWPatch>=0.2.12.8      # WASAPI 环回采集（抓系统播放的声音）
faster-whisper>=1.0          # 语音识别
PyQt5>=5.15                  # 透明置顶浮窗
argostranslate>=1.9          # 离线翻译（注意：没有 vi->zh 直连包）
"""


def clean(t: str) -> str:
    """把本机绝对路径换成相对推导（确定性替换，不做语法重排）。"""
    t = re.sub(r'r?"D:\\NEW WORK\\work\\subtitle"',
               'os.path.dirname(os.path.abspath(__file__))', t)
    t = re.sub(r'r?"D:\\NEW WORK\\work\\subtitle\\([^"]+)"',
               lambda m: 'os.path.join(os.path.dirname(os.path.abspath(__file__)),'
                         f' "{m.group(1)}")', t)
    t = re.sub(r'r?"D:\\NEW WORK\\work\\audio"',
               'os.path.join(os.path.dirname(os.path.dirname('
               'os.path.abspath(__file__))), "samples")', t)
    return t


def fix_subdir(t: str) -> str:
    """子目录脚本：路径按"多退一级"修正。"""
    # sys.path 加仓库根
    t = re.sub(
        r"sys\.path\.insert\(0, os\.path\.dirname\(os\.path\.abspath\(__file__\)\)\)",
        "sys.path.insert(0, os.path.dirname(os.path.dirname("
        "os.path.abspath(__file__))))", t)
    # 指向项目根的绝对路径 → 多退一级
    t = re.sub(r'r?"D:\\NEW WORK\\work\\subtitle"',
               'os.path.dirname(os.path.dirname(os.path.abspath(__file__)))', t)
    t = re.sub(
        r'r?"D:\\NEW WORK\\work\\subtitle\\([^"]+)"',
        lambda m: ('os.path.join(os.path.dirname(os.path.dirname('
                   f'os.path.abspath(__file__))), "{m.group(1)}")'), t)
    # 样本音频统一走 ../samples/
    # 把任意"推导到本目录/上级目录 + e2e_speech.wav"的写法，统一换成 samples/
    t = re.sub(
        r'os\.path\.join\(\s*os\.path\.dirname\([^)]*\)\s*,\s*'
        r'"e2e_speech\.wav"\s*\)',
        'os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),'
        ' "samples", "e2e_speech.wav")', t)
    # 形如 WAV = "…/e2e_speech.wav" 的绝对路径残留
    t = re.sub(r'r?"[^"\n]*e2e_speech\.wav"',
               'os.path.join(os.path.dirname(os.path.dirname('
               'os.path.abspath(__file__))), "samples", "e2e_speech.wav")', t)
    # 样本缺失时优雅退出（而不是 FileNotFoundError）
    if "e2e_speech.wav" in t and "_need_sample" not in t:
        lines = t.split("\n")
        ins = next((i for i, l in enumerate(lines)
                    if "e2e_speech.wav" in l), None)
        if ins is not None:
            guard = [
                "# ── 样本检查 ─────────────────────────────────────────────",
                "# tests/e2e_speech.wav 随仓库发布（samples/ 下）。缺失就友好退出。",
                "def _need_sample(p):",
                '    """确认样本存在，否则给出提示后退出。"""',
                "    if os.path.exists(p):",
                "        return p",
                '    print("跳过：缺少 " + p)',
                '    print("本仓库自带 samples/e2e_speech.wav；请确认已完整 clone。")',
                "    raise SystemExit(0)",
                "",
            ]
            lines[ins:ins] = guard
            # 把 wave.open(WAV, …) 换成 wave.open(_need_sample(WAV), …)
            for i, l in enumerate(lines):
                if "wave.open(WAV" in l:
                    lines[i] = l.replace("wave.open(WAV", "wave.open(_need_sample(WAV)")
            t = "\n".join(lines)
    # 已删的越南语样本 → 约定名
    t = t.replace('"_vi_sample.wav"', '"vi_news.wav"')
    t = t.replace('"_vi_long.wav"', '"vi_news.wav"')
    # v1 → v2
    t = re.sub(r"^from live_subtitle import", "from live_subtitle_v2 import",
               t, flags=re.M)
    t = t.replace("Segmenter, Transcriber, Translator",
                  "Segmenter, BatchTranscriber, Translator")
    t = t.replace("th = Transcriber(", "th = BatchTranscriber(")
    return t


def main(src: str, dst: str) -> int:
    print(f"源：{src}\n目标：{dst}\n")
    print("① 清空目标")
    os.makedirs(dst, exist_ok=True)
    # 这些不删：
    #   .git            —— **删了就等于毁掉整个仓库**（我犯过两次）
    #   .gitattributes / .gitignore / LICENSE / requirements.txt
    #                     —— 由本脚本自己生成，留着让 git 看到改动
    KEEP = {".git", ".gitattributes", ".gitignore", "LICENSE",
            "requirements.txt"}
    # 这些是运行时产物，删掉（.gitignore 也列了，但别让它们待在发布目录里）
    DROP = {"__pycache__", "run.log", "live.srt", "overlay_qt.json",
            "overlay_settings.json"}
    for name in os.listdir(dst):
        if name in KEEP:
            continue                      # ← 必须在删除之前跳过
        p = os.path.join(dst, name)
        if name in DROP or name.endswith((".pyc", ".log")):
            try:
                shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) else os.remove(p)
            except Exception:
                pass
            continue
        try:
            shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) else os.remove(p)
        except Exception as e:
            print(f"   跳过 {name}: {type(e).__name__}")
    for sub in SUBDIRS:
        os.makedirs(os.path.join(dst, sub), exist_ok=True)

    n = 0
    print("② 根目录文件")
    for f in ROOT_FILES:
        s = os.path.join(src, f)
        if not os.path.exists(s):
            print(f"   !! 缺 {f}")
            continue
        if f.endswith(".py"):
            open(os.path.join(dst, f), "w", encoding="utf-8", newline="").write(
                clean(open(s, encoding="utf-8").read()))
        else:
            shutil.copy2(s, os.path.join(dst, f))
        n += 1

    print("③ 子目录文件")
    for sub, files in (("bench", BENCH), ("tools", TOOLS)):
        for f in files:
            s = os.path.join(src, f)
            if not os.path.exists(s):
                print(f"   !! 缺 {sub}/{f}")
                continue
            open(os.path.join(dst, sub, f), "w", encoding="utf-8",
                 newline="").write(fix_subdir(open(s, encoding="utf-8").read()))
            n += 1

    print("④ 样本")
    for f in SAMPLES:
        # 两处都找：开发目录里有些样本在根下、有些在 samples/ 下。
        # 只查根目录会**静默跳过**（以前踩过：11 个真实音频检查被跳过）。
        s = next((p for p in (os.path.join(src, f),
                              os.path.join(src, "samples", f))
                  if os.path.exists(p)), None)
        if s:
            shutil.copy2(s, os.path.join(dst, "samples", f))
            n += 1
        else:
            print(f"      ！样本缺失，已跳过: {f}")

    print("⑤ 仓库元文件")
    open(os.path.join(dst, "LICENSE"), "w", encoding="utf-8").write(LICENSE)
    open(os.path.join(dst, ".gitignore"), "w", encoding="utf-8").write(GITIGNORE)
    open(os.path.join(dst, "requirements.txt"), "w",
         encoding="utf-8").write(REQUIREMENTS)
    n += 3

    print(f"\n完成，共 {n} 个文件")

    # ── 凭据扫描：发现 key 形状的字符串就**拒绝同步** ──────────────
    # 为什么要有这一步：key 存在 ~/.dsh/.dashscope（仓库之外），
    # 但只要有人图省事把它复制进项目目录、或者调试时临时写死一个
    # key 忘了删，下一次同步就会把它带进公开仓库。
    # 这里主动扫一遍，**发现就报错退出**，不要静默同步。
    print()
    print("=" * 70)
    print("凭据扫描（防止 key 进公开仓库）")
    print("=" * 70)
    import re as _re
    # key 形状：sk- 开头、足够长；以及几种常见的凭据字段名
    PATS = [
        (r"sk-[A-Za-z0-9_\-\.]{20,}", "sk- 开头的长密钥"),
        (r"sk-ws-[A-Za-z0-9_\-\.]{20,}", "阿里云百炼 key"),
        (r"(?i)(api[_-]?key|apikey|secret|token)\s*[:=]\s*[\"']"
         r"[A-Za-z0-9_\-\.]{20,}[\"']", "硬编码的 key 赋值"),
    ]
    leaked = []
    for root, dirs, files in os.walk(dst):
        dirs[:] = [d for d in dirs if d != ".git"]
        for f in files:
            p = os.path.join(root, f)
            if os.path.getsize(p) > 2_000_000:
                continue
            try:
                txt = open(p, encoding="utf-8", errors="ignore").read()
            except Exception:
                continue
            for pat, why in PATS:
                for m in _re.finditer(pat, txt):
                    leaked.append((os.path.relpath(p, dst), why,
                                   m.group(0)[:14] + "…"))
    if leaked:
        print("  !! 发现疑似凭据，**已拒绝发布**：")
        for rel, why, frag in leaked[:10]:
            print(f"     {rel}　{why}　{frag}")
        print()
        print("  处置：把该值移到 ~/.dsh/.dashscope（或环境变量），"
              "再从源码里删掉后重跑。")
        print("  仓库里**不要**提交任何真实 key —— 一旦进过 git 历史，"
              "删文件也没用，必须换 key。")
        return 2
    print("  干净：没有 key 形状的字符串")

    # 自检：pyflakes + 残留路径
    print()
    print("=" * 70)
    print("自检")
    print("=" * 70)
    left = []
    for root, _, files in os.walk(dst):
        for fn in files:
            if fn.endswith((".py", ".md", ".bat", ".txt")):
                p = os.path.join(root, fn)
                t = open(p, encoding="utf-8", errors="replace").read()
                for m in re.finditer(r"[A-Z]:\\\\?[^\s\"']{4,}", t):
                    left.append((os.path.relpath(p, dst), m.group(0)[:52]))
    print(f"  残留本机路径：{len(left)}")
    for f, s in left[:8]:
        print(f"    {f}: {s}")
    try:
        import subprocess
        r = subprocess.run([sys.executable, "-m", "pyflakes", dst],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        bad = [l for l in (r.stdout or "").split("\n")
               if any(k in l for k in ("undefined", "syntax", "redefinition"))]
        print(f"  pyflakes 真错误：{len(bad)}")
        for l in bad[:8]:
            print("    " + l.replace(dst, ""))
    except Exception as e:
        print(f"  pyflakes 跳过：{type(e).__name__}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2]))
