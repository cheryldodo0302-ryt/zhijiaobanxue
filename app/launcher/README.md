# 智教伴学 Windows 启动器

`智教伴学.exe` 是仓库根目录中的双击启动入口。它会运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\app\start.ps1 -Mode all
```

服务就绪后，启动器会自动打开 `http://127.0.0.1:5173`。运行期间请保留启动窗口；在窗口中按 `Ctrl+C` 可停止本次启动的服务。

启动器必须位于仓库根目录，并与 `app` 文件夹保持同级。`app-icon.ico` 由用户提供的 PNG 图片居中裁剪后生成，包含 16、24、32、48、64、128、256 像素图层。

修改启动器源码后，可在仓库根目录运行 `powershell -ExecutionPolicy Bypass -File .\app\launcher\build.ps1` 重新生成 EXE。
