# MinerU 与 Pix2Text GPU 服务器部署教程

适用当前智教伴学项目。以下是推荐租用规格，未读取你的云账号，不能作为已购买实例的规格证明。

## 1. 租什么服务器

建议先按小时试租一台 Linux x86_64 GPU 云服务器：

| 项目 | 建议 |
| --- | --- |
| GPU | 单张 NVIDIA A10 24GB；也可选 RTX 3090/4090 24GB |
| vCPU | 8 核起 |
| 内存 | 32GB 起，64GB 更宽裕 |
| 磁盘 | SSD 总容量 200GB 起；Docker 数据目录所在磁盘也要有空间 |
| 系统 | Ubuntu 22.04/24.04 x86_64 |
| 软件前提 | NVIDIA 驱动、Docker Engine、Compose 插件、NVIDIA Container Toolkit |
| 网络 | 有 SSH 入口，能下载容器镜像、Python 包和模型 |
| 初始并发 | 一次一个重型文档任务 |

这是两个解析服务共用一张卡的工程规划建议，并非性能保证。先用真实扫描件和公式页压测，再决定是否升级。CPU 模式也能运行部分识别功能，因此租 GPU 并不是唯一选择；GPU 主要用于加速。

你现有的阿里云函数继续负责大模型中转。这里新增的 GPU 服务器负责 PDF/OCR/公式识别，不需要把百炼 Key 放到 GPU 服务器。

如果使用按小时容器算力平台，先确认是否允许运行 Docker 和使用 GPU 容器。只提供普通 Python 容器、无法运行 Docker 的套餐不适用下面的部署步骤。

## 2. 购买后先验收基础环境

SSH 登录 GPU 服务器，执行：

```bash
nvidia-smi
docker --version
docker compose version
docker run --rm --gpus all nvidia/cuda:12.6.3-base-ubuntu24.04 nvidia-smi
df -h
```

必须在宿主机和容器里都能看到 GPU。驱动须兼容 CUDA 12.6。若 Docker GPU 测试失败，先按 NVIDIA Container Toolkit 官方安装说明配置环境，不能只安装普通 Docker 就继续。

规格确认命令：

```bash
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv
lscpu
free -h
```

把以上输出与租用页面的 vCPU、内存、显存和磁盘逐项核对。下面示例中的 HOST、USER、SSH_PORT 均替换为平台提供的真实值。

## 3. 上传最小部署文件

服务器执行：

```bash
mkdir -p ~/zhijiao-parser/workers
```

Windows 在项目根目录打开 PowerShell，执行（替换 SSH 参数）：

```powershell
scp -P SSH_PORT app/docker-compose.ingestion.yml USER@HOST:~/zhijiao-parser/
scp -P SSH_PORT -r app/workers/formula_worker USER@HOST:~/zhijiao-parser/workers/
```

不需要上传学习数据库、学生资料、真实 Relay 配置或本机虚拟环境。

## 4. 构建固定版本 MinerU 镜像

服务器进入目录：

```bash
cd ~/zhijiao-parser
nano Dockerfile.mineru
```

写入并保存：

```dockerfile
FROM nvidia/cuda:12.6.3-cudnn-runtime-ubuntu24.04
ENV DEBIAN_FRONTEND=noninteractive PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip libgl1 libglib2.0-0 curl && rm -rf /var/lib/apt/lists/*
RUN python3 -m pip install --break-system-packages torch==2.7.1 torchvision==0.22.1 --index-url https://download.pytorch.org/whl/cu126
RUN python3 -m pip install --break-system-packages "mineru[pipeline]==3.4.5" fastapi uvicorn python-multipart
EXPOSE 8000
ENTRYPOINT ["mineru-api"]
CMD ["--host", "0.0.0.0", "--port", "8000"]
```

执行：

```bash
docker build -t zhijiao-mineru:3.4.5 -f Dockerfile.mineru .
```

已检查官方 PyPI 3.4.5 安装包，包含本项目调用的 `/file_parse`、`/health`、`return_middle_json` 和 `response_format_zip`。这只是接口匹配检查，并未在你的 GPU 服务器完成构建及真实文件验收。MinerU 4.x 改用了新 API，不能将此版本号直接替换为最新版。

## 5. 配置公式令牌和模型缓存

生成访问令牌：

```bash
openssl rand -hex 32
nano parser.env
```

parser.env 内容如下，把生成结果填在等号后：

```env
MINERU_IMAGE=zhijiao-mineru:3.4.5
ZHIJIAO_FORMULA_TOKEN=填入刚生成的公式服务令牌
```

然后执行 `chmod 600 parser.env`。妥善私下保管这个文件。

新建 `parser.override.yml`：

```yaml
services:
  mineru-api:
    volumes:
      - mineru-model-cache:/root/.cache
  formula-worker:
    environment:
      ZHIJIAO_FORMULA_TOKEN: ${ZHIJIAO_FORMULA_TOKEN:?请在parser.env填写公式服务令牌}
    volumes:
      - formula-model-cache:/root/.cache
      - formula-model-data:/root/.pix2text
volumes:
  mineru-model-cache:
  formula-model-cache:
  formula-model-data:
```

原 Compose 未向公式服务注入令牌；缺少此设置时，健康检查可能正常，但 `/v1/formula` 会返回 503。

## 6. 启动两个服务

```bash
docker compose --env-file parser.env -f docker-compose.ingestion.yml -f parser.override.yml --profile formula-review up -d --build
docker compose --env-file parser.env -f docker-compose.ingestion.yml -f parser.override.yml --profile formula-review ps
docker logs --tail 100 zhijiao-mineru
docker logs --tail 100 zhijiao-formula-worker
curl -f http://127.0.0.1:18000/health
curl -f http://127.0.0.1:18100/health
```

首次解析会下载模型，可能需要较长时间。Pix2Text 返回 `loaded:false` 代表模型尚未加载，不能据此认定失败，也不能据此认定已完成 GPU 推理验收。

## 7. 从你的电脑连接：先用 SSH 隧道验收

服务器现有 Compose 只监听回环端口；保留这个设置。先在 Windows PowerShell 打开隧道：

```powershell
ssh -p SSH_PORT -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -L 127.0.0.1:18000:127.0.0.1:18000 -L 127.0.0.1:18100:127.0.0.1:18100 USER@HOST
```

保留窗口，在另一个 PowerShell 窗口检查：

```powershell
curl.exe --noproxy "*" http://127.0.0.1:18000/health
curl.exe --noproxy "*" http://127.0.0.1:18100/health
```

编辑本地 `app/server.env`，仅增加或更新以下解析配置；保留已有其他配置：

```env
ZHIJIAO_MINERU_URL=http://127.0.0.1:18000
ZHIJIAO_MINERU_BACKEND=pipeline
ZHIJIAO_MINERU_LANG=ch
ZHIJIAO_MINERU_TIMEOUT=3600
ZHIJIAO_FORMULA_URL=http://127.0.0.1:18100
ZHIJIAO_FORMULA_TOKEN=与服务器parser.env中完全相同的公式服务令牌
ZHIJIAO_FORMULA_TIMEOUT=600
```

不要原样复制整个 server.env.example；其中 `ZHIJIAO_AI_MODE=mock` 会覆盖当前 Relay 默认模式。不要把服务器地址填成你本机不存在的服务端口；此处 localhost 只有在 SSH 隧道保持连接时才会转发到服务器。

完全关闭并重新启动智教伴学，检查知识中心两个解析服务的状态。

## 8. 必须用真实文件验收

1. 上传一份小型文字 PDF，再上传一份扫描 PDF，确认页码、文字、图像和公式有正确来源。
2. 等待文档解析完成，再确认内容分析继续通过现有云函数调用模型。
3. 直接测试公式接口：在服务器准备 formula.png，执行下面命令，手动替换令牌：

```bash
curl -f -H 'Authorization: Bearer 你的公式服务令牌' -F 'file=@formula.png' http://127.0.0.1:18100/v1/formula
```

4. 在识别过程中观察 `nvidia-smi`、两个容器日志及输出质量，确认实际使用 GPU。
5. 重启容器后重复检查，并核对磁盘增长和模型缓存。

健康接口返回 200 只说明服务可响应，不代表模型下载、PDF 返回格式和公式识别全部通过。

## 9. 多电脑长期使用

SSH 隧道适合第一阶段验收，每台电脑都要保持隧道连接。若希望新电脑打开软件就能解析，应在 GPU 服务器配置固定 HTTPS 域名、证书和带 Bearer Token 鉴权的反向代理，再把本地配置改成 HTTPS 地址。MinerU 当前入口不能直接作为未鉴权公网服务开放。

建议最终分别使用 `mineru.你的域名` 和 `formula.你的域名`；代理需允许大文件上传和长请求，并验证超时、鉴权和临时文件清理。GPU 服务器只要关机或欠费，新的远程解析就不可用；已有课程数据仍保存在本地。

## 官方参考

- MinerU 固定版本：https://pypi.org/project/mineru/3.4.5/
- MinerU 新旧接口说明：https://github.com/opendatalab/MinerU/blob/master/docs/en/faq/index.md
- Pix2Text：https://github.com/breezedeus/Pix2Text
- NVIDIA Container Toolkit：https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html

文件生成时未登录你的云平台、未购买实例、未在远程 GPU 上执行部署。推荐规格须经实际教材压测后确认。
