# 桌面启动（消费者版）

双击一个图标就能用：**没有黑色命令行窗口**，关掉窗口程序不退出（收到托盘），
日志写文件。本文是这一层的一页说明。

## 0. 一键安装 / 卸载（推荐）

```powershell
powershell -ExecutionPolicy Bypass -File desktop\install.ps1
# 可选：登录后托盘常驻（写 HKCU Run，不需要管理员）
powershell -ExecutionPolicy Bypass -File desktop\install.ps1 -Autostart
# 卸载（删快捷方式 + 自启 + 安装标记）
powershell -ExecutionPolicy Bypass -File desktop\install.ps1 -Uninstall
```

`install.ps1` 只负责入口：建快捷方式、写
`%LOCALAPPDATA%\BabelPlan\install.json`、可选自启；不复制文件、不动模型。
加 `-DryRun` 可先看它会做什么。日志目录默认保留（`-Uninstall -RemoveLogs` 才删）。

## 1. 一次性创建快捷方式

在项目根目录打开 PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File desktop\create_shortcut.ps1
```

它会在**桌面**和**开始菜单**各放一个「巴别塔计划」快捷方式，可以重复执行
（只会覆盖同名快捷方式）。删除快捷方式：

```powershell
powershell -ExecutionPolicy Bypass -File desktop\create_shortcut.ps1 -Remove
```

快捷方式的目标是 `wscript.exe "<项目>\desktop\BabelPlan.vbs"`，图标是
`desktop\babel.ico`，工作目录是项目根。

## 2. 双击之后

- 启动链：`wscript.exe` → `desktop\BabelPlan.vbs` → `pythonw.exe app.py`。
  `wscript.exe` 和 `pythonw.exe` 都是 GUI 子系统，**整条链不会创建控制台**；
  `app.py` 在无控制台时把 `sys.stdout/sys.stderr` 接到日志文件。
- 第一次启动会稍微慢几秒（导出模型快照 + 预热），窗口随后出现。
- 如果 `http://127.0.0.1:8770` 已经有服务（例如开发时开着 `admin.bat`），
  桌面壳**直接复用**，不会重复起服务；日志里会写一行「已有服务…直接复用」。
- **单实例**：再双击一次不会出现第二个托盘/第二个窗口，第二个进程写一行
  日志后以 0 退出。

窗口标题：`巴别塔计划 · 直播翻译与视频翻译`。

## 3. 托盘菜单

右键托盘图标（绿底声波）：

| 菜单项 | 行为 |
| --- | --- |
| 打开控制台（默认双击） | 显示/唤起主窗口 |
| 打开日志目录 | 打开 `%LOCALAPPDATA%\BabelPlan` |
| 退出（停止引擎） | 退出程序，并停止**本进程拉起的**引擎子进程 |
| 退出（保留引擎） | 退出程序，让引擎继续跑（下次打开会接上） |

点窗口右上角关闭 = **收到托盘**，程序继续运行；真要退出走托盘菜单。
用户已经手动开着的引擎（例如另一个终端里的 `live_subtitle_v2.py`）不会被
桌面壳杀掉。

## 4. 日志

| 文件 | 内容 |
| --- | --- |
| `%LOCALAPPDATA%\BabelPlan\app.log` | 桌面壳主日志：启动、服务复用、窗口/托盘、退出、异常堆栈 |
| `%LOCALAPPDATA%\BabelPlan\launch.log` | VBS 启动器每次拉起 pythonw 的一行记录（解释器、工作目录、参数个数） |

`app.log` 超过 2MB 会在下次启动时轮转：`app.log` → `app.log.1` → `app.log.2`
→ `app.log.3`（保留 3 份）。引擎自己的日志不在这里，在管理端「运行日志」区
或项目根下的 `run.log` / `watch.log`。

## 5. 卸载

最省事：`powershell -ExecutionPolicy Bypass -File desktop\install.ps1 -Uninstall`
（默认保留日志；加 `-RemoveLogs` 连日志一起删）。手动步骤：

1. 托盘菜单「退出」；
2. 删除桌面/开始菜单的「巴别塔计划」快捷方式（或跑
   `create_shortcut.ps1 -Remove`）；
3. 可选：删除 `%LOCALAPPDATA%\BabelPlan`（日志）。

项目目录、模型文件、Python 环境都不在这个脚本的卸载范围内，按需自行清理。
快捷方式只是入口，不写注册表、不开机自启。

## 6. 开发者调试

```powershell
# 带控制台调试（日志同时落盘）
python app.py --debug

# 启动后直接收进托盘（不弹窗）
python app.py --tray

# 关窗即退出（不建托盘），用于自动化验证
$env:BABEL_NO_TRAY = "1"; python app.py
```

- 直接验证启动器：`wscript.exe desktop\BabelPlan.vbs`（可带 `--tray` /
  `--keep-engine`，VBS 会原样传给 app.py）。
- 引擎解释器覆盖：`$env:BABEL_PY = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"`，
  app.py 会把它映射成 `babel_admin/dlls.py` 认的 `BABEL_PYTHON`。
- 图标：`python desktop\make_icon.py`（复用 `app.py` 的托盘画法，生成
  16/24/32/48/64/128/256 多尺寸 `desktop\babel.ico`）。
- 打 exe（可选，不是消费者必需）：

  ```powershell
  powershell -ExecutionPolicy Bypass -File build\build_exe.ps1 -InstallPyInstaller -NoQt
  ```

  产物在 `build\_dist\BabelPlan\BabelPlan.exe`。只打**控制台壳**，
  不含 ASR / 翻译模型；引擎仍用本机 Python（`BABEL_PY` 可覆盖）。
  one-folder 产物必须整个目录一起拷贝。

### 为什么没有黑窗（实现要点）

- VBS 用 `pythonw.exe`，并设置 `shell.CurrentDirectory = 项目根`；
- `wscript.Run` 的窗口样式是 **1**，不是 0 —— 0 会把 SW_HIDE 写进
  子进程的 STARTUPINFO，Windows 会因此忽略窗口的第一次 `ShowWindow`，
  表现是「程序开了但窗口看不见」；
- 无控制台的 `app.py` 给所有子进程统一加 `CREATE_NO_WINDOW`
  （解释器探测、引擎拉起都不会新分配 conhost/黑窗）；
- 管理端 / 引擎那边由 `babel_admin/serve.py` 自己再兜一层。

## 7. 常见问题

| 现象 | 先看 |
| --- | --- |
| 双击没反应 | `launch.log` 有没有新行；没有 → 快捷方式目标/`BabelPlan.vbs` 路径不对 |
| 有进程没窗口 | `app.log` 末尾；确认不是 `--tray` 模式；WebView2 运行时是否安装 |
| 提示端口被占用 | 8770 被别的程序占了；日志里会写「不是巴别塔计划的服务」 |
| 托盘没有图标 | `app.log` 里「托盘不可用」；缺 `pystray`/`Pillow` 时关窗即退出 |
| 想换图标 | 改 `app.py` 的 `_tray_image()` 后重跑 `make_icon.py` |
