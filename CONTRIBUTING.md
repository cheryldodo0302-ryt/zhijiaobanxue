# 开发与提交说明

## 开发边界

本项目只服务学生学习辅助和教师教学辅助。个人课程、教师共享课程、文档入库、课程问答、练习、知识中心、知识图谱、教学诊断和报告导出属于范围；不得新增行政办公端、行政 Agent 或行政数据模型。

所有适配器都必须复用服务层的权限校验。`personal_course` 与 `shared_course` 的文件、索引、检索和统计必须隔离：学生不能直接修改共享资料，教师不能读取学生个人课程和私有资料。

## 本地验证

在仓库根目录执行：

```powershell
.\start.ps1 -Mode test
cd web
npm ci
npm run test
npm run build
```

后端新增能力还应补充课程类型、越权访问、文件安全、重复上传、检索隔离和 Agent 响应测试。新增 Agent Action 时同步更新 `agent_service.py`、API/适配器、前端调用、测试和 README；未实现能力必须返回标准 `not_implemented`，关闭的教师能力必须返回 `disabled`。

## 功能约束

- 知识卡片的 AI 语义拆分必须先预览、再由用户确认写入；关键词挖空未填写关键词时，只能在点击“生成挖空”后发送内容给 AI。
- 判断题对用户显示“判断题”和“对/错”，服务层继续兼容历史 `T/F`、`true/false` 等答案值。
- AI 自习室的视频/帧分析在浏览器本地完成，后端只接收脱敏后的状态、专注度和实时分。
- 教学班、实验矩阵、考核方案、知识点和教学档案必须保留正确的教学层级；考核方案合并时保留来源文件，避免不同文件的组成被混淆。

## 不应提交的内容

不要提交 `.env`、`server.env`、`user_ai.env`、`relay_client.env`、数据库、`data/`、演示账号文件、上传资料、真实师生信息、`web/node_modules/`、`web/dist/`、Python 缓存或临时目录。真实密钥只能放在未跟踪的环境文件或部署环境变量中。

提交前可检查：

```powershell
git status --short
git diff --check
git diff --cached --name-only
```
