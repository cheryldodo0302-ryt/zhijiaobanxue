# 智教伴学（学生学习辅助与教师教学辅助系统）

一个可本地运行和部署的校园智能学习系统。当前仓库使用 Vue 3 作为学生与教师的统一前端，并包含 FastAPI、SQLAlchemy 管理的 SQLite 数据层、FAISS 课程检索、文档入库 Worker、题库/知识库治理和可选 AI 中转服务；所有访问、写入、删除、检索和统计权限都应在服务层校验。

项目只服务学生学习辅助和教师教学辅助，不包含行政办公端。教师端、教师知识中心和教师 Agent 均已接入统一权限校验。教师能力默认关闭；在真实 `server.env` 或进程环境中设置 `ZHIJIAO_TEACHER_AGENT_ENABLED=1` 后重启即可开放，设置为 `0` 同时关闭教师 REST 和 Agent 能力。进程环境优先于配置文件；示例文件不参与运行配置。

## 2026-09 审查修复

- 课程练习、挖空、AI 练习和背诵使用服务端保存的题目或内容快照判分，绑定账号与课程；重复提交返回已有结果，不重复计入学情。教师题库使用发布题目快照及提交回执。旧页面中尚未保存试卷的练习需要重新生成。
- 发布知识与题库冻结正文、答案、来源及班级范围，草稿修改必须重新发布才进入学生端。撤回只作用于指定课程的指定版本，由课程所有教师确认。学生卡片保留来源、支持合并，教师更新后提示新版而不覆盖个人编辑。
- 检索、卡片、题库及原文件预览统一检查当前班级访问范围。新授权区分手工授权与班级授权，班级调动或撤销不会误删其他仍有效的授权。
- 问答、课程练习、挖空/背诵、AI 练习和发布题库汇入同一学习统计口径，分数统一为百分制。新事件保存发生时的班级，历史班级无法确定的记录只计课程汇总。学生个人课程、个人导入题库和自习数据不进入教师学情。
- 习题中心的学习统计统一移至教学诊断，与课程学习概况共用课程和教学班筛选；保留参与人数、有效作答、正确率、习题薄弱点及高错误率题目排行，习题中心只保留题库管理和统计跳转。
- 习题薄弱点和题目排行读取对应发布版本的知识点与题干快照；综合学情优先使用作答事件快照，旧事件按实际作答版本读取发布快照。编辑草稿不会重新归类旧成绩；缺少历史快照时保留成绩，不用当前标签推测归属。
- 批量审核发布保存进度和发布回执；上传保留失败队列，刷新后重新选择原目录即可匹配未完成文件。课程、题库筛选、教学范围和档案导入默认值按账号、课程记忆。
- 改密、重置密码或退出会撤销该账号所有设备的旧访问、刷新及预览令牌。密码哈希和首次受控文件上传的哈希校验继续保留；重复操作通过记录 ID、唯一约束和事务去重。

旧数据库的发布内容迁移使用 `scripts/upgrade_review_data.py` 显式执行：先停止服务，备份并验证副本，再加 `--apply` 更新原库。旧版本只能冻结迁移时尚存的内容，无法还原此前已经被覆盖的正文。普通启动不自动补写历史学习事件或批量重构已发布正文。

```powershell
.\.venv\Scripts\python.exe scripts\upgrade_review_data.py --database data\learning.db --backup-dir data\backups\review-upgrade
# 确认副本校验通过后，在服务停止时应用：
.\.venv\Scripts\python.exe scripts\upgrade_review_data.py --database data\learning.db --backup-dir data\backups\review-upgrade --apply
```

服务端判分用于学习辅助，部分自学/导出接口仍提供答案，不是保密考试系统；主观题质量仍取决于模型。公式 Worker 单独部署时必须在其进程中设置 `ZHIJIAO_FORMULA_TOKEN`，客户端使用相同 Bearer Token；单次图片限制 10MB、2500 万像素。

## 当前实现状态

学生端已经覆盖个人课程、资料上传解析、知识块、课程问答、练习、背诵、错题、薄弱点、学习画像和个人数据导出。教师端已经覆盖登录、共享课程、班级成员、资料解析任务、DocumentIR 知识块、知识树、题库审核发布、课程知识库状态、教学分析和报告导出。

当前版本另外包含以下面向实际使用的闭环：

- 学生端会记忆当前课程；教师共享课程中已发布的知识点可以导入学生知识卡片。
- 长知识卡片支持点击“AI 语义拆分”后预览语块，确认后再生成独立卡片；内容会使用当前已配置的 AI 服务商。
- 关键词挖空有关键词时使用手动关键词；未填写关键词时，只有点击“生成挖空”才会请求 AI 分析重点，避免在输入过程中发送内容。
- 单选、多选、判断和简答题在学生端、教师统计、错题本和 Word 导出中统一显示中文；判断题显示“判断题”和“对/错”，内部仍兼容历史 `T/F` 值。
- AI 自习室的专注度和实时分由浏览器本地视觉/状态分析产生，保存时只向后端提交脱敏后的分数与学习记录，不上传视频。
- 教师教学班支持新建、编辑课程/学年学期/班级/教学范围；教学档案、实验矩阵、考核方案和知识点可按教学层级归并，并保留考核来源文件。
- 知识中心提供文档解析、AI 语义分析、中文进度标签、预览、分类、知识点审核、整本资料审核、发布和知识图谱同步；已发布知识可在知识图谱中核对，待审核知识点从知识中心进入审核。

教师资料解析可使用原生 Office/PDF 解析、MinerU、Pix2Text 和公式 Worker；教师知识抽取采用绑定真实文档块、页码和证据的流程。知识候选、材料分区、来源预览、教师审批、回收站和发布版本均保留在教师知识中心中。

## 已实现功能

### 学生端

- 创建个人课程，记忆当前课程；粘贴文本或上传 PDF、DOCX、PPTX、Markdown、TXT 和图片
- PDF/Word 本地精准提取；图片可调用当前配置的多模态模型后保存为课程文字
- 当前 AI 按语义、逻辑段落和关键词密度生成知识块、记忆标题与关键词
- 翻转知识卡片；手动编辑、收藏、拆分或合并知识块，也可导入教师已发布知识点
- 点击“AI 语义拆分”后预览和确认长知识点语块；点击“生成挖空”后才会发送内容给当前 AI 服务商分析重点
- 关键词挖空、浏览器真人发音与倍速、麦克风耳返和跟读录音
- AI 监督背诵，保存缺失点、错误点和薄弱总结
- 关键词挖空直接填写、提交判分，错误自动进入个人背诵本
- AI 生成单选、多选、判断、简答题，实际作答后由 AI 批改并保存成绩；判断题和答案在界面中显示为中文“判断题/对/错”
- 个人画像展示资料、知识块、背诵/练习平均分、薄弱统计、历史成绩和背诵本
- 学生可删除自己的个人课程及其关联学习数据
- AI 智能出题并导出 Word 练习册
- AI 自习室保存专注度、实时分和学习记录；视频分析在浏览器本地完成
- 课程隔离检索、按课件/教材等材料分区检索、带文件名/章节/页码/证据片段的个性化答疑
- 引导式问答会话在服务端保存，至少完成两次自主思考后才可揭示课程答案

### 教师端

- 教师账号登录与刷新令牌
- 创建共享课程、学期、教学班及导入班级成员；支持编辑课程、学年学期、教学班名称、版本和教学范围
- 教学班、教学档案、实验矩阵、考核方案和知识点支持教学层级标注与归并；考核方案保留来源文件，便于同一门课合并核对
- 上传课程资料并查看持久化解析任务
- 原生 Office/数字 PDF 优先解析，扫描件降级到本地 MinerU
- MinerU 与 Pix2Text 双引擎公式复核，保留原图、页码、bbox 和两份 LaTeX
- DocumentIR 知识块审核及 `PUBLIC/GUIDANCE/ASSESSMENT/VAULT` 可见域
- 知识中心提供预览审核、分类确认、知识点审核和“整本资料已审核”并列入口；进度标签使用中文业务状态
- 资料体检单、任务取消/重试与知识库版本发布
- 知识图谱可查看已发布知识、待审核知识点和知识中心差异，并支持将已发布知识同步到图谱
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
├── api.py                         # FastAPI 接口
├── campus_service.py              # 课程、资料、问答、练习和统计
├── agent_service.py               # Agent 校验和 Action 路由
├── teacher_service.py             # 教师领域服务
├── database.py / migrations.py    # SQLAlchemy + SQLite 数据层
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
└── data/                          # 本地数据库、上传文件和运行数据（禁止提交）
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
.\start.ps1 -Mode test      # 后端测试
.\start.ps1 -Mode web-build # 前端生产构建
```

启动成功后，统一入口为 `http://127.0.0.1:5173`，API 文档为 `http://127.0.0.1:8000/docs`。旧的 `ui` 启动模式已移除。

无任何 AI 配置时系统默认使用确定性 Mock：不联网、不需要 API Key，相同输入得到稳定结果，适合开箱运行、测试和演示。也可切换到内置云中转、OpenAI 兼容接口、Google Gemini 或本机 Ollama。首次启动会在空数据库中创建完全虚构的演示账号与课程，随机密码写入本机 `data/demo_credentials.txt`，不会提交到 Git。

学生端所有智能能力统一经过 `student_assistant` 编排轻量化 Skill：课程检索与课后答疑、课堂互动练习、作答评价、错题与薄弱点研判。共享课程中的学习反馈只以匿名聚合形式交给 `teacher_assistant`，用于资料覆盖分析和课程内容迭代建议；个人课程数据不会进入教师端。

下载后可先执行以下命令验证当前 AI 模式：

```powershell
.\start.ps1 ai-check
```

Mock 模式会显示 `offline://deterministic-mock` 和 `SUCCESS`；其他模式会检查 DNS、TCP 和模型响应。部署密钥保存在未提交的环境文件或环境变量中；网页 AI 偏好按账号保存，Key 加密后写入数据库，不向浏览器返回。学生设置只影响自己的 Agent 调用；教师知识中心仍有独立的解析配置。更换接口地址需要重新填写对应 Key，不能把原接口密钥带到新地址。

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

教师共享课程的 PDF 由自适应管线分批调用 MinerU：普通页使用 `auto`，扫描/复杂公式页升级到 `ocr`；TXT、Markdown、DOCX、PPTX 有有效文字时仍只走原生解析。正式部署推荐带 Bearer Token 和 TLS 的校内/远程解析服务；本地 Docker 与 SSH 隧道仅作为可选开发方式。PPTX 在浏览器端渲染，DOCX 由服务端通过 `python-docx` 生成经过转义的审阅 HTML，二者都不要求安装 LibreOffice，并始终保留原文件下载。

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

个人课程不会进入教师统计；教师端的班级分析只读取共享课程数据且不返回学生 ID。Vue、FastAPI 和统一 Agent 接口均复用 `CampusService`，不能绕过这些规则。

## 开发与安全规则

- 新功能必须属于学生学习辅助或教师教学辅助范围，不得新增行政功能；
- `personal_course` 与 `shared_course` 的资料、索引、检索和统计链路必须隔离；
- 学生不能修改共享课程资料，教师不能访问个人课程或私有资料；
- 上传必须检查扩展名、MIME、大小、空文件、文件名安全、路径穿越和重复哈希；
- OpenClaw/Agent 只能使用受控文件内容、引用或文件 ID，不能访问任意本地文件；
- 未实现能力必须返回标准 `not_implemented`，不得用占位数据伪造分析；
- FastAPI、Vue、CLI、Worker 和 OpenClaw 适配器必须复用服务层权限规则。

完整的项目边界、Agent 规则、目录职责、安全要求和验收标准以本目录的 [SYSTEM_USER_GUIDE.md](SYSTEM_USER_GUIDE.md)、[SECURITY.md](SECURITY.md) 和 `tests/` 为准。

## GitHub 下载者共享云端 AI

项目支持确定性 Mock、内置云中转、OpenAI 兼容接口、Google Gemini 和 Ollama。
无配置时固定使用 Mock。部署环境文件、账号配置数据库和密钥文件都不进入 GitHub。
网页选择存入 `account_ai_settings`，Key 由服务端加密保存；旧 `user_ai.env` 仅保留为部署兼容默认，
网页不再修改它，也不把其 Key 自动复制给某个账号。Ollama 可使用 `http://127.0.0.1:11434/v1` 且无需 API Key。

完整部署步骤见 [CLOUD_RELAY_DEPLOYMENT.md](CLOUD_RELAY_DEPLOYMENT.md)。
