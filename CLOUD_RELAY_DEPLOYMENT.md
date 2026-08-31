# 云端中转部署与 GitHub 下载即用

## 设计结果

GitHub 项目中不保存真实千问 `DASHSCOPE_API_KEY`。下载者默认调用你部署的
中转服务；中转服务在服务器端加入真实 Key，再请求阿里云百炼。

```text
下载者的 Vue 统一前端
  -> HTTPS 中转地址 + 客户端令牌
  -> 云端 relay
  -> 服务器环境变量 DASHSCOPE_API_KEY
  -> 阿里云千问
```

账户菜单中的“AI 服务设置”也允许下载者选择自己的 OpenAI 兼容 Base URL、API Key
和模型，也支持 Google Gemini 原生接口。该配置只写入本机 `user_ai.env`，已被
`.gitignore` 排除。

## 1. 生成中转客户端令牌

在 PowerShell 执行：

```powershell
$token = ([guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N"))
$token
```

它不是千问 Key，但公开客户端最终可以读取它，因此必须配合中转限流、费用告警
和定期轮换。

## 2. 部署 `relay` 目录

在项目根目录执行以下命令生成函数计算上传包：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build_relay_zip.ps1
```

脚本会生成适用于函数计算 Linux x64、Python 3.10 的
`relay-deploy-linux-x64.zip`，并自动确认 `app.py` 位于 ZIP 根目录且没有混入
Windows `.pyd` 文件，同时检查 Python 3.10 所需的 `exceptiongroup`。创建函数时
运行环境的 Python 版本必须选择 **Python 3.10**。
请勿再使用
Windows PowerShell 的 `Compress-Archive` 压缩依赖目录，因为它可能对映射中的
`.pyd` 或 `.dll` 文件报错。

任意支持 Docker 的 HTTPS 云平台都可使用。若使用阿里云，建议选择函数计算
Function Compute 的自定义容器，并把监听端口设置为平台要求的端口；镜像已兼容
`FC_CUSTOM_LISTEN_PORT`。构建目录必须设置为 `relay`，并配置：

```text
DASHSCOPE_API_KEY=你的真实千问Key
ZHIJIAO_RELAY_CLIENT_TOKEN=上一步生成的令牌
ZHIJIAO_QWEN_BASE_URL=https://ws-c4qflt1k6x8xwd4f.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
ZHIJIAO_QWEN_TEXT_MODEL=qwen-plus
ZHIJIAO_QWEN_OCR_MODEL=qwen-vl-ocr
ZHIJIAO_RELAY_RATE_LIMIT=30
```

云平台必须只通过环境变量或 Secret 管理器注入这些值，不能把 `.env` 提交到 Git。

阿里云函数计算使用自定义容器时，镜像需要先推送到同账号、同地域的阿里云容器
镜像服务 ACR，然后为函数创建 HTTP 触发器/公网函数 URL。建议选择北京地域，
与当前百炼业务空间保持一致。

部署成功后访问：

```text
https://你的中转域名/health
```

应返回 `{"status":"ok"}`。

### 使用 `cheryldodo0302.xyz` 自定义域名

自定义域名只改变访问入口，不会恢复函数计算免费额度。额度耗尽后，必须为原函数
开通按量付费/资源包，或把 relay 迁移到仍有计算资源的服务器。

建议使用子域名 `api.cheryldodo0302.xyz`：

1. 在函数计算控制台打开对应函数，进入“自定义域名”，复制控制台给出的公网
   CNAME 目标（通常形如 `<account-id>.cn-beijing.fc.aliyuncs.com`）。
2. 若继续使用阿里云 DNS，在云解析 DNS 添加 `api` 的 CNAME，记录值填写上一步的
   目标。若使用 Cloudflare，则先把全部 DNS 记录迁入 Cloudflare，再到阿里云域名
   控制台把 NS 改为 Cloudflare 分配的两台服务器；已启用 DNSSEC 时先关闭旧
   DNSSEC/删除旧 DS。
3. Cloudflare 中添加 `CNAME api -> FC 公网 CNAME`，首次必须使用灰云
   **DNS only**。等 FC 完成域名校验后再考虑切为橙云。
4. 回到 FC 创建 `api.cheryldodo0302.xyz` 自定义域名，将路径 `/*` 路由到 relay
   函数和当前版本，并配置与该子域名匹配的 HTTPS 证书。
5. 验证 `https://api.cheryldodo0302.xyz/health` 返回 `{"status":"ok"}`。如开启
   Cloudflare 橙云，将 SSL/TLS 模式设置为 **Full (strict)**。
6. 最后把 `relay_client.env` 中的地址改成：

```text
ZHIJIAO_RELAY_URL=https://api.cheryldodo0302.xyz/compatible-mode/v1
```

中国内地地域的 FC 自定义域名需要完成 ICP 备案；Cloudflare 免费版不提供中国大陆
网络节点。主要用户在大陆时，保持阿里云 DNS 直连或 Cloudflare 灰云通常更简单。

## 3. 把中转连接写入 GitHub 客户端

复制 `relay_client.env.example` 为 `relay_client.env`：

```text
ZHIJIAO_AI_MODE=relay
ZHIJIAO_RELAY_URL=https://你的中转域名/compatible-mode/v1
ZHIJIAO_RELAY_TOKEN=上一步生成的令牌
```

`relay_client.env` 可以提交到 GitHub，因为其中没有真实千问 Key。提交后，其他人
下载 GitHub ZIP、安装依赖并执行 `.\start.cmd`，再访问 `http://127.0.0.1:5173`，即可默认使用你的云端服务。

## 4. 上线前保护

- 云网关和应用同时限流；
- 设置阿里云模型调用费用告警和月度预算；
- 中转服务只允许服务端指定的文本与 OCR 模型；
- 日志不得记录 Authorization 请求头或完整学习材料；
- 泄漏或滥用时轮换客户端令牌，并重新发布 `relay_client.env`；
- 正式公开前增加用户登录、个人额度和服务条款。

## 5. 用户自定义 API 示例

在学生端侧栏“AI 服务设置”选择“使用我自己的接口”。

OpenAI 兼容接口：

```text
接口协议：OpenAI 兼容接口
Base URL：https://你的服务地址/v1
模型名称：服务商提供的模型 ID
```

Gemini 原生接口：

```text
接口协议：Google Gemini 原生接口
Base URL：https://generativelanguage.googleapis.com/v1beta
模型名称：gemini-2.5-flash（以账号实际可用模型为准）
```

也可以把完整的 `.../models/<模型>:generateContent` 地址粘贴到 Base URL。程序还
支持 Gemini 的 OpenAI 兼容地址 `https://generativelanguage.googleapis.com/v1beta/openai`。

若正确地址仍报 `ConnectTimeout`，表示请求尚未到达 Gemini，通常是当前网络无法
连接 Google API。应用会读取系统的 `HTTPS_PROXY`；也可以改用当前网络可访问的
OpenAI 兼容服务或云端中转。仅修改 URL 无法解决网络层不可达。
