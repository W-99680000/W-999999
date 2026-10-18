# -*- mode: python ; coding: utf-8 -*-
"""build/babel.spec —— 巴别塔计划 · 桌面壳打包定义（PyInstaller）

打的是什么
==========
**只打"控制台壳"**：app.py（窗口 + 托盘 + 本地 HTTP 服务）以及它 import
的 babel_admin/ 后台代码、babel_admin/index.html、图标。
**不打包 ASR / 翻译引擎，也不打包任何模型权重** —— 引擎仍然由本机
Python 解释器跑（babel_admin/dlls.py 负责挑选，serve.py 负责拉起）。

为什么是 one-folder 而不是 one-file
===================================
one-file 每次启动都要把几百 MB 解压到临时目录，消费者双击要等十几秒，
并且 WebView2 / PyQt 的 DLL 在临时目录里更容易被杀软拦。one-folder 启动
就是直接映射，快且稳。代价是发布时**必须拷贝整个输出目录**。

windowed
========
`console=False`（等价于 `--windowed`）→ 生成的 exe 是 GUI 子系统，
双击不会弹黑窗；app.py 里已经把无控制台时的 stdout/stderr 接到日志文件。

BABEL_PY：引擎解释器覆盖入口
============================
exe 只是壳，引擎解释器仍可从外部指定：

    set BABEL_PY=C:\\Python312\\python.exe
    BabelPlan.exe

app.py 会把 BABEL_PY 映射成 babel_admin/dlls.py 认识的 BABEL_PYTHON。

怎么跑
======
    python -m PyInstaller --noconfirm --clean build\\babel.spec
或直接：
    powershell -ExecutionPolicy Bypass -File build\\build_exe.ps1
"""
import importlib.util
import os

#: PyInstaller 执行 spec 时注入：本文件（build/）所在目录
SPEC_DIR = SPECPATH
ROOT = os.path.dirname(SPEC_DIR)
APP = os.path.join(ROOT, "app.py")
INDEX = os.path.join(ROOT, "babel_admin", "index.html")
ICON = os.path.join(ROOT, "desktop", "babel.ico")
REGISTRY = os.path.join(ROOT, "model_registry.json")
REGISTRY_WEB = os.path.join(ROOT, "model_registry_web.json")


def _has(mod: str) -> bool:
    """解释器里有没有这个模块（PyInstaller 的 hiddenimport 不能瞎写）。"""
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


# ── 资源文件 ────────────────────────────────────────────────────────────
# 注意目标目录（第二个元素）是相对 one-folder 的 `_internal`：
#   babel_admin/index.html  →  _internal/babel_admin/index.html（serve.py 读它）
#   desktop/babel.ico       →  _internal/desktop/babel.ico
#   model_registry*.json    →  _internal/（model_registry.PATH 在冻结后指向这里）
datas = []
for src, dest in ((INDEX, "babel_admin"), (ICON, "desktop"),
                  (REGISTRY, "."), (REGISTRY_WEB, ".")):
    if os.path.isfile(src):
        datas.append((src, dest))
    else:
        print(f"[babel.spec] 警告：缺资源 {src}（打包后对应功能可能不可用）")

# ── 隐藏导入 ────────────────────────────────────────────────────────────
# PyQt5：桌面壳本身不用 Qt，浮窗 / 引擎是独立进程。保留它是因为
# 打包要求里明确要带（以后若把浮窗并进壳就不用改 spec）；
# `build_exe.ps1 -NoQt` 会通过环境变量把它剔掉，壳能小很多。
# pywebview 的**发行包名**叫 pywebview，import 名是 webview —— 这里两个
# 都列，`_has()` 会过滤掉不存在的名字。
_CANDIDATES = (
    "webview",                       # pywebview 的实际模块名
    "webview.platforms.winforms",    # Windows 后端（Edge WebView2）
    "webview.platforms.edgechromium",
    "webview.platforms.mshtml",
    "pystray",
    "pystray._win32",                # Windows 托盘后端是动态选择的
    "PIL",
    "PIL.Image",
    "PIL.ImageDraw",
    "PyQt5",
    "PyQt5.QtCore",
    "PyQt5.QtGui",
    "PyQt5.QtWidgets",
)
hiddenimports = [m for m in _CANDIDATES if _has(m)]
if os.environ.get("BABEL_EXE_NO_QT") == "1":
    hiddenimports = [m for m in hiddenimports if not m.startswith("PyQt5")]

# ── 明确排除：这些是引擎 / 训练 / 开发期依赖，不该进壳 ────────────────
excludes = [
    "tkinter", "matplotlib", "IPython", "pytest",
    "torch", "torchaudio", "transformers", "sentencepiece", "onnxruntime",
    "faster_whisper", "ctranslate2", "argostranslate", "pyaudiowpatch",
    "soundcard", "sounddevice", "PySide6", "PyQt6",
]

print(f"[babel.spec] root={ROOT}")
print(f"[babel.spec] datas={[os.path.basename(s) for s, _ in datas]}")
print(f"[babel.spec] hiddenimports={hiddenimports}")

a = Analysis(
    [APP],
    pathex=[ROOT, os.path.join(ROOT, "babel_admin")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BabelPlan",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                       # UPX 容易触发杀软误报，不压
    console=False,                   # ← windowed：双击没有黑窗
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON if os.path.isfile(ICON) else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="BabelPlan",
)
