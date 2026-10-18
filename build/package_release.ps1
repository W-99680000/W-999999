<#
.SYNOPSIS
    巴别塔计划 · 分发打包（portable zip，不含模型/缓存/日志）。

.DESCRIPTION
    把「运行必需」的文件收进 build\_release\BabelPlan\，再压成
    BabelPlan-<日期>.zip。模型权重、pre_cache、work、.git 都不进包
    （首次运行由 get_models / 缓存机制下载/生成；本机已有 HF 缓存则直接复用）。

    · 不含引擎 exe（消费者用 VBS+pythonw，或先跑 build_exe.ps1 再打壳）
    · 目标机需要：Windows 10/11 + Python 3.10+（pip install -r requirements.txt）
    · 安装入口：解压后跑一次 desktop\install.ps1（或直接双击 BabelPlan.vbs）

.PARAMETER OutDir
    输出目录，默认 build\_release。

.PARAMETER NoZip
    只生成目录，不压缩。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File build\package_release.ps1
#>
[CmdletBinding()]
param(
    [string]$OutDir = "",
    [switch]$NoZip
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $scriptDir
if ([string]::IsNullOrWhiteSpace($OutDir)) {
    $OutDir = Join-Path $root "build\_release"
}
$stage = Join-Path $OutDir "BabelPlan"

if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path $stage | Out-Null

# ── 根文件：显式白名单（运行必需，排除日志/状态/pre_cache/开发脚本）──
$rootNames = @(
    "live_subtitle_v2.py", "overlay_qt.py", "streaming_asr.py",
    "streaming_asr2.py", "streaming_asr_remote.py", "llm_translate.py",
    "remote_asr.py", "gpu_setup.py", "device_placement.py",
    "device_pref.py", "local_mt.py", "control_panel.py", "get_models.py",
    "get_vi_models.py", "watch_player.py", "watch_overlay.py",
    "pretranslate.py", "browser_history.py", "autopipe.py",
    "smtc_tracker.py", "foreground.py", "model_registry.py",
    "model_registry.json", "hardware_detect.py", "model_manager.py",
    "startup_stats.py", "native_preload.py", "app.py", "admin_qt.py",
    "start.bat", "stream.bat", "panel.bat", "watch.bat", "cache.bat",
    "admin.bat", "admin_qt.bat", "app.bat",
    "README.md", "requirements.txt", "LICENSE",
    "OPTIMIZATION_REVIEW_20261018.md"
)
$nRoot = 0
foreach ($name in $rootNames) {
    $src = Join-Path $root $name
    if (Test-Path -LiteralPath $src) {
        Copy-Item -LiteralPath $src -Destination $stage -Force
        $nRoot++
    } else {
        Write-Host ("  跳过（不存在）：{0}" -f $name)
    }
}
Write-Host ("根文件 {0} 个" -f $nRoot)

# ── 子目录（精确清单，别整目录复制）──────────────────────────
$dirs = @{
    "babel_admin" = @("index.html", "serve.py", "dlls.py")
    "desktop"     = @("BabelPlan.vbs", "babel.ico", "make_icon.py", "create_shortcut.ps1", "install.ps1")
    "build"       = @("babel.spec", "build_exe.ps1", "package_release.ps1")
    "docs"        = @("DESKTOP.md", "QUALITY_ROADMAP.md", "RESEARCH_20261018_streaming_translation.md")
    "tools"       = @("doctor.py", "quality_report.py", "check_admin_html.py",
                      "test_engine_control.py", "soak_test.py", "test_watchdog.py",
                      "check_qwen_terms.py")
    "bench"       = @("eval_asr.py", "_verify_quality_gates.py", "_verify_hysteresis.py",
                      "_bench_batched_asr.py")
    "samples"     = @("e2e_speech.wav", "loopback_test.wav", "jfk.wav", "streaming_msgs.json")
}
foreach ($sub in $dirs.Keys) {
    $dst = Join-Path $stage $sub
    New-Item -ItemType Directory -Force -Path $dst | Out-Null
    $n = 0
    foreach ($name in $dirs[$sub]) {
        $src = Join-Path (Join-Path $root $sub) $name
        if (Test-Path -LiteralPath $src) {
            Copy-Item -LiteralPath $src -Destination $dst -Force
            $n++
        } else {
            Write-Host ("  跳过（不存在）：{0}\{1}" -f $sub, $name)
        }
    }
    Write-Host ("{0}\ {1} 个" -f $sub, $n)
}

# ── 快速开始 ─────────────────────────────────────────────────
$quick = @"
巴别塔计划 · 快速开始
=====================

1) 安装 Python 3.10+（Windows 10/11）
2) 在本目录打开 PowerShell：
   pip install -r requirements.txt
3) 安装入口（快捷方式，可选开机自启）：
   powershell -ExecutionPolicy Bypass -File desktop\install.ps1
   或直接双击 desktop\BabelPlan.vbs（无黑窗，托盘常驻）
4) 双击桌面「巴别塔计划」→ 首页两个按钮：直播翻译 / 视频翻译

日志：%LOCALAPPDATA%\BabelPlan\app.log
卸载：powershell -ExecutionPolicy Bypass -File desktop\install.ps1 -Uninstall

说明：本包不含模型权重；首次用某模型会走模型仓库/HF 缓存。
GPU 环境建议：pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
"@
Set-Content -LiteralPath (Join-Path $stage "快速开始.txt") -Value $quick -Encoding UTF8

# ── 统计 / 压缩 ──────────────────────────────────────────────
$files = Get-ChildItem -LiteralPath $stage -Recurse -File
$mb = [math]::Round((($files | Measure-Object -Property Length -Sum).Sum) / 1MB, 2)
Write-Host ("打包目录：{0}（{1} 个文件，{2} MB）" -f $stage, $files.Count, $mb)
if (-not $NoZip) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmm"
    $zip = Join-Path $OutDir ("BabelPlan-{0}.zip" -f $stamp)
    if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
    Compress-Archive -LiteralPath $stage -DestinationPath $zip -CompressionLevel Optimal
    $z = Get-Item -LiteralPath $zip
    Write-Host ("ZIP：{0}（{1} MB）" -f $z.FullName, [math]::Round($z.Length / 1MB, 2))
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $entries = [System.IO.Compression.ZipFile]::OpenRead($zip).Entries.Count
    Write-Host ("ZIP 条目：{0}" -f $entries)
}
Write-Host "完成。"
