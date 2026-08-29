# 智教伴学

一个可本地运行和部署的校园智能学习系统。当前仓库包含 Streamlit 学生端、Vue 3 + FastAPI 教师端、SQLite 数据层、文档入库 Worker、题库/知识库治理和可选 AI 中转服务；所有访问、写入、删除、检索和统计权限都应在服务层校验。

项目只服务学生学习辅助和教师教学辅助，不包含行政办公端。教师端、教师知识中心和教师 Agent 均已接入统一权限校验；可用 `ZHIJIAO_TEACHER_AGENT_ENABLED=0` 在特殊部署中关闭教师 Agent。

## 当前实现状态

学生端已经覆盖个人课程、资料上传解析、知识块、课程问答、练习、背诵、错题、薄弱点、学习画像和个人数据导出。教师端已经覆盖登录、共享课程、班级成员、资料解析任务、DocumentIR 知识块、知识树、题库审核发布、课程知识库状态、教学分析和报告导出。

教师资料解析可使用原生 Office/PDF 解析、MinerU、Pix2Text 和公式 Worker；教师知识抽取采用绑定真实文档块、页码和证据的流程。知识候选、材料分区、来源预览、教师审批、回收站和发布版本均保留在教师知识中心中。

## 已实现功能

### 学生端

- 创建个人课程；粘贴文本或上传 PDF、DOCX、图片
- PDF/Word 本地精准提取；图片可调用当前配置的多模态模型后保存为课程文字
- 当前 AI 按语义、逻辑段落和关键词密度生成知识块、记忆标题与关键词
- 翻转知识卡片；手动编辑、收藏、拆分或合并知识块
- 关键词挖空、浏览器真人发音与倍速、麦克风耳返和跟读录音
- AI 监督背诵，保存缺失点、错误点和薄弱总结
- 关键词挖空直接填写、提交判分，错误自动进入个人背诵本
- AI 生成单选、多选、判断、简答题，实际作答后由 AI 批改并保存成绩
- 个人画像展示资料、知识块、背诵/练习平均分、薄弱统计、历史成绩和背诵本
- 学生可删除自己的个人课程及其关联学习数据
- AI 智能出题并导出 Word 练习册
- 课程隔离检索、按课件/教材等材料分区检索、带文件名/章节/页码/证据片段的个性化答疑
- 引导式问答会话在服务端保存，至少完成两次自主思考后才可揭示课程答案

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
- PDF 导入先执行本地 `DocumentInspect` 和 `PageRouter`，按封面/目录/正文/扫描页选择 `SKIP/FAST/STRUCTURE/NORMAL/DEEP`，默认按 40 页批次运行。
- 每份 PDF 保留 `RAW → NORMALIZED → APPROVED` 目录层次；原始 MinerU 响应和原生文本通道写入 `raw/`，规范化块写入 `normalized/blocks.jsonl`，教师审核前不会写入 `approved/knowledge_points.jsonl`。
- 每页和每批都有 `manifest.json` checkpoint、文本量校验、缺页/异常页状态和错误信息；重试会复用已完成批次，只重新处理失败或可疑批次。
- 导入后会执行本地章节树构建和目录对照校验；目录与正文标题不一致时生成 `STRUCTURE_WARNING`，不会默默丢弃章节。
- 知识边界候选以原始 `document_blocks` 为唯一正文来源，支持区域分类、父区域继承和教师批准/驳回；批准后写入 `approved/knowledge_points.jsonl`。
- PPTX 会执行 `PptFastInspector`，保存页级类型、形状数量、二维阅读顺序和复杂度信息，不在 Fast Inspect 阶段调用视觉模型。

### 接口

- `CampusAgentService.invoke()`：只开放 `student_assistant` 和 `teacher_assistant`
- `POST /api/v1/agent/invoke`：统一 Agent HTTP 接口
- `POST /api/v1/documents/upload`：受控文件上传接口
- `GET /api/v1/courses` 与 `GET /health`
- 未知 Action 标准返回 `{"status":"not_implemented","message":"该功能暂未实现"}`

主要接口按领域分为 `/api/v1/auth/*`、`/api/v1/student/*`、`/api/v1/teacher/*`、`/api/v1/agent/invoke` 和受控文件上传接口。若部署方显式关闭教师 Agent，则返回 `disabled`，不得通过其他适配器绕过开关。

## 目录职责

```text
zhijiao_banxue/
├── app.py                         # Streamlit 学生端
├── api.py                         # FastAPI 接口
├── campus_service.py              # 课程、资料、问答、练习和统计
├── agent_service.py               # Agent 校验和 Action 路由
├── teacher_service.py             # 教师领域服务
├── database.py / migrations.py    # SQLite 数据层
├── ingestion_service.py           # 文档解析和入库任务
├── document_ir.py                 # 文档块、证据和中间表示
├── semantic_knowledge_service.py  # 知识树和知识关系
├── question_bank_service.py       # 题库治理
├── adaptive_ingestion.py          # PDF Inspect、路由、批次、manifest、校验和 JSONL 规范化
├── knowledge_ingestion.py         # 章节/目录校验、区域分类、知识边界候选、PPT Fast Inspect
├── skills/                        # 学生问答、检索、练习、画像和记忆 Skill
├── relay/                         # 可选 AI 中转服务
├── workers/                       # 公式 Worker
├── web/                           # Vue 3 前端
├── scripts/                       # 启动、建号和 Worker 脚本
├── tests/                         # Python 测试
├── course_materials/              # 演示资料
└── data/                          # SQLite、上传文件和运行数据
```

`data/`、`web/node_modules/`、`web/dist/`、Python 缓存和临时目录属于本地运行或构建产物，不应提交；密钥、本机 AI 配置和真实账号信息也不得提交。`reports/` 属于项目文档产物，应单独保留。

## 快速启动

支持 Python 3.10、3.11 和 3.12；前端需要 Node.js 20 或更高版本。启动器会在项目目录创建隔离虚拟环境，不会替换系统 Python 或 Anaconda。

```powershell
cd zhijiao_banxue
.\start.cmd
```

也可以按模块启动：

```powershell
.\start.ps1 -Mode all       # FastAPI + Worker + Vue 学生/教师端
.\start.ps1 -Mode ui        # Streamlit 学生端
.\start.ps1 -Mode test      # 后端测试
.\start.ps1 -Mode web-build # 前端生产构建
```

无任何 AI 配置时系统默认使用确定性 Mock：不联网、不需要 API Key，相同输入得到稳定结果，适合开箱运行、测试和演示。也可切换到内置云中转、OpenAI 兼容接口、Google Gemini 或本机 Ollama。首次启动会在空数据库中创建完全虚构的演示账号与课程，随机密码写入本机 `data/demo_credentials.txt`，不会提交到 Git。

学生端所有智能能力统一经过 `student_assistant` 编排轻量化 Skill：课程检索与课后答疑、课堂互动练习、作答评价、错题与薄弱点研判。共享课程中的学习反馈只以匿名聚合形式交给 `teacher_assistant`，用于资料覆盖分析和课程内容迭代建议；个人课程数据不会进入教师端。

下载后可先执行以下命令验证当前 AI 模式：

```powershell
.\start.ps1 ai-check
```

Mock 模式会显示 `offline://deterministic-mock` 和 `SUCCESS`；其他模式会检查 DNS、TCP 和模型响应。真实密钥只允许放在未提交的环境文件或部署环境变量中，不写入 SQLite。学生侧栏支持 Mock、云中转和自定义接口；教师知识中心还支持单独保存加密的 OpenAI/Gemini 配置或无需 Key 的本机 Ollama。

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

需要远程解析时，先在未提交的 `server.env` 中配置 MinerU/Pix2Text；随后一个命令即可启动 API、Worker 和 Vue：

```powershell
.\start.ps1 -Mode all
```

访问 `http://127.0.0.1:5173`。解析容器的安装与检查说明见 [docker/INGESTION.md](docker/INGESTION.md)。

教师共享课程的 PDF 由自适应管线分批调用 MinerU：普通页使用 `auto`，扫描/复杂公式页升级到 `ocr`；TXT、Markdown、DOCX、PPTX 有有效文字时仍只走原生解析。正式部署推荐带 Bearer Token 和 TLS 的校内/远程解析服务；本地 Docker 与 SSH 隧道仅作为可选开发方式。PPTX/DOCX 网页预览还需要服务器安装 LibreOffice，缺失时界面会明确提示并保留原文件下载。

PDF 自适应导入的默认批次大小为 40 页，可在 `server.env` 中通过 `ZHIJIAO_INGESTION_BATCH_SIZE` 调整。教师可通过以下接口查看页级清单和批次恢复状态：

```text
GET /api/v1/teacher/documents/{document_id}/manifest
GET /api/v1/teacher/documents/{document_id}/pages
GET /api/v1/teacher/documents/{document_id}/pages/{page_number}
GET /api/v1/teacher/documents/{document_id}/structure
GET /api/v1/teacher/documents/{document_id}/slides
GET /api/v1/teacher/documents/{document_id}/knowledge-candidates
PATCH /api/v1/teacher/knowledge-candidates/{candidate_id}
POST /api/v1/teacher/knowledge-candidates/{candidate_id}/approve
POST /api/v1/teacher/knowledge-candidates/{candidate_id}/reject
```

导入阶段不调用大模型重写或总结教材正文；章节结构、区域标签、知识边界候选和 PPT 阅读顺序均是可解释的本地元数据。教师批准候选后才写入 `approved` 层，现有语义知识树仍由教师单独触发，并继续绑定原始 `document_blocks`。

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

前端测试和生产构建：

```powershell
cd web
npm run test
npm run build
```

当前测试重点还包括教师知识治理、题库审核/发布、解析 Worker、公式适配器、AI 配置和中转服务。建议在干净 Python 环境中执行完整后端测试；如果缺少 `pytest` 或文档解析依赖，启动脚本会提示安装缺失依赖。

## 数据位置

- SQLite：`data/learning.db`
- 上传文件：`data/uploads/<course_id>/`
- 演示原始资料：`course_materials/`

如需把运行数据放到其他位置，可设置环境变量 `ZHIJIAO_DATA_DIR`。

个人课程不会进入教师统计；教师端的班级分析只读取共享课程数据且不返回学生 ID。Streamlit、FastAPI 和统一 Agent 接口均复用 `CampusService`，不能绕过这些规则。

## 开发与安全规则

- 新功能必须属于学生学习辅助或教师教学辅助范围，不得新增行政功能；
- `personal_course` 与 `shared_course` 的资料、索引、检索和统计链路必须隔离；
- 学生不能修改共享课程资料，教师不能访问个人课程或私有资料；
- 上传必须检查扩展名、MIME、大小、空文件、文件名安全、路径穿越和重复哈希；
- OpenClaw/Agent 只能使用受控文件内容、引用或文件 ID，不能访问任意本地文件；
- 未实现能力必须返回标准 `not_implemented`，不得用占位数据伪造分析；
- Streamlit、FastAPI、Vue、CLI、Worker 和 OpenClaw 适配器必须复用服务层权限规则。

完整项目边界、Agent 规则、目录职责、安全要求和验收标准见仓库根目录 [AGENTS.md](../AGENTS.md)。

## GitHub 下载者共享云端 AI

项目支持确定性 Mock、内置云中转、OpenAI 兼容接口、Google Gemini 和 Ollama。
无配置时固定使用 Mock；真实上游密钥只放在中转服务器或本机未提交的环境文件中，
不进入 GitHub。学生本机选择保存在已被 Git 排除的 `user_ai.env`；教师自有密钥由
服务端加密后保存。Ollama 可使用 `http://127.0.0.1:11434/v1` 且无需 API Key。

完整部署步骤见 [CLOUD_RELAY_DEPLOYMENT.md](CLOUD_RELAY_DEPLOYMENT.md)。
