using System;
using System.Diagnostics;
using System.IO;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Threading;

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
    private const int WebPort = 5173;
    private const string WebUrl = "http://127.0.0.1:5173";

    private static int Main(string[] args)
    {
        SetConsoleCP(Utf8CodePage);
        SetConsoleOutputCP(Utf8CodePage);
        Console.Title = "智教伴学";

        string launcherDirectory = AppDomain.CurrentDomain.BaseDirectory;
        string startScript = Path.Combine(launcherDirectory, "start.ps1");

        if (!File.Exists(startScript))
        {
            Console.ForegroundColor = ConsoleColor.Red;
            Console.WriteLine("启动失败：未在 EXE 所在目录找到 start.ps1。");
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

        Console.WriteLine("正在启动智教伴学，请勿关闭此窗口……");
        Console.WriteLine("服务就绪后将自动打开浏览器。\n");

        Thread browserThread = new Thread(OpenBrowserWhenReady);
        browserThread.IsBackground = true;
        browserThread.Start();

        ProcessStartInfo startInfo = new ProcessStartInfo();
        startInfo.FileName = "powershell.exe";
        startInfo.Arguments = "-NoLogo -NoProfile -ExecutionPolicy Bypass -File "
            + QuoteArgument(startScript) + " -Mode all";
        startInfo.WorkingDirectory = launcherDirectory;
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

    private static void OpenBrowserWhenReady()
    {
        DateTime deadline = DateTime.UtcNow.AddMinutes(3);
        while (DateTime.UtcNow < deadline)
        {
            try
            {
                using (TcpClient client = new TcpClient())
                {
                    IAsyncResult result = client.BeginConnect("127.0.0.1", WebPort, null, null);
                    if (result.AsyncWaitHandle.WaitOne(500) && client.Connected)
                    {
                        client.EndConnect(result);
                        ProcessStartInfo browser = new ProcessStartInfo();
                        browser.FileName = WebUrl;
                        browser.UseShellExecute = true;
                        Process.Start(browser);
                        return;
                    }
                }
            }
            catch
            {
                // The local web server is still starting; retry quietly.
            }
            Thread.Sleep(750);
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
