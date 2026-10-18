<#
.SYNOPSIS
    用 PyInstaller 打「巴别塔计划」桌面壳（one-folder / windowed）。

.DESCRIPTION
    产物：build\_dist\BabelPlan\BabelPlan.exe（整个 BabelPlan 目录一起发布）。

    打的是什么：app.py（pywebview 窗口 + pystray 托盘 + 本地 HTTP 服务）、
    babel_admin 的后台代码、babel_admin\index.html、图标。
    **不打包 ASR / 翻译模型**：引擎仍然用本机 Python 解释器跑
    （babel_admin/dlls.py 挑解释器，serve.py 拉起子进程）。

    BABEL_PY 是引擎解释器的覆盖入口，运行 exe 时设置即可：
        set BABEL_PY=C:\Python312\python.exe
        BabelPlan.exe
    （app.py 会把它映射为 babel_admin/dlls.py 认识的 BABEL_PYTHON。）

    消费者日常用的入口仍是 desktop\BabelPlan.vbs + 快捷方式：它用
    pythonw.exe 跑 app.py，不需要打 exe。exe 只是"不想让壳依赖
    系统 Python"时的可选路线；引擎那一段仍然需要本机 Python。

.PARAMETER Python
    用来打包的解释器。默认依次找 BABEL_PY、Python312/311、.dsh runtime、PATH。

.PARAMETER InstallPyInstaller
    未安装 PyInstaller 时自动 pip install。默认代理 http://127.0.0.1:10808，
    可用环境变量 BABEL_PIP_PROXY 覆盖。

.PARAMETER NoQt
    不把 PyQt5 打进壳（体积小很多）。默认按 spec 打包 PyQt5 ——
    babel.spec 的隐藏导入列表里带了它；壳本身其实不用 Qt。

.PARAMETER Clean
    打包前删掉 _dist / _work 输出目录。

.PARAMETER DistPath
    输出目录，默认 build\_dist。

.PARAMETER WorkPath
    中间物目录，默认 build\_work。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File build\build_exe.ps1 -InstallPyInstaller

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File build\build_exe.ps1 -NoQt -Clean
#>
[CmdletBinding()]
param(
    [string]$Python = "",
    [switch]$InstallPyInstaller,
    [switch]$NoQt,
    [switch]$Clean,
    [string]$DistPath = "",
    [string]$WorkPath = ""
)

$ErrorActionPreference = "Stop"

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here
$spec = Join-Path $here "babel.spec"
$app = Join-Path $root "app.py"
$index = Join-Path $root "babel_admin\index.html"
$icon = Join-Path $root "desktop\babel.ico"

foreach ($p in @($spec, $app, $index, $icon)) {
    if (-not (Test-Path -LiteralPath $p -PathType Leaf)) {
        throw "缺少打包输入：$p"
    }
}

# ── 选解释器：-Python > BABEL_PY > 标准安装位置 > PATH ──────────────────
if ([string]::IsNullOrWhiteSpace($Python)) {
    $candidates = New-Object System.Collections.Generic.List[string]
    if ($env:BABEL_PY) { $candidates.Add($env:BABEL_PY) }
    $candidates.Add((Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"))
    $candidates.Add((Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"))
    $candidates.Add((Join-Path $env:USERPROFILE ".dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"))
    foreach ($c in $candidates) {
        if ($c -and (Test-Path -LiteralPath $c -PathType Leaf)) {
            $Python = $c
            break
        }
    }
}
if ([string]::IsNullOrWhiteSpace($Python)) { $Python = "python" }

$version = & $Python -c "import sys; print(sys.version.split()[0])"
if ($LASTEXITCODE -ne 0) { throw "无法运行 Python：$Python" }
Write-Host "Python $version  ($Python)"

# ── PyInstaller ────────────────────────────────────────────────────────
$hasPyi = (& $Python -c "import importlib.util; print(1 if importlib.util.find_spec('PyInstaller') else 0)").Trim()
if ($hasPyi -ne "1") {
    if ($InstallPyInstaller) {
        $proxy = if ($env:BABEL_PIP_PROXY) { $env:BABEL_PIP_PROXY } else { "http://127.0.0.1:10808" }
        Write-Host "安装 PyInstaller（pip 代理：$proxy）..."
        & $Python -m pip install --upgrade pyinstaller --proxy $proxy --disable-pip-version-check
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller 安装失败（代理不可用？）" }
    } else {
        throw ("未安装 PyInstaller。`n" +
               "  手动安装：`"$Python`" -m pip install pyinstaller`n" +
               "  或重跑本脚本并加 -InstallPyInstaller（默认代理 127.0.0.1:10808）")
    }
}

if ([string]::IsNullOrWhiteSpace($DistPath)) { $DistPath = Join-Path $here "_dist" }
if ([string]::IsNullOrWhiteSpace($WorkPath)) { $WorkPath = Join-Path $here "_work" }

if ($Clean) {
    foreach ($d in @($DistPath, $WorkPath)) {
        if (Test-Path -LiteralPath $d) {
            Remove-Item -LiteralPath $d -Recurse -Force
            Write-Host "已清理 $d"
        }
    }
}
New-Item -ItemType Directory -Force -Path $DistPath, $WorkPath | Out-Null

# NoQt 通过环境变量传给 babel.spec（spec 在 Analysis 阶段读它）
$oldNoQt = $env:BABEL_EXE_NO_QT
try {
    if ($NoQt) {
        $env:BABEL_EXE_NO_QT = "1"
        Write-Host "本次打包不包含 PyQt5（-NoQt）"
    } else {
        Remove-Item Env:BABEL_EXE_NO_QT -ErrorAction SilentlyContinue
    }

    Write-Host "PyInstaller 开始打包（one-folder / windowed）..."
    & $Python -m PyInstaller --noconfirm --clean `
        --distpath $DistPath --workpath $WorkPath $spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失败（退出码 $LASTEXITCODE）" }
}
finally {
    if ($null -eq $oldNoQt) {
        Remove-Item Env:BABEL_EXE_NO_QT -ErrorAction SilentlyContinue
    } else {
        $env:BABEL_EXE_NO_QT = $oldNoQt
    }
}

$exe = Join-Path $DistPath "BabelPlan\BabelPlan.exe"
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
    throw "打包结束但没有找到 $exe"
}
$folder = Split-Path -Parent $exe
$total = (Get-ChildItem -LiteralPath $folder -Recurse -File | Measure-Object -Property Length -Sum).Sum
$sizeMB = [math]::Round($total / 1MB, 1)

Write-Host ""
Write-Host "打包完成：$exe"
Write-Host "整个目录一起发布：$folder（约 $sizeMB MB）"
Write-Host ""
Write-Host "说明："
Write-Host "  · 只打控制台壳，不含 ASR / 翻译模型；引擎仍用本机 Python"
Write-Host "    （运行前可 set BABEL_PY=<python.exe> 指定引擎解释器）"
Write-Host "  · one-folder：不要只拷 exe，必须连同 _internal 目录一起拷"
Write-Host "  · 引擎脚本 live_subtitle_v2.py 不在壳里，仍从项目目录使用；"
Write-Host "    消费者推荐入口仍是 desktop\BabelPlan.vbs（pythonw，无黑窗）"
