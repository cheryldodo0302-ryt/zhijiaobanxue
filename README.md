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

系统后端固定接入北京地域业务空间 `ws-c4qflt1k6x8xwd4f` 的阿里云百炼 OpenAI 兼容接口，默认模型为 `qwen-plus`。学生和教师端不会出现模型配置输入框。学生演示账号是 `demo_student_001`，教师演示账号是 `demo_teacher_001`；首次启动会创建公开虚拟课程“人工智能基础（虚拟课程）”并导入两份演示资料。

学生端所有智能能力统一经过 `student_assistant` 编排轻量化 Skill：课程检索与课后答疑、课堂互动练习、作答评价、错题与薄弱点研判。共享课程中的学习反馈只以匿名聚合形式交给 `teacher_assistant`，用于资料覆盖分析和课程内容迭代建议；个人课程数据不会进入教师端。

首次部署时，由管理员在服务器执行一次 `./configure_qwen.ps1`，安全输入百炼 `DASHSCOPE_API_KEY`。脚本会把密钥、业务空间地址和模型写入仅后端读取、受当前 Windows 用户权限保护的 `server.env`，随后自动执行真实接口连通性检查。该文件不会发送到浏览器、写入 SQLite 或提交到代码仓库。系统不会用本地模板伪装大模型回答；密钥缺失或真实接口调用失败时会明确报错。也可随时执行 `./start.ps1 ai-check` 单独检查模型连接。

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
