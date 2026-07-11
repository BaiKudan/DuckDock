# DuckDock 文档索引

本文档索引用来区分“当前可执行手册”和“历史 PRD / 设计材料”。

## 当前推荐阅读顺序

1. [DuckDock 使用手册](./duckdock-handbook.zh-CN.md)
   - 面向管理员、员工和普通使用者。
   - 说明控制台、个人工作台、Reporter 接入、资产确认和交接视图如何使用。

2. [DuckDock 运维手册](./operations-runbook.zh-CN.md)
   - 面向部署和运维人员。
   - 说明单机 Docker Compose、环境变量、组件管理、备份恢复、健康检查和常见故障。

3. [生产部署 Runbook](./production-deployment.md)
   - 面向上线部署人员。
   - 说明 Compose + SOPS/age、首次部署、analysis-worker profile、备份与恢复演练。

4. [Reporter 集成验收指南](./workbuddy-reporter-validation.zh-CN.md)
   - 提供 Hermes、WorkBuddy 与其他 Agent 的可复现验收流程。
   - 覆盖结构化日报/周报、交接包、凭证安全、失败场景和验收记录模板。

5. [DuckDock Runtime MCP 接入说明](./duckdock-runtime-mcp.zh-CN.md)
   - 说明如何把 DuckDock Reporter 接入流程 MCP 化。
   - 覆盖 WorkBuddy 第一版 MCP 工具、首次接入、dry-run、定时任务和长期预置计划。

6. [Analysis Worker 生产运行说明](./agent-control-plane-prd/analysis-worker-production-runbook.md)
   - 面向专属 OpenClaw / Analysis Worker 部署。
   - 说明 Worker token、任务租约、并行扩容和结果制品。

7. [专属 OpenClaw Worker 领取与归档流程](./agent-control-plane-prd/openclaw-worker-lease-archive-flow.zh-CN.md)
   - 说明 Worker 如何 lease 任务、下载 report pack、产出结果文件、finalize、失败重试和并行处理。
   - 明确 MinIO 只保存完整内容、MySQL 只保存精简索引的实现边界。

8. [Reporter Skill 设计](./agent-control-plane-prd/12-duckdock-reporter-skill-design.zh-CN.md)
   - 面向 Reporter skill 维护者。
   - 说明采集范围、隐私边界、上报包格式和运行时侧部署原则。

## 历史设计材料

`docs/agent-control-plane-prd/` 下的 `01` 到 `13` 号文档是产品和架构演进期的 PRD / 设计稿，适合追溯决策，不一定代表当前 UI 的逐项操作入口。

## 文档维护原则

- 当前可执行流程优先维护在 `duckdock-handbook.zh-CN.md` 和 `operations-runbook.zh-CN.md`。
- 公共文档只记录可复现步骤和不含环境身份信息的能力状态；环境专属验收结果保存在部署方的受控记录系统。
- PRD 文档可以保留历史上下文，但不要把临时 token、真实密钥或一次性上传 URL 写入仓库。
