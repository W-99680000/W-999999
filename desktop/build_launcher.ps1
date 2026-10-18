<#
.SYNOPSIS
    构建带图标的原生启动器 desktop\BabelPlan.exe（C#，20KB，启动即开）。

.DESCRIPTION
    早期用 PyInstaller onefile 做的启动器每次启动要解包 ~7MB 到临时目录，
    实测“双击后迟迟没反应”。现在改成原生 C# WinExe：
      · 20KB，无运行时解包，启动瞬间完成；
      · /win32icon 把 desktop\babel.ico 嵌进 exe，任务栏/资源管理器图标正确；
      · 运行后立即转交 desktop\BabelPlan.vbs（由它找 pythonw 拉起 app.py）。

    需要 Windows 自带的 .NET Framework csc.exe（Win10/11 默认有）。
    产物 desktop\BabelPlan.exe 在 .gitignore 里，不提交。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File desktop\build_launcher.ps1
#>
[CmdletBinding()]
param(
    [string]$ProjectRoot = ""
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Split-Path -Parent $scriptDir
}
$src = Join-Path $scriptDir "launcher_native.cs"
$ico = Join-Path $scriptDir "babel.ico"
$out = Join-Path $scriptDir "BabelPlan.exe"
$csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path -LiteralPath $csc)) {
    $csc = Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\csc.exe"
}
if (-not (Test-Path -LiteralPath $csc)) {
    throw "找不到 .NET Framework csc.exe（需要 Windows 自带 .NET 4.x）"
}
if (-not (Test-Path -LiteralPath $src)) { throw "找不到 $src" }
if (-not (Test-Path -LiteralPath $ico)) {
    Write-Warning "找不到图标 $ico（启动器仍可构建，只是没有图标）"
}
$args = @("/nologo", "/target:winexe", "/out:$out")
if (Test-Path -LiteralPath $ico) { $args += "/win32icon:$ico" }
$args += $src
& $csc @args
if ($LASTEXITCODE -ne 0) { throw "csc 编译失败（exit $LASTEXITCODE）" }
$fi = Get-Item -LiteralPath $out
Write-Host "已构建：$out（$([math]::Round($fi.Length/1KB,1)) KB）"
Write-Host "它启动后转交 desktop\BabelPlan.vbs；快捷方式已指向该 exe。"
