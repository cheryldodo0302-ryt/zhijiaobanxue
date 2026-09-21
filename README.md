# 智教伴学

这是智教伴学当前发布仓库。正式的学生端 + 教师端统一系统位于 [`zhijiao_banxue/`](zhijiao_banxue/)，请以其中的 README、启动脚本和源码为准。根目录的 `start.ps1` 转发到同一套启动器。

当前版本已覆盖学生学习辅助与教师教学辅助：个人/共享课程、文档解析、知识中心审核发布、知识卡片 AI 语义拆分、课程练习、错题与学习画像、AI 自习室、教学班与教学档案、知识图谱、题库和匿名班级学情分析。项目不包含行政办公端。

## 已实现功能

### 学生端

- 创建个人课程；粘贴文本或上传 PDF、DOCX、图片
- PDF/Word 本地精准提取；图片调用千问 OCR 后保存为课程文字
- 千问按语义、逻辑段落和关键词密度生成知识块、记忆标题与关键词
- 翻转知识卡片；手动编辑、收藏、拆分或合并知识块
- 关键词挖空、浏览器真人发音与倍速、麦克风耳返和跟读录音
- AI 监督背诵，保存缺失点、错误点和薄弱总结
- 关键词挖空直接填写、提交判分，错误自动进入个人背诵本
- AI 生成单选、多选、判断、简答题，实际作答后由 AI 批改并保存成绩
- 个人画像展示资料、知识块、背诵/练习平均分、薄弱统计、历史成绩和背诵本
- 学生可删除自己的个人课程及其关联学习数据
- AI 智能出题并导出 Word 练习册
- 课程隔离检索、带文件名/章节/页码/证据片段的个性化答疑

### 教师端状态

教师端已包含共享课程、教学班、资料解析、知识中心、知识图谱、题库、教学诊断和报告导出。统一 Agent 是否开放由 `ZHIJIAO_TEACHER_AGENT_ENABLED` 控制；本地默认关闭，Docker 示例默认开启。关闭时服务返回 `disabled`，不能通过其他适配器绕过。

### 接口

- `CampusAgentService.invoke()`：只开放 `student_assistant` 和 `teacher_assistant`
- `POST /api/v1/agent/invoke`：统一 Agent HTTP 接口
- `POST /api/v1/documents/upload`：受控文件上传接口
- `GET /api/v1/courses` 与 `GET /health`
- 未知 Action 标准返回 `{"status":"not_implemented","message":"该功能暂未实现"}`

## 快速启动

请先安装 Python 3.10、3.11 或 3.12（推荐 3.12），以及 Node.js 22.12+ 或 24。首次启动需要联网下载 Python 和 npm 依赖。

使用 Git 下载：

```powershell
git clone --branch main https://github.com/cheryldodo0302-ryt/zhijiaobanxue.git
cd zhijiaobanxue
```

也可以在 GitHub 选择 **Code → Download ZIP**，解压后进入包含本 README 的目录。Windows 启动：

```powershell
cd zhijiao_banxue
.\start.ps1 -Mode all
```

若 Windows 提示脚本执行被禁用，可在同一目录运行 `powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 -Mode all`。macOS/Linux 使用 `cd zhijiao_banxue` 后执行 `sh start.sh all`。

常用模式：

```powershell
.\start.ps1 -Mode api
.\start.ps1 -Mode worker
.\start.ps1 -Mode web-dev
.\start.ps1 -Mode test
.\start.ps1 -Mode ai-check
```

访问 `http://127.0.0.1:5173`；API 文档为 `http://127.0.0.1:8000/docs`。完整启动、AI 配置、教师资料审核、权限和测试说明见 [`zhijiao_banxue/README.md`](zhijiao_banxue/README.md) 与 [`zhijiao_banxue/SYSTEM_USER_GUIDE.md`](zhijiao_banxue/SYSTEM_USER_GUIDE.md)。

默认 AI 模式是离线确定性 Mock，不需要 API Key；也可以在 `zhijiao_banxue` 中配置云中转、OpenAI 兼容接口、Gemini 或 Ollama。空数据库首次启动时生成的演示凭据写入本机 `zhijiao_banxue/data/demo_credentials.txt`，不会提交到 Git。

学生端智能能力通过 `student_assistant` 编排 Skill；教师端复用统一服务层权限校验。常规班级学情使用匿名聚合；任课教师可查看本班正式任务的实名画像及学生主动授权的自习汇总，个人课程和私人问答不进入教师端。

本地教师功能默认关闭。需要使用教师端时，在 `zhijiao_banxue/server.env` 中设置 `ZHIJIAO_TEACHER_AGENT_ENABLED=1`，重启服务后使用本机生成的演示教师账号登录。该文件不上传到 GitHub；完整配置见项目内的 `server.env.example`。

下载后可先执行以下命令验证云端模型：

```powershell
.\start.ps1 ai-check
```

Mock 模式会显示离线检查成功；其他模式会检查网络、模型配置和响应。真实密钥只能放在未提交的环境文件或部署环境变量中。学生可以选择自己的 OpenAI 兼容 Base URL、API Key 和模型；自定义配置仅保存在本机且不会被 Git 跟踪。

如果是系统管理员需要绕过中转进行本机直连，仍可执行 `.\configure_qwen.ps1`，该模式会把 Key 保存到已被 Git 排除的 `server.env`。

单独启动 FastAPI：

```powershell
.\start.ps1 api
# API 文档：http://127.0.0.1:8000/docs
```

## 测试

```powershell
cd zhijiao_banxue
python -m pytest -q
```

测试覆盖课程越权、课程类型混用、学生修改共享资料、教师访问个人课程、文件安全与重复上传、检索隔离、匿名班级统计、虚拟课程所有权和统一 Agent 接口。

## 数据位置

- SQLite：`zhijiao_banxue/data/learning.db`
- 上传文件：`zhijiao_banxue/data/uploads/<course_id>/`
- 演示原始资料：`zhijiao_banxue/course_materials/`

如需把运行数据放到其他位置，可设置环境变量 `ZHIJIAO_DATA_DIR`。

个人课程不会进入教师统计；班级匿名分析和正式任务实名画像遵守各自的授权范围。Vue、FastAPI 和统一 Agent 接口均复用服务层，不能绕过这些规则。

源码包含启动脚本、依赖清单及前端锁文件、配置模板、示例课程和自习室浏览器模型。运行数据库、上传资料、真实密钥、虚拟环境、`node_modules` 和 `dist` 由下载者本机配置或生成，不随源码分发。

## GitHub 下载者共享云端 AI

项目支持“云端中转”和“用户自定义 OpenAI 兼容接口”两种方式。真实千问
`DASHSCOPE_API_KEY` 只放在中转服务器环境变量中，不进入 GitHub；下载者默认使用离线 Mock 演示模式。真实 AI 能力需要自行配置服务：可参考
`zhijiao_banxue/relay_client.env.example` 创建本机 `relay_client.env` 连接中转服务，也可在“AI 服务设置”中填写自己的
Base URL、API Key 和模型。本机自定义配置保存在已被 Git 排除的 `user_ai.env`。

完整部署步骤见 [云端 AI 中转部署说明](zhijiao_banxue/CLOUD_RELAY_DEPLOYMENT.md)。高级扫描件/公式解析所需的可选 Worker 见 [文档解析部署说明](zhijiao_banxue/docker/INGESTION.md)。
