// BabelPlan 原生启动器（C#，无 .NET 运行时解包，启动即开）
// 目标：双击 BabelPlan.exe 立即转交 desktop\BabelPlan.vbs，exe 内嵌 babel.ico。
using System;
using System.Diagnostics;
using System.IO;

internal static class Launcher
{
    [STAThread]
    private static void Main()
    {
        string exeDir = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');
        string root = exeDir;
        if (string.Equals(Path.GetFileName(exeDir), "desktop",
                          StringComparison.OrdinalIgnoreCase))
        {
            DirectoryInfo parent = Directory.GetParent(exeDir);
            if (parent != null) root = parent.FullName;
        }
        string vbs = Path.Combine(root, "desktop", "BabelPlan.vbs");
        if (!File.Exists(vbs))
        {
            // exe 放在项目根时也兼容
            vbs = Path.Combine(exeDir, "desktop", "BabelPlan.vbs");
        }
        if (!File.Exists(vbs)) return;
        var psi = new ProcessStartInfo
        {
            FileName = "wscript.exe",
            Arguments = "\"" + vbs + "\"",
            WorkingDirectory = root,
            UseShellExecute = true,
            WindowStyle = ProcessWindowStyle.Hidden
        };
        Process.Start(psi);
    }
}
