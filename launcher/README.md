# 智教伴学 Windows 启动器

`智教伴学.exe` 是项目的双击启动入口。它会从 EXE 所在目录运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 -Mode all
```

服务就绪后，启动器会自动打开 `http://127.0.0.1:5173`。运行期间请保留启动窗口；在窗口中按 `Ctrl+C` 可停止本次启动的服务。

启动器必须和项目根目录中的 `start.ps1` 放在一起。`app-icon.ico` 由用户提供的 PNG 图片居中裁剪后生成，包含 16、24、32、48、64、128、256 像素图层。

修改启动器源码后，可在项目根目录运行 `powershell -ExecutionPolicy Bypass -File .\launcher\build.ps1` 重新生成 EXE。
