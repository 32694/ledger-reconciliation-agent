# 智能对账分析 Agent 迁移指南

本指南用于在新电脑迁移本地演示环境，或在升级前建立可回退点。前置条件是新旧电脑均可使用 Git 和 Docker（含 `docker compose`）。

## 迁移对象

| 对象 | 保存位置 | 迁移方式 | 是否进入 Git |
| --- | --- | --- | --- |
| 代码与规则 | Git 仓库 | clone 指定 commit 或 tag | 是 |
| Agent 数据库 | Docker volume `ledger-postgres-data` | `backup.sh` 导出、`restore.sh` 恢复 | 否 |
| embedding 模型 | Docker volume `ledger-huggingface-cache` | 新电脑按需重新下载 | 否 |
| 模型服务密钥 | 本机根 `.env` 的 `MODEL_API_KEY` | 在新电脑手工重新填写 | 否 |

Compose 中显示的是逻辑 volume 名；Docker 实际资源名可能带 `ledger-reconciliation-agent_` project 前缀。不要复制 PostgreSQL volume 目录，也不要把数据库 dump、Hugging Face cache 或 `.env` 提交到 Git。

## 安全迁移流程

### 1. 在旧电脑记录版本并备份

进入旧仓库，确认当前 commit：

```bash
git rev-parse HEAD
git status --short
```

保证 `postgres` 健康后创建自定义格式备份：

```bash
./scripts/start.sh
./scripts/backup.sh backups/pre-migration.dump
```

脚本通过容器内 `pg_dump` 写临时文件，成功后再原子替换目标文件。检查文件存在且非空：

```bash
test -s backups/pre-migration.dump
```

将 commit SHA 和 `pre-migration.dump` 通过可信渠道带到新电脑。dump 可能含业务数据；即使本项目默认只有合成数据，也应按敏感数据库备份管理。不要传输旧 `.env`，模型 Key 应在新电脑重新配置。

### 2. 在新电脑 clone 同一代码版本

```bash
git clone https://github.com/32694/ledger-reconciliation-agent.git
cd ledger-reconciliation-agent
git switch --detach <old-computer-commit-sha>
```

先迁移同一代码版本可以避免数据库 schema 与应用代码同时变化。确认 Docker 和 Git 可用：

```bash
git --version
docker info
docker compose version
```

### 3. 创建本机配置

```bash
cp .env.example .env
```

在新电脑编辑 `.env`，设置新的本地数据库密码，并填写 `MODEL_BASE_URL`、`MODEL_API_KEY`、`MODEL_NAME`。`MODEL_API_KEY` 只保存在本机，不要提交到 Git，也不要放进备份 dump。

### 4. 只启动新数据库并恢复

先启动空的 PostgreSQL：

```bash
docker compose --env-file .env -f compose.yaml up -d postgres
docker compose --env-file .env -f compose.yaml ps postgres
```

状态为 `healthy` 后，把备份文件放在仓库外或被忽略的 `backups/` 中，然后运行：

```bash
./scripts/restore.sh /absolute/path/to/pre-migration.dump
```

交互式恢复要求输入固定文本“确认恢复”，因为 `pg_restore --clean --if-exists` 会覆盖当前 Agent 数据库对象。自动化环境可以显式使用：

```bash
./scripts/restore.sh --yes /absolute/path/to/pre-migration.dump
```

`--yes` 跳过人工确认，风险更高，只应在已经核对目标数据库且保留当前备份时使用。

### 5. 启动完整服务并验收

```bash
./scripts/start.sh
```

打开 `http://localhost:3000`，确认演示案件、规则引用和审计历史可读，再执行一次 `CASE-1001` 分析。首次规则摄取会在 `ledger-huggingface-cache` 中重新下载 Hugging Face 模型；缓存无需从旧电脑迁移，也不要提交到 Git。

## 升级前后的备份与恢复

升级代码前创建带版本含义的备份：

```bash
./scripts/backup.sh backups/before-$(git rev-parse --short HEAD).dump
git pull --ff-only
./scripts/start.sh
```

升级后先执行无 Key 质量门禁，再进行真实模型验收：

```bash
./scripts/verify.sh
```

如需恢复旧数据，先停止会写入数据库的服务、保留 PostgreSQL，再额外备份当前状态：

```bash
./scripts/stop.sh
docker compose --env-file .env -f compose.yaml up -d postgres
./scripts/backup.sh backups/failed-upgrade-state.dump
./scripts/restore.sh backups/before-<commit>.dump
```

恢复完成后再启动与该 schema 兼容的代码版本。不要使用 `docker compose down -v`，它会删除数据和缓存 volume。

## 回滚边界

- **代码回滚不等于数据库回滚**：切换 Git commit 不会反向执行 Alembic migration，也不会恢复案件、确认或审计数据。
- **数据库恢复是覆盖操作**：`restore.sh` 清理并重建当前数据库对象；恢复前必须另存当前状态，并核对目标是本地 Agent 数据库。
- **缓存不属于恢复点**：删除 `ledger-huggingface-cache` 只会触发模型重新下载，不会回滚代码或数据库；没有网络时应保留现有缓存。
- **密钥不属于备份**：`.env` 与 `MODEL_API_KEY` 必须单独在每台电脑配置。旧 Key 的吊销、轮换和权限由模型服务提供方管理。
- **跨 schema 版本需成对回滚**：只有已验证的代码 commit 与其升级前数据库 dump 组合才构成完整回退点。无法确认兼容性时，不要让旧代码连接升级后的 volume。

本流程保护的是单机演示数据，不构成生产灾备方案。
