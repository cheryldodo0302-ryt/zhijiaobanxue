using System;
using System.Diagnostics;
using System.IO;

[assembly: System.Reflection.AssemblyTitle("智教伴学卸载器")]
[assembly: System.Reflection.AssemblyDescription("删除智教伴学本地运行环境")]
[assembly: System.Reflection.AssemblyCompany("智教伴学")]
[assembly: System.Reflection.AssemblyProduct("智教伴学")]
[assembly: System.Reflection.AssemblyVersion("1.0.0.0")]
[assembly: System.Reflection.AssemblyFileVersion("1.0.0.0")]

internal static class UninstallLauncher
{
    private static int Main(string[] args)
    {
        Console.Title = "智教伴学卸载器";
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string script = Path.Combine(root, "app", "uninstall.ps1");

        if (!File.Exists(script))
        {
            Console.ForegroundColor = ConsoleColor.Red;
            Console.WriteLine("卸载失败：未找到 app\\uninstall.ps1。");
            Console.ResetColor();
            PauseBeforeExit();
            return 2;
        }

        bool yes = args.Length > 0 && string.Equals(args[0], "--yes", StringComparison.OrdinalIgnoreCase);
        Console.WriteLine("智教伴学卸载器");
        Console.WriteLine("此操作只删除运行环境和构建缓存，不删除源码和用户数据。\n");

        ProcessStartInfo startInfo = new ProcessStartInfo();
        startInfo.FileName = "powershell.exe";
        startInfo.Arguments = "-NoLogo -NoProfile -ExecutionPolicy Bypass -File "
            + QuoteArgument(script) + (yes ? " -Yes" : "");
        startInfo.WorkingDirectory = root;
        startInfo.UseShellExecute = false;
        startInfo.CreateNoWindow = false;

        try
        {
            using (Process process = Process.Start(startInfo))
            {
                process.WaitForExit();
                if (process.ExitCode != 0)
                {
                    Console.ForegroundColor = ConsoleColor.Red;
                    Console.WriteLine("\n卸载失败，错误代码：" + process.ExitCode);
                    Console.ResetColor();
                    PauseBeforeExit();
                }
                return process.ExitCode;
            }
        }
        catch (Exception error)
        {
            Console.ForegroundColor = ConsoleColor.Red;
            Console.WriteLine("\n无法启动 PowerShell：" + error.Message);
            Console.ResetColor();
            PauseBeforeExit();
            return 1;
        }
    }

    private static string QuoteArgument(string value)
    {
        return "\"" + value.Replace("\"", "\\\"") + "\"";
    }

    private static void PauseBeforeExit()
    {
        Console.WriteLine("按任意键关闭窗口……");
        try { Console.ReadKey(true); } catch { }
    }
}
