# 智教伴学 Windows 启动器

`智教伴学.exe` 是仓库根目录中的双击启动入口。首次运行会自动修复复制过来的 Python 环境；没有兼容 Python 时使用随附安装器自动安装项目专用 Python，然后运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\app\start.ps1 -Mode desktop
```

桌面版直接托管 `app/web/dist` 中已构建的前端，新电脑无需 Node.js。服务及 Worker 校验通过后自动打开浏览器，默认地址为 `http://127.0.0.1:5173`；该端口被旧界面或其他程序占用时自动选择其他入口，不会误打开旧界面。首次安装 Python 依赖仍需联网。运行期间请保留启动窗口；在窗口中按 `Ctrl+C` 可停止本次启动的服务。

如果只想安装或修复依赖而不启动服务，可以运行：

```powershell
.\智教伴学.exe --setup
```

也可以双击项目根目录的 `安装智教伴学.cmd`。启动器还支持 `--check` 和 `--help`。

项目根目录的 `Uninstall.exe` 或 `卸载智教伴学.cmd` 会删除 `.venv*`、`web\node_modules`、前端构建产物、临时目录和测试缓存；源码、数据库、课程资料、上传文件、环境配置和卸载器本身会保留。卸载器默认要求输入 `YES` 确认，也支持 `Uninstall.exe --yes`。

启动器必须位于仓库根目录，并与 `app` 文件夹保持同级。`app-icon.ico` 由用户提供的 PNG 图片居中裁剪后生成，包含 16、24、32、48、64、128、256 像素图层。

修改启动器源码后，可在仓库根目录运行 `powershell -ExecutionPolicy Bypass -File .\app\launcher\build.ps1` 重新生成 EXE。
