# 智教伴学

一个可本地运行和部署的校园智能学习系统。学生端保留现有 Streamlit MVP，教师端采用 Vue 3 + FastAPI；所有访问、写入和删除权限都在服务层校验。

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

### 教师端

- 教师账号登录与刷新令牌
- 创建共享课程、学期、班级及导入班级成员
- 上传课程资料并查看持久化解析任务
- 原生 Office/数字 PDF 优先解析，扫描件降级到本地 MinerU
- MinerU 与 Pix2Text 双引擎公式复核，保留原图、页码、bbox 和两份 LaTeX
- DocumentIR 知识块审核及 `PUBLIC/GUIDANCE/ASSESSMENT/VAULT` 可见域
- 资料体检单、任务取消/重试与知识库版本发布
- 学生检索只读取已发布、已验证且属于 `PUBLIC` 的知识块
- 扫描 PDF 支持左侧 Markdown、右侧原页的逐页对照审核
- 原始 PDF/PPTX/DOCX 与检索用 Markdown 分离授权；教师可单独决定学生能否查看原文件
- 教师端默认支持最大 500MB 单文件流式上传，可用 `ZHIJIAO_MAX_UPLOAD_MB` 调整
- 导入学生前必须在真实 `server.env` 或系统环境变量中配置非空的 `ZHIJIAO_STUDENT_DEFAULT_PASSWORD`；短密码允许使用但教师端会显示弱密码警告。数据库仅保存 Argon2 哈希，学生首次登录必须改密。`server.env.example` 仅是模板，不参与运行时读取。
- 教师资料在 DocumentIR 解析后进入独立语义分析队列；必须同时运行 `./start.ps1 worker`。旧资料不会自动消耗 API，需要在“知识中心”逐文档点击“完整重新分析”。
- 语义分析会生成文档独立目录、课程统一目录和知识关系草稿；教师批准后才进入发布版本和学生检索。

### 接口

- `CampusAgentService.invoke()`：只开放 `student_assistant` 和 `teacher_assistant`
- `POST /api/v1/agent/invoke`：统一 Agent HTTP 接口
- `POST /api/v1/documents/upload`：受控文件上传接口
- `GET /api/v1/courses` 与 `GET /health`
- 未知 Action 标准返回 `{"status":"not_implemented","message":"该功能暂未实现"}`

## 快速启动

需要 Python 3.10 或更高版本。

```powershell
cd zhijiao_banxue
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

也可以使用启动脚本：

```powershell
.\start.ps1 ui
```

`cloud` 分支已内置云端中转连接。GitHub 下载者不需要填写千问 API Key，系统默认通过阿里云函数计算中转调用北京地域业务空间中的模型。学生演示账号是 `demo_student_001`，教师演示账号是 `demo_teacher_001`；首次启动会创建公开虚拟课程“人工智能基础（虚拟课程）”并导入两份演示资料。

学生端所有智能能力统一经过 `student_assistant` 编排轻量化 Skill：课程检索与课后答疑、课堂互动练习、作答评价、错题与薄弱点研判。共享课程中的学习反馈只以匿名聚合形式交给 `teacher_assistant`，用于资料覆盖分析和课程内容迭代建议；个人课程数据不会进入教师端。

下载后可先执行以下命令验证云端模型：

```powershell
.\start.ps1 ai-check
```

正常会显示 `SUCCESS: 智能服务连接成功`。真实 `DASHSCOPE_API_KEY` 只保存在阿里云函数计算环境变量中，不会下载到本机、写入 SQLite 或提交到仓库。学生也可在侧栏“AI 服务设置”中选择自己的 OpenAI 兼容 Base URL、API Key 和模型；自定义配置仅保存在本机且不会被 Git 跟踪。

如果是系统管理员需要绕过中转进行本机直连，仍可执行 `.\configure_qwen.ps1`，该模式会把 Key 保存到已被 Git 排除的 `server.env`。

启动 FastAPI：

```powershell
.\start.ps1 api
# API 文档：http://127.0.0.1:8000/docs
```

### 启动 Vue 教师端

首次使用先创建教师账号：

```powershell
D:\anapython\python.exe scripts\create_teacher.py teacher01 --display-name "教师"
```

先在未提交的 `server.env` 中配置远程 MinerU/Pix2Text，再分别打开三个终端：

```powershell
# 终端一：教师 API
.\start.ps1 api

# 终端二：持久化知识入库 Worker
.\start.ps1 worker

# 终端三：Vue 开发服务器
.\start.ps1 web-dev
```

访问 `http://127.0.0.1:5173`。解析容器的安装与检查说明见 [docker/INGESTION.md](docker/INGESTION.md)。

教师共享课程的 PDF 固定由 MinerU `auto` 模式生成规范 Markdown；TXT、Markdown、DOCX、PPTX 有有效文字时只走原生解析。正式部署推荐带 Bearer Token 和 TLS 的校内/远程解析服务；本地 Docker 与 SSH 隧道仅作为可选开发方式。PPTX/DOCX 网页预览还需要服务器安装 LibreOffice，缺失时界面会明确提示并保留原文件下载。

### 教师知识树抽取

教师共享课程统一使用内置证据 Map–Reduce 后端：PDF 读取 MinerU 的完整 Markdown，其他格式读取原生 Markdown；AI 只生成分类、章节、分节、知识点标题和关键词，正文始终由原文块拼接。每个知识点必须绑定真实 `block_id`、页码和逐字证据。该流程不导入 Docling、Torch，也不需要额外模型或 Token。

旧环境中的 `ZHIJIAO_KNOWLEDGE_EXTRACTOR=docling_graph` 会映射到内置后端，并在系统状态中显示弃用提示。

如需修改上传上限：

```powershell
$env:ZHIJIAO_MAX_UPLOAD_MB = "800"
.\start.ps1 api
```

## 测试

```powershell
python -m pytest -q
```

测试覆盖课程越权、课程类型混用、学生修改共享资料、教师访问个人课程、文件安全与重复上传、检索隔离、匿名班级统计、虚拟课程所有权和统一 Agent 接口。

## 数据位置

- SQLite：`data/learning.db`
- 上传文件：`data/uploads/<course_id>/`
- 演示原始资料：`course_materials/`

如需把运行数据放到其他位置，可设置环境变量 `ZHIJIAO_DATA_DIR`。

个人课程不会进入教师统计；教师端的班级分析只读取共享课程数据且不返回学生 ID。Streamlit、FastAPI 和统一 Agent 接口均复用 `CampusService`，不能绕过这些规则。

## GitHub 下载者共享云端 AI

项目支持“云端中转”和“用户自定义 OpenAI 兼容接口”两种方式。真实千问
`DASHSCOPE_API_KEY` 只放在中转服务器环境变量中，不进入 GitHub；下载者默认通过
`relay_client.env` 连接中转服务，也可在学生端侧栏“AI 服务设置”中填写自己的
Base URL、API Key 和模型。本机自定义配置保存在已被 Git 排除的 `user_ai.env`。

完整部署步骤见 [CLOUD_RELAY_DEPLOYMENT.md](CLOUD_RELAY_DEPLOYMENT.md)。
