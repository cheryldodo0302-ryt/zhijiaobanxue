# 项目内部 Skill

本目录把可复用业务能力拆成小型 Skill。`skill_manifest.yaml` 只用于本项目登记名称、输入输出和权限，**不是官方 OpenClaw 格式**，也不表示已通过任何平台兼容认证。

系统内部采用 Python 接口直接调用 Skill。`integrations/openclaw_adapter/` 已提供本地 MCP stdio 服务，将七项业务能力作为工具开放给 OpenClaw，并复用 `CampusService` 权限校验。MCP 工具定义与本目录内部的 Skill manifest 是两套契约，不能混用。接入方式见 [OpenClaw 接入说明](../integrations/openclaw_adapter/README.md)。

每个 Skill 都应遵守三条规则：先校验登录身份和课程权限；失败时记录内部日志但向用户返回普通语言；不得绕过课程证据和课程级数据隔离。
