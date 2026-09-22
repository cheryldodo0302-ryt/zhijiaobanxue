# 智教伴学云端中转

该服务将 GitHub 下载的学生端与阿里云千问隔离。真实
`DASHSCOPE_API_KEY` 只放在云平台环境变量中，不得写入客户端文件或 Git。

必需环境变量见 `.env.example`。服务暴露：

- `GET /health`
- `POST /compatible-mode/v1/chat/completions`

Docker 本地启动：

```powershell
docker build -t zhijiao-relay .
docker run --rm -p 8080:8080 --env-file .env zhijiao-relay
```

部署后，客户端 Base URL 是：

```text
https://你的中转域名/compatible-mode/v1
```

生产环境应配置频率限制、费用告警、日志脱敏并定期轮换客户端令牌。
