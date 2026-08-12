# 智能对账分析 Agent 用户指南

本指南用于本地运行合成数据演示。系统不连接真实支付渠道，不应承载真实资金操作。

## 1. 准备配置

确认 Docker daemon 已启动，然后在仓库根目录执行：

```bash
cp .env.example .env
```

在本机编辑 `.env`，填写真实 OpenAI 兼容模型服务提供的三个值：

```dotenv
MODEL_BASE_URL=https://your-openai-compatible-provider.example/v1
MODEL_API_KEY=your-local-api-key
MODEL_NAME=your-model-name
```

- `MODEL_BASE_URL`：兼容 OpenAI API 的基础地址，通常以 `/v1` 结尾。
- `MODEL_API_KEY`：模型服务密钥。
- `MODEL_NAME`：该服务实际开放的模型名称。

`.env` 已被 Git 忽略。密钥只应保存在本机，不要提交、粘贴到 issue、写入截图或发送到聊天中。`BAAI/bge-small-zh-v1.5` embedding 在本地运行，但这不替代 Agent 推理所需的真实模型。

## 2. 启动服务

在仓库根目录运行：

```bash
./scripts/start.sh
```

脚本会构建并启动 PostgreSQL、Agent 和 Web，等待健康检查完成后打印：

```text
Web: http://localhost:3000
Agent API: http://localhost:2024
```

首次运行需要下载容器镜像和 Hugging Face embedding 模型，可能比后续启动慢。浏览器打开 `http://localhost:3000`，顶部状态应显示“服务正常”。

## 3. 分析演示案件

左侧“演示案件”包含 `CASE-1001` 至 `CASE-1004`。选择 `CASE-1001`，输入或直接发送：

```text
分析 CASE-1001 的差异原因
```

旧草案中出现过 `CASE-0001`，当前 seed 数据并不存在该编号；请使用界面列出的 `CASE-1001`。如果手动输入不存在的 `CASE-0001`，系统应返回证据不足，而不是生成案件事实。

分析完成后，中间会话区显示结论和工具过程。打开右侧“证据面板”核对：

- “结构化事实”是否属于当前案件；
- “命中规则”是否与案件锁定的 `rule_version` 一致；
- “证据引用”中的规则版本、章节、chunk 和来源是否完整。

不要只根据自然语言摘要执行处理；证据面板是核对结论可追溯性的依据。

系统还会校验支付状态、渠道交易号、支付金额以及账本借贷平衡。即使模型输出了分析文本，只要结构化证据之间互相矛盾，系统仍会显示“证据不足”，不会生成处理建议或进入人工确认。

## 4. 验证券不足路径

选择 `CASE-1004` 并发送：

```text
分析 CASE-1004 的差异原因
```

该合成案件锁定了不存在的规则版本 `RULE-MISSING-V9`。预期状态是“证据不足”，证据面板提示“未找到案件锁定的规则版本”，且不会引用其他规则版本、不会给出可执行建议。

不存在的案件编号同样应进入证据不足路径。系统不应根据相似编号或其他案件补齐事实。

## 5. 批准或拒绝建议

证据齐全时，Agent 会显示“等待人工确认”并列出案件编号、处理方式和处理说明。

- 先逐项核对案件编号、结构化事实、规则版本和建议内容。
- 选择“确认执行”后，系统才写入案件解决结果和审计事件；同一案件不能重复解决。
- 选择“拒绝执行”后，建议被拒绝，已验证证据仍可查看，案件保持未处理状态。
- 确认窗口有效期为 10 分钟；过期后重新发起分析，不要尝试复用旧确认。

这是演示环境的安全边界，不代表具备生产审批、权限治理或真实资金处理能力。

## 6. 停止服务

在仓库根目录运行：

```bash
./scripts/stop.sh
```

该命令停止 `postgres`、`agent` 和 `web`，不会删除 PostgreSQL 数据 volume 或 Hugging Face 模型缓存。需要迁移或保留数据时，使用迁移指南中的备份流程，不要手工删除 volume。

## 7. 常见错误

### `Docker CLI or daemon is unavailable`

启动 Docker Desktop 或本机 Docker daemon，确认以下命令成功后重试：

```bash
docker info
docker compose version
```

### `Missing root .env`

在仓库根目录重新创建配置：

```bash
cp .env.example .env
```

### `Missing required model setting`

检查 `.env` 中 `MODEL_BASE_URL`、`MODEL_API_KEY`、`MODEL_NAME` 均为非空值。不要把密钥发给他人排查；可在模型服务控制台核对地址、模型名和密钥权限。

### 启动健康检查超时

查看服务状态和日志：

```bash
docker compose --env-file .env -f compose.yaml ps
docker compose --env-file .env -f compose.yaml logs --tail=200 postgres agent web
```

常见原因包括镜像或模型尚未下载、端口 `3000`、`2024`、`5433` 被占用，以及模型服务从容器内不可达。修改配置后重新执行 `./scripts/start.sh`。

### 页面显示“服务离线”

确认 Agent 健康接口可访问，并检查 Agent 日志：

```bash
curl --fail http://localhost:2024/ok
docker compose --env-file .env -f compose.yaml logs --tail=200 agent
```

### 模型返回错误或没有形成结论

确认模型服务兼容 OpenAI Chat Completions、`MODEL_NAME` 存在，并且模型支持当前工具调用格式。证据或规则确实缺失时，“证据不足”是预期结果，不应通过改写提示词绕过。

### 已完成的真实模型验收

本机已使用 `deepseek-chat` 完成真实模型验收：

- 分析 `CASE-1001` 和 `CASE-1002` 成功，模型实际调用了案件、支付、账本和规则工具。
- 页面展示结构化事实、规则引用以及案件锁定的规则版本（例如 `RULE-ALIPAY-V1`）。
- 对解决请求的验证符合安全边界：当模型返回内容未通过严格 JSON/schema 校验时，系统显示“证据不足”，不出现批准或已解决状态，也不执行写操作。

这意味着真实模型的读取和规则引用链路已验证；人工确认写入链路仍应以结构化输出校验通过的模型响应再验收。README 的“界面预览”部分提供了上述过程的截图。

### 需要验证代码但没有模型 Key

运行隔离的 scripted model 质量门禁：

```bash
./scripts/verify.sh
```

此命令不会调用在线模型。它只验证代码、固定评测和构建，不能替代真实模型的交互验收。
