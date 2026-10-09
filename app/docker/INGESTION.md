# 远程优先的知识库解析 Worker

重型解析与主应用分离。教师共享课程的 PDF 统一由 MinerU 生成规范 Markdown；有可读文字的 Office/TXT/Markdown 直接原生解析。Pix2Text 只复核 MinerU 识别出的公式区域。

## 1. 推荐：配置远程 HTTPS 服务

将 `server.env.example` 复制为未提交的 `server.env`，填写 MinerU/Pix2Text URL、令牌和 TLS 配置。启动器不会再自动注入本机端口。教师端“知识中心”会显示两项服务的健康状态。

## 2. 可选：准备本机 MinerU 镜像

项目 Compose 使用本机的 `mineru:latest`。按 MinerU 官方 Docker 文档构建镜像，并在打包或分发前复核代码与模型权重许可证：

国内网络使用官方 China Dockerfile：

```powershell
curl.exe -L "https://gcore.jsdelivr.net/gh/opendatalab/MinerU@master/docker/china/Dockerfile" -o "docker/mineru.Dockerfile"
docker build -t mineru:latest -f docker/mineru.Dockerfile .
```

国际网络可将地址中的 `china` 换成 `global`。官方当前镜像默认使用 CUDA 13.0 基础镜像；若驱动只兼容 CUDA 12.9，请按 Dockerfile 顶部注释切换 `cu129` 基础镜像。

## 3. 启动与检查

```powershell
docker compose -f docker-compose.ingestion.yml up -d mineru-api
docker compose -f docker-compose.ingestion.yml --profile formula-review up -d
curl.exe http://127.0.0.1:18000/health
curl.exe http://127.0.0.1:18100/health
```

首次启动会下载模型，耗时取决于网络。RTX 4060 Laptop 8GB 默认采用 `pipeline`，同时只处理一个重型任务。

## 4. 本机开发配置

在启动 FastAPI 的同一终端设置：

```powershell
$env:ZHIJIAO_MINERU_URL = "http://127.0.0.1:18000"
$env:ZHIJIAO_MINERU_BACKEND = "pipeline"
$env:ZHIJIAO_MINERU_LANG = "ch"
$env:ZHIJIAO_FORMULA_URL = "http://127.0.0.1:18100"
./start.ps1 api
```

另开一个终端启动持久化任务 Worker：

```powershell
./start.ps1 worker
```

`start.ps1 worker` 只读取进程环境或 `server.env`，不会默认连接本机端口。Pix2Text 的 `/health` 在首次推理前显示 `loaded:false` 属于正常状态。公式 Worker 固定使用 `onnxruntime-gpu==1.23.2`，与 CUDA 12.6 基础镜像兼容。

不设置 `ZHIJIAO_MINERU_URL` 时，教师 PDF 会明确失败并保留可重试任务；TXT、Markdown、DOCX、PPTX 的有效文字仍可原生入库。远程服务通过真实 PDF 验收前不要卸载本机 Worker。
