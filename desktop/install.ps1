<#
.SYNOPSIS
    巴别塔计划 · 消费级安装/卸载（快捷方式 + 可选开机自启）。

.DESCRIPTION
    这是「可安装」的那一层：复制/构建不是它的职责（项目目录即安装目录，
    exe 可选），它只把**入口**和**自启**装好，并提供干净的卸载。

    · 安装：调用 desktop\create_shortcut.ps1 建桌面/开始菜单快捷方式；
      写 %LOCALAPPDATA%\BabelPlan\install.json 记录安装位置/版本；
      可选 -Autostart 写 HKCU\...\Run（登录后托盘常驻，无黑窗）。
    · 卸载：删快捷方式、删 Run 项、删 install.json；日志目录默认保留
      （-RemoveLogs 才删）。

.PARAMETER ProjectRoot
    项目根目录。默认取本脚本上一级。

.PARAMETER Autostart
    安装时写开机自启（HKCU Run，用户级，不需要管理员）。

.PARAMETER NoShortcuts
    只写 install.json，不建快捷方式。

.PARAMETER Uninstall
    卸载（快捷方式 + Run 项 + install.json）。

.PARAMETER RemoveLogs
    卸载时同时删 %LOCALAPPDATA%\BabelPlan（日志）。

.PARAMETER DryRun
    只打印将执行的动作，不改任何东西。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File desktop\install.ps1
    powershell -ExecutionPolicy Bypass -File desktop\install.ps1 -Autostart
    powershell -ExecutionPolicy Bypass -File desktop\install.ps1 -Uninstall
    powershell -ExecutionPolicy Bypass -File desktop\install.ps1 -DryRun -Uninstall
#>
[CmdletBinding()]
param(
    [string]$ProjectRoot = "",
    [switch]$Autostart,
    [switch]$NoShortcuts,
    [switch]$Uninstall,
    [switch]$RemoveLogs,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$AppName = "巴别塔计划"
$RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$RunValue = "BabelPlan"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Split-Path -Parent $scriptDir
}
$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).ProviderPath
$vbs = Join-Path $ProjectRoot "desktop\BabelPlan.vbs"
$shortcutPs1 = Join-Path $ProjectRoot "desktop\create_shortcut.ps1"
$logDir = Join-Path $env:LOCALAPPDATA "BabelPlan"
$marker = Join-Path $logDir "install.json"

function Say([string]$msg) { Write-Host "  $msg" }
function Act([string]$desc, [scriptblock]$do) {
    if ($DryRun) { Say "[dry-run] $desc"; return }
    Say $desc
    & $do
}

if ($Uninstall) {
    Write-Host "卸载 $AppName"
    Act "删除开始菜单/桌面快捷方式" {
        if (Test-Path -LiteralPath $shortcutPs1) {
            & powershell -NoProfile -ExecutionPolicy Bypass -File $shortcutPs1 -Remove
        }
    }
    Act "删除开机自启（$RunValue）" {
        if (Get-ItemProperty -Path $RunKey -Name $RunValue -ErrorAction SilentlyContinue) {
            Remove-ItemProperty -Path $RunKey -Name $RunValue -ErrorAction SilentlyContinue
        }
    }
    Act "删除安装标记（$marker）" {
        if (Test-Path -LiteralPath $marker) { Remove-Item -LiteralPath $marker -Force }
    }
    if ($RemoveLogs) {
        Act "删除日志目录（$logDir）" {
            if (Test-Path -LiteralPath $logDir) {
                Remove-Item -LiteralPath $logDir -Recurse -Force
            }
        }
    }
    Write-Host "卸载完成（项目目录、模型、Python 环境未动）"
    return
}

# ── 安装 ────────────────────────────────────────────────────
if (-not (Test-Path -LiteralPath $vbs -PathType Leaf)) {
    throw "找不到启动器：$vbs（项目目录不对？）"
}
Write-Host "安装 $AppName"
if (-not $NoShortcuts) {
    Act "创建桌面/开始菜单快捷方式" {
        & powershell -NoProfile -ExecutionPolicy Bypass -File $shortcutPs1 -ProjectRoot $ProjectRoot
    }
}
Act "写安装标记（$marker）" {
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $obj = [ordered]@{
        app       = "BabelPlan"
        project   = $ProjectRoot
        launcher  = $vbs
        installed = (Get-Date).ToString("s")
        autostart = [bool]$Autostart
    }
    $obj | ConvertTo-Json | Set-Content -LiteralPath $marker -Encoding UTF8
}
if ($Autostart) {
    Act "设置开机自启（HKCU Run，托盘常驻）" {
        $cmd = "wscript.exe `"$vbs`" --tray"
        New-Item -Path $RunKey -Force | Out-Null
        Set-ItemProperty -Path $RunKey -Name $RunValue -Value $cmd
    }
}
Write-Host "安装完成：双击桌面「$AppName」即可；卸载：-Uninstall"
