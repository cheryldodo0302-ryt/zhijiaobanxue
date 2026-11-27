# OpenClaw 本机接入

本目录实现 MCP stdio 服务，注册名为 `zhijiao-database`。OpenClaw 通过独立 Python
子进程调用业务工具，工具复用 `CampusService`，不会向模型开放任意 SQL 或本地文件。
这条链路无需先启动 FastAPI、网页或解析 Worker。

## 一键启动（Windows）

双击仓库根目录的 `启动OpenClaw.cmd`。默认绑定 `demo_teacher_001`。
启动器会检查项目 Python、验证 MCP 握手与课程读取、注册工具、启动本机 Gateway 并打开 Dashboard。
已准备好的 Python 环境会直接复用；缺失时执行 `start.ps1 -Mode setup`。

OpenClaw 与知识库 API 分析共用配置：本机模型入口从绑定教师的
`teacher_ai_settings` 读取接口地址、模型，以及通过数据库旁 `.job-secret.key` 解密的
同一个 API Key。每次请求实时读取，知识库修改模型或密钥后立即跟随，不另存真实密钥，
也不改用系统环境变量里另一份凭据。OpenClaw 配置中的本机随机令牌只用于验证本机
共享入口，并不是上游模型 API Key。教师课程问答工具也复用知识库 API 分析配置。
切换 OpenAI/Gemini 等协议后重新运行启动器以同步协议元数据；同协议内修改模型或密钥
不需要重新填写 OpenClaw 设置。学生绑定使用本人 `account_ai_settings` 保存的自定义接口。

若尚未安装 OpenClaw，启动器会将已验证版本 `2026.11.6` 安装到
`app/.runtime/openclaw-cli`。Node.js 24.18.0 的便携包随附在 `installers`，按官方 SHA-256
校验并解压至项目内，无需新电脑预先安装 Node.js/npm。已完整复制的 `.runtime` 中的
OpenClaw 和 Node.js 会直接复用；没有缓存时首次安装需要联网。
项目始终使用自己的固定版本，不修改电脑上另行安装的 OpenClaw。

本机配置保存在 `app/.runtime/openclaw-state/<机器与安装路径标识>/`，工作区位于其中
`workspace/`。不同电脑、不同安装路径使用独立设备身份、锁与本地配置，启动时重新
生成当前 Python、数据库、密钥读取器和工作区路径。已绑定账号保存在
`app/openclaw.binding.json`，随完整文件夹迁移。

绑定其他已有账号（账号须启用且已修改初始密码）：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\app\launch_openclaw.ps1 -UserId "实际账号ID"
```

`-ConfigureOnly` 只安装/注册并初始化本机配置；`-SkipDashboard` 启动 Gateway 但不打开浏览器。
Gateway 和共享模型入口均仅监听本机，后台日志保存在
`app/.runtime/openclaw-state/<本机标识>/gateway.stdout.log`、`gateway.stderr.log`、
`model-bridge.stdout.log`、`model-bridge.stderr.log`。
本启动器不安装 Windows 计划任务，也不强行终止占用端口的其他程序。

## 检查与诊断

以下命令均从仓库根目录运行；绝对路径入口也支持从任意当前目录调用。

只验证本系统 MCP（不依赖 OpenClaw 安装）：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\app\configure_openclaw.ps1 -CheckOnly
```

注册/更新同名服务：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\app\configure_openclaw.ps1 -UserId "demo_teacher_001" -InstallOpenClaw
```

脚本通过官方 `openclaw config patch --file` 更新本项目服务与模型配置，不调用
`mcp remove`，也不需要强制停止 Gateway。
注册通过 JSON 文件传递，避免 Windows PowerShell 5.1 丢失引号。同一配置重复启动
复用现有进程；正在运行的 Gateway 通过官方配置更新机制接收变化。新电脑与新路径
使用新生成的本机配置，不复用旧机器的进程锁。

OpenClaw 原生诊断（`probe` 在 Gateway 停止时执行）：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\app\openclaw.ps1 mcp status --verbose
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\app\openclaw.ps1 mcp probe zhijiao-database --json
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\app\openclaw.ps1 gateway health
```

`--check` 仅检查数据库与账号；`--probe` 启动独立 MCP 子进程，检查 initialize、tools/list、
connection_status 和 list_courses。两者均可通过绝对路径入口运行：

```powershell
& .\app\.venv\Scripts\python.exe .\app\scripts\run_openclaw_mcp.py --user-id demo_teacher_001 --probe
```

## 工具与权限

| 工具 | 参数 | 用途 |
| --- | --- | --- |
| `zhijiao_connection_status` | 无 | 连接和绑定账号 |
| `zhijiao_list_courses` | 无 | 当前账号可访问课程 |
| `zhijiao_list_documents` | `course_id` | 课程资料元数据 |
| `zhijiao_course_knowledge_status` | `course_id` | 课程知识库状态 |
| `zhijiao_ask_course` | `course_id`, `question` | 课程问答，会保存记录，教师复用知识库 API 分析配置 |
| `zhijiao_learning_profile` | `course_id` | 仅学生本人学习画像 |
| `zhijiao_class_analysis` | `course_id`，可选 `class_id` | 教师自己的共享课程匿名汇总 |

非演示教师使用班情分析时需启用 `server.env` 的 `ZHIJIAO_TEACHER_AGENT_ENABLED=1`。
每次调用会重新检查账号状态；学生不能查看其他学生私有课程、教师不能查看个人学习画像。
问答参数 `question` 最长 2000 字符，课程/教学班 ID 最长 80 字符。

## 手动配置其他 MCP 客户端

- Command：项目实际可运行的 `.venv-windows/Scripts/python.exe` 或 `.venv/Scripts/python.exe` 绝对路径。
- Args：`app/scripts/run_openclaw_mcp.py` 的绝对路径、`--user-id`、账号 ID，可选 `--database`、数据库绝对路径。
- Working directory：`app` 绝对路径（入口自身不依赖 cwd）。
- 启动超时：60 秒；工具调用超时：180 秒。

不传 `--user-id` 时，也支持环境变量 `ZHIJIAO_OPENCLAW_ACCESS_TOKEN`，或
`ZHIJIAO_OPENCLAW_USERNAME` + `ZHIJIAO_OPENCLAW_PASSWORD`，或 `ZHIJIAO_OPENCLAW_USER_ID`
（按此顺序选择）。可用 `ZHIJIAO_OPENCLAW_DB_PATH` 指定数据库；相对路径以 `app` 为基准。
`--user-id` 是显式本机绑定，会优先于继承的旧登录环境变量。数据库路径错误会报错，不会新建空库。

用户 ID 绑定仅用于同一系统账号下可信的本机 stdio 进程，不能作为远程鉴权机制。
目前未提供远程 HTTP MCP 服务；跨机器部署应另行实现 TLS 与鉴权，不能共享 SQLite 文件。
内部 `skills/*/skill_manifest.yaml` 不是 OpenClaw 官方 Skill 格式，也不是兼容认证。

官方参考：[MCP registry](https://docs.openclaw.ai/cli/mcp/registry)、
[Windows 安装](https://docs.openclaw.ai/platforms/windows)。

## 整文件夹迁移到新电脑

1. 迁移前停止系统操作，完整复制仓库文件夹，包含隐藏文件；不要只复制 exe 或 learning.db。
2. 必须保留 `app/data` 的全部内容，尤其是 `.job-secret.key`、数据库和上传资料；这是保留
   已保存 API Key、账号和知识库所需的数据。若自行配置了外部数据目录，也需一起迁移，
   或使用根目录的“导出演示包.cmd”把它收进私有演示包。
3. 保留 `installers`、`app/web/dist`，直接复制时也保留 `.runtime`。新电脑运行
   `智教伴学.exe` 或 `启动OpenClaw.cmd`，启动器会检测复制后失效的虚拟环境，使用随附
   Python 安装器修复；首次准备 Python 依赖需要联网。
4. 使用原账号登录。知识库已保存的接口、模型和 API Key 无需重填；新电脑仍需能联网
   访问对应模型服务，并且原 API Key 有可用额度。

已验证的目标是 Windows x64。不同电脑的 OpenClaw 设备身份和会话目录分开保存，
智教伴学的账号、课程、知识库和加密 API 配置随数据目录保留。
