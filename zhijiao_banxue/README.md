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

## 教师知识中心：审查、入库与学生发布

上传文件并等待解析、语义分析完成后，按以下顺序操作：

1. **审查**：在资料行进入原文与知识点对照区，核对分类、来源和正文，使用“保存审查修订”或“驳回”。保存内容修订后需再次批准。
2. **批准到知识库**：逐个知识点、勾选目录分支，或对已核对的整份资料批准。批量资料按钮仅负责入库；部分失败会逐份报告并支持继续。此时只更新教师知识库，学生版本不变。
3. **发布给学生**：在“课程知识发布”区统一发布本课程全部符合条件的已入库知识。后端校验发布条件、保存版本快照，并继续执行课程授权和教学层级隔离。资料行不再提供课程发布按钮。

“知识库状态”反映当前资料的批准情况；“学生版本”标出哪个已发布版本包含该资料，不能把它理解为最新修订已经发布。撤回学生知识版本后，教师仍可维护知识库并重新发布。

知识图谱从教师知识库同步已入库知识到图谱草稿，因此不必先发布课程知识。图谱内容与关系可以继续审查；点击“发布图谱给学生”生成独立学生图谱版本。来源被修改、撤销批准或已失效时，应先重新批准并同步，或把对应图谱节点退回审查后再发布。

主要接口（均在 `/api/v1` 下）：

- `GET /teacher/courses/{course_id}/knowledge-workflow`：统一返回发布条件、每份资料的入库状态和实际学生版本。
- `POST /teacher/documents/{document_id}/approve-to-library`：整份资料批准到知识库；旧 `/knowledge-review` 保留兼容，但不发布。
- `POST /teacher/courses/{course_id}/knowledge-library/approve`：批量入库，传 `{"ids": ["document_id"]}`；先校验全部资料所属课程，再逐份执行并返回 `approved`、`failed`。
- `POST /teacher/courses/{course_id}/knowledge-versions/publish`：发布学生知识版本，支持 `request_id` 幂等重试。

没有新增 Agent Action；现有教师能力开关和服务层权限规则继续生效。

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


### 2026-09 图片问题修复补充

- 学生材料预览：教师发布材料并开启“学生查看原文件”后，已授权学生可在课程材料中预览；引导回答的来源支持文档及页码跳转，无页码时显示章节提示。
- 学号新录入规则为 6–20 位数字，保留前导零；同学号不同姓名作为冲突处理，不覆盖原身份。
- 教师周课表地点必填。`PUT /api/v1/teacher/classes/{class_id}/weekly-schedules` 新增可选 `adjustments` 数组，每项为 `original_date`、可空的 `makeup_date`、`reason`。省略数组保留现有设置，空数组清除；补课日期为空代表停课。服务层校验教学班归属及学期范围，与课表在同一事务中保存。
- 课程日历返回 `adjustments` 和 `cancelled_events`；补课事件保留教学周次，并携带 `original_date` 与 `adjustment_reason`。规则只应用一次，不链式移课。按学校校历配置，不自动推断法定假期和学校补课。
- 新增迁移 `037_class_calendar_adjustments`，由数据库初始化正常应用。教师 Agent 默认开关保持原状。


## 2026-09-19 剩余问题复验

问答在相似度之外增加原文覆盖检查，阻断“课程术语＋无关事实”和随机输入；直接问答及引导问答共用门禁。门禁保守处理未覆盖的同义表达，提示补充资料，不宣称解决了任意模型的全部幻觉。规则位于 `skills/qa/grounding.py`，反例测试位于 `tests/test_grounding_gate.py`。

学生问答/练习总次数来自统一学习事件汇总，不再用最近 20 条记录数充当总数；无成绩与实际 0 分分开显示。资料数量只统计当前学生可见文件，教师班级成员人次明确跨班重复计数。

### Windows 干净 Python 环境复现

已在独立 Python 3.12.4 虚拟环境安装并运行全部后端测试和端到端流程。锁定的环境见 `requirements-lock.txt`；不依赖全局 httpx。下面从项目 `zhijiao_banxue` 目录执行，验证目录使用临时位置，不覆盖运行数据：

```powershell
python -m venv .venv-verify
.\.venv-verify\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv-verify\Scripts\python.exe -m pytest tests -q
cd web
npm ci
npm run test
npm run build
cd ..
$env:ZHIJIAO_DATA_DIR = Join-Path $env:TEMP ('zhijiao-e2e-' + [guid]::NewGuid())
.\.venv-verify\Scripts\python.exe scripts/e2e_smoke.py
```

E2E 使用独立本地端口 18000/15173、虚构账号及离线测试模型，验证服务启动、来源、拒答、练习、权限和导出；不代表已验证外部模型服务或部署到生产站点。教师能力原有开关不变。学生画像见下节。

## 教师端学生画像与班级任务

教师导航“学生画像与任务”（`/student-portraits`）支持按课程、班级、学生及日期查看多维画像。默认最近 30 天，本学期使用班级所属学期的起止日期，未设置日期时需选择自定义范围。学生从“班级作业 / 考试”（`/student/tasks`）进入正式任务。

教师从已发布题库选择单选、多选、判断题并设置分值、截止时间；发布时冻结试题、答案、分值和班级有效学生名单，不随题库或名单后续修改。每题默认 1 分，支持 0.01–1000 分，整份任务最多 100 题。后加入的学生不自动进入旧任务。

- 首次全部题目有有效答案的正式提交确定完成先后；同一 UTC 秒提交并列。名次 = 更早完整提交的人数 + 1，前百分比 = 名次 / 发布时应完成人数。例如 40 人首位为前 2.5%。截止前为动态名次，截止后固定，迟交不参与排名。
- 作业可多次提交，采用截止前最后一次正式提交的百分制成绩；截止时刻本身仍可提交。首次完整提交时间不会被订正覆盖。作业补交成绩单列；考试只允许一次正式提交，截止后关闭。同一请求标识和相同答案重试返回原结果。
- 成绩按任务截止日期归入所选时段；到期完整完成率包含完整补交，按时完整提交率不含补交。答题完整率按所列任务最近一次提交累计。未提交与真实零分分开。知识点正确率只计算实际回答的题目。
- 作业、考试独立计算百分制均分。与前一等长时段各至少 3 次已评分任务才计算分数变化，不校正试题难度，不合成为总分。
- 自习默认为私人。学生可在开始前关联课程和班级并主动授权；只共享新关联记录的汇总。相邻有效信号间隔不超过 3 秒才计入有效采样；无摄像头、校准、断连和过期浏览器帧不计入专注分母。有效采样累计至少 60 秒才生成按有效时长加权的专注参考，附采样覆盖率，不推断态度、人格或心理状态。
- 撤销授权、删除自习记录或任务提交变化会使旧 AI 评价失效；源数据指纹再次校验保证读取时不返回失效内容。重新授权不能恢复旧授权记录。AI 只读取去标识化计算证据，逐条引用证据编号，校验结构、引用和数值；仍为供教师复核的草案，不宣称自动验证全部自然语言结论。

接口统一以 `/api/v1` 开头，所有范围校验在服务层：

| 能力 | 接口 |
| --- | --- |
| 教师题库来源 | `GET /teacher/courses/{course_id}/classes/{class_id}/task-sources` |
| 发布、列出任务 | `POST /teacher/courses/{course_id}/classes/{class_id}/tasks`；同路径 `GET` |
| 学生教学班、任务 | `GET /student/task-scopes`；`GET /student/courses/{course_id}/classes/{class_id}/tasks` |
| 正式提交 | `POST /student/tasks/{task_id}/submissions`，传 `request_id` 与按题目 ID 索引的 `responses` |
| 自习授权 | `GET/POST /student/study-room/grants`；`DELETE /student/study-room/grants/{grant_id}` |
| 关联自习 | `POST /student/study-room/start` 可选传 `course_id`、`class_id`；省略保持私人 |
| 班级画像列表、单人画像 | `GET /teacher/courses/{course_id}/classes/{class_id}/portraits[/{student_id}]` |
| AI 评价 | `POST /teacher/courses/{course_id}/classes/{class_id}/portraits/{student_id}/evaluate` |

画像列表与详情必须提供带时区的 `start_at`、`end_at`，范围为左闭右开；评价接口通过 JSON 传同名字段。统一 Agent 增加 `student_portrait`、`student_portrait_evaluate`，`scope` 包含课程及班级，`input` 包含学生及时间范围。教师 API 和 Agent 继续遵守原有开关；默认不因此开放教师端。

数据库初始化增量应用 `038_student_portraits`；自习库通过幂等建表增加授权记录，旧自习记录不回填授权。升级前按现有部署流程备份主库和 `study_room.db`，后端与前端同步更新。新增回归：`tests/test_student_portraits.py`、`web/src/portrait-utils.test.ts`，正式验收还需运行既有后端/前端测试及构建。真实外部模型与生产发布需要部署环境另行验证。

独立画像端到端验收：`.\.venv\Scripts\python.exe scripts/e2e_student_portraits.py`。它创建临时库、虚构账号并启动独立端口，真实执行题库审核发布、正式任务提交、成绩画像和越权检查，结束后停止进程；不会修改正在运行的课程库。离线模式明确验证 AI 失败响应，不将其当成外部模型成功验收。

可选 Chrome 页面验收（临时安装 Playwright，不修改项目依赖）：

```powershell
npm install --prefix "$env:TEMP/zhijiao-portrait-browser-tools" --cache "$env:TEMP/zhijiao-portrait-npm-cache" --no-save --package-lock=false playwright
.\.venv\Scripts\python.exe scripts/e2e_student_portraits.py --browser-tools "$env:TEMP/zhijiao-portrait-browser-tools"
```

需要本机已安装 Chrome；会验证教师发布、学生作答、画像展示、自习授权、结束保存和撤权。输出目录含桌面/窄屏截图和运行日志，所有账号与任务均为验收时生成的虚构数据。
