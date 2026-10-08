# OpenClaw 数据库接入

本目录提供可直接连接 OpenClaw 的 MCP stdio 服务。它不会把 SQLite 或任意 SQL
暴露给模型，而是以一个智教伴学账号调用现有 `CampusService`，因此课程权限、学生
隐私与教师数据范围和网页/API 完全一致。

## 1. 本地连通检查

PowerShell：

```powershell
$env:ZHIJIAO_OPENCLAW_USER_ID = "demo_teacher_001"
& .\.venv\Scripts\python.exe -m integrations.openclaw_adapter.server --check
```

看到 `"connected": true` 即表示 MCP 服务已经连到 `data/learning.db`。正式使用时请
把示例用户 ID 换成要绑定的账号 ID。可选的 `ZHIJIAO_OPENCLAW_DB_PATH` 能指定其他
数据库文件。

## 2. 注册到 OpenClaw

先安装并确认 `openclaw --version` 可用，然后在项目根目录执行：

```powershell
.\configure_openclaw.ps1 -UserId "demo_teacher_001"
```

脚本会注册名为 `zhijiao-database` 的本地 stdio MCP server，并运行 OpenClaw 的
实时探测。若同名配置已经存在，可使用 `-Replace` 更新。

也可以在 OpenClaw Settings → MCP 手工添加：

- Transport: `Stdio`
- Command: 本项目 `.venv\Scripts\python.exe` 的绝对路径
- Arguments: `-m`, `integrations.openclaw_adapter.server`
- Working directory: 项目根目录的绝对路径
- Environment: `ZHIJIAO_OPENCLAW_USER_ID=<账号ID>`

OpenClaw 中出现以下工具就表示接入成功：

- `zhijiao_connection_status`
- `zhijiao_list_courses`
- `zhijiao_list_documents`
- `zhijiao_course_knowledge_status`
- `zhijiao_ask_course`
- `zhijiao_learning_profile`（学生）
- `zhijiao_class_analysis`（教师）

## 安全说明

`USER_ID` 方式只适合与数据库在同一台机器、同一系统账号下运行的 stdio 连接；该
OpenClaw 进程本来就具有读取本地数据库文件的权限。跨机器部署不要共享 SQLite，
应改用带 TLS 与鉴权的远程 MCP/API 服务。也可以通过 OpenClaw secret 向本服务传入
`ZHIJIAO_OPENCLAW_ACCESS_TOKEN`，或 `ZHIJIAO_OPENCLAW_USERNAME` 与
`ZHIJIAO_OPENCLAW_PASSWORD`。
