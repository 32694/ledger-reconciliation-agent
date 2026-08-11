# 对账分析 Web 工作台

项目定位、Docker 启动方式和使用边界见[根 README](../README.md)，操作流程见[用户指南](../docs/USER_GUIDE.md)。

## 目录职责

- `src/app/`：Next.js 页面、布局和 Agent API 代理。
- `src/components/cases/`：演示案件、证据面板和人工确认界面。
- `src/components/thread/`：LangGraph 会话、消息与工具调用展示。
- `src/lib/`、`src/providers/`：对账结果转换、stream 状态和客户端配置。
- `tests/`：Playwright 端到端流程。

## 开发命令

先确保 Agent API 在 `http://localhost:2024` 运行，然后执行：

```bash
cd web
pnpm install --frozen-lockfile
NEXT_PUBLIC_API_URL=http://localhost:2024 \
NEXT_PUBLIC_ASSISTANT_ID=reconciliation_agent \
pnpm dev
```

质量检查：

```bash
pnpm test
pnpm typecheck
pnpm build
pnpm format:check
```

根目录的 `./scripts/verify.sh` 会在固定容器环境中执行完整前后端交付门禁。

## 上游版权

前端由 MIT 许可的 [langchain-ai/agent-chat-ui](https://github.com/langchain-ai/agent-chat-ui) 改造而来。上游许可证保留在 [LICENSE](LICENSE)，固定来源版本见[第三方声明](../THIRD_PARTY_NOTICES.md)。
