<#
.SYNOPSIS
    为「巴别塔计划」创建桌面 / 开始菜单快捷方式（可重复执行）。

.DESCRIPTION
    快捷方式的目标不是 python / pythonw，而是 wscript.exe：
        wscript.exe "<项目>\desktop\BabelPlan.vbs"
    BabelPlan.vbs 再用 pythonw.exe 拉起 app.py。整条链上没有任何一个
    控制台子系统进程，所以双击后不会闪黑窗。

    图标用 desktop\babel.ico，工作目录固定为项目根（app.py 的相对路径
    都按它自己所在目录推导，但工作目录正确能保证日志/工具行为一致）。

    重复执行只会覆盖同名 .lnk，不会产生重复项。

.PARAMETER ProjectRoot
    项目根目录。默认取本脚本上一级（即 desktop\ 的父目录）。

.PARAMETER NoDesktop
    不创建桌面快捷方式。

.PARAMETER NoStartMenu
    不创建开始菜单快捷方式。

.PARAMETER Remove
    删除本脚本创建的同名快捷方式。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File desktop\create_shortcut.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File desktop\create_shortcut.ps1 -Remove
#>
[CmdletBinding()]
param(
    [string]$ProjectRoot = "",
    [switch]$NoDesktop,
    [switch]$NoStartMenu,
    [switch]$Remove
)

$ErrorActionPreference = "Stop"

# ── 定位项目根：<project>\desktop\create_shortcut.ps1 -> <project> ──────
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Split-Path -Parent $scriptDir
}
if (-not (Test-Path -LiteralPath $ProjectRoot -PathType Container)) {
    throw "项目目录不存在：$ProjectRoot"
}
$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).ProviderPath

$vbs = Join-Path $ProjectRoot "desktop\BabelPlan.vbs"
$ico = Join-Path $ProjectRoot "desktop\babel.ico"
$wscript = Join-Path $env:SystemRoot "System32\wscript.exe"

if (-not (Test-Path -LiteralPath $vbs -PathType Leaf)) {
    throw "找不到启动器：$vbs"
}
if (-not (Test-Path -LiteralPath $ico -PathType Leaf)) {
    Write-Warning "找不到图标 $ico；先运行 python desktop\make_icon.py（快捷方式会暂时用默认图标）"
}
if (-not (Test-Path -LiteralPath $wscript -PathType Leaf)) {
    throw "找不到 wscript.exe：$wscript"
}

$AppName = "巴别塔计划"
$Description = "巴别塔计划 · 直播翻译与视频翻译"

# ── 收集目标位置（桌面 / 开始菜单）──────────────────────────────────────
$targets = New-Object System.Collections.Generic.List[string]
if (-not $NoDesktop) {
    $desktop = [Environment]::GetFolderPath("Desktop")
    if ($desktop) {
        $targets.Add((Join-Path $desktop "$AppName.lnk"))
    } else {
        Write-Warning "找不到桌面目录，跳过桌面快捷方式"
    }
}
if (-not $NoStartMenu) {
    $programs = [Environment]::GetFolderPath("Programs")
    if ($programs) {
        $targets.Add((Join-Path $programs "$AppName.lnk"))
    } else {
        Write-Warning "找不到开始菜单目录，跳过开始菜单快捷方式"
    }
}
if ($targets.Count -eq 0) {
    throw "桌面和开始菜单都不可用，没有可写入的位置"
}

# ── 删除模式 ───────────────────────────────────────────────────────────
if ($Remove) {
    foreach ($lnk in $targets) {
        if (Test-Path -LiteralPath $lnk) {
            Remove-Item -LiteralPath $lnk -Force
            Write-Host "已删除：$lnk"
        } else {
            Write-Host "不存在，跳过：$lnk"
        }
    }
    return
}

# ── 创建 / 覆盖 ────────────────────────────────────────────────────────
$shell = New-Object -ComObject WScript.Shell
foreach ($lnk in $targets) {
    $sc = $shell.CreateShortcut($lnk)
    $sc.TargetPath = $wscript
    $sc.Arguments = '"' + $vbs + '"'
    $sc.WorkingDirectory = $ProjectRoot
    if (Test-Path -LiteralPath $ico -PathType Leaf) {
        $sc.IconLocation = $ico + ",0"
    }
    $sc.Description = $Description
    $sc.WindowStyle = 1          # 1 = 普通窗口（wscript 本来就不显示窗口）
    $sc.Save()
    Write-Host "已创建：$lnk"
}

Write-Host ""
Write-Host "目标：$wscript `"$vbs`""
Write-Host "图标：$ico"
Write-Host "工作目录：$ProjectRoot"
Write-Host ""
Write-Host "双击快捷方式即可启动；日志在 %LOCALAPPDATA%\BabelPlan\app.log"
