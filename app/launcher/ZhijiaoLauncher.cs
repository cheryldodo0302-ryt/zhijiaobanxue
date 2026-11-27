using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;

[assembly: System.Reflection.AssemblyTitle("智教伴学启动器")]
[assembly: System.Reflection.AssemblyDescription("一键启动智教伴学本地服务")]
[assembly: System.Reflection.AssemblyCompany("智教伴学")]
[assembly: System.Reflection.AssemblyProduct("智教伴学")]
[assembly: System.Reflection.AssemblyVersion("1.0.0.0")]
[assembly: System.Reflection.AssemblyFileVersion("1.0.0.0")]

internal static class ZhijiaoLauncher
{
    [DllImport("kernel32.dll")]
    private static extern bool SetConsoleCP(uint codePageId);

    [DllImport("kernel32.dll")]
    private static extern bool SetConsoleOutputCP(uint codePageId);

    private const int Utf8CodePage = 65001;

    private static int Main(string[] args)
    {
        SetConsoleCP(Utf8CodePage);
        SetConsoleOutputCP(Utf8CodePage);
        Console.Title = "智教伴学";

        string launcherDirectory = AppDomain.CurrentDomain.BaseDirectory;
        string projectDirectory = Path.Combine(launcherDirectory, "app");
        string startScript = Path.Combine(projectDirectory, "start.ps1");

        if (!File.Exists(startScript))
        {
            Console.ForegroundColor = ConsoleColor.Red;
            Console.WriteLine("启动失败：未找到 app\\start.ps1。");
            Console.ResetColor();
            Console.WriteLine("请把“智教伴学.exe”放回项目根目录后再双击运行。");
            PauseBeforeExit();
            return 2;
        }

        if (args.Length > 0 && string.Equals(args[0], "--check", StringComparison.OrdinalIgnoreCase))
        {
            Console.WriteLine("启动器检查通过：" + startScript);
            return 0;
        }

        if (args.Length > 0 && string.Equals(args[0], "--help", StringComparison.OrdinalIgnoreCase))
        {
            Console.WriteLine("智教伴学启动器");
            Console.WriteLine("  直接运行       自动准备环境并启动桌面版");
            Console.WriteLine("  --setup        仅安装/修复运行环境，不启动服务");
            Console.WriteLine("  --check        检查启动器与项目目录是否匹配");
            return 0;
        }

        bool setupOnly = args.Length > 0 && string.Equals(args[0], "--setup", StringComparison.OrdinalIgnoreCase);
        if (setupOnly)
        {
            Console.WriteLine("正在安装/修复智教伴学运行环境，请勿关闭此窗口……\n");
        }
        else
        {
            Console.WriteLine("正在启动智教伴学，请勿关闭此窗口……");
            Console.WriteLine("服务就绪后将自动打开浏览器。\n");
        }

        // The Python supervisor opens the browser only after verifying this
        // installation's API and worker. A listening port may belong to an old copy.

        ProcessStartInfo startInfo = new ProcessStartInfo();
        startInfo.FileName = "powershell.exe";
        string mode = setupOnly
            ? "setup"
            : "desktop";
        startInfo.Arguments = "-NoLogo -NoProfile -ExecutionPolicy Bypass -File "
            + QuoteArgument(startScript) + " -Mode " + mode;
        startInfo.WorkingDirectory = projectDirectory;
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
                    Console.WriteLine("\n智教伴学启动器已退出，错误代码：" + process.ExitCode);
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
        try
        {
            Console.ReadKey(true);
        }
        catch
        {
            // There may be no interactive console when invoked by another process.
        }
    }
}
