# 智教伴学

一个可本地运行和部署的学生智能学习 MVP。当前阶段教师端已禁用且不在主页面显示，优先稳定学生端“材料导入—智能分块—记忆训练—AI 检测—个性化答疑”闭环。所有访问、写入和删除权限都在服务层校验。

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

教师端当前在页面和统一 Agent 层均处于 `disabled`，保留后端代码但不继续修改、不在主页面展示。待学生端最小闭环稳定验收后再开放。

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
