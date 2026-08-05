# DuckDock 文档索引

本文档索引用来区分“当前可执行手册”和“历史 PRD / 设计材料”。

## 当前推荐阅读顺序

1. [DuckDock 2.0.0-rc.1 发布就绪评估](./release-2.0-rc1-readiness.zh-CN.md)
   - 给出当前候选版的工程发布结论、实时门禁、验证范围和 GA 前置条件。
   - 明确区分 RC 技术 READY 与目标环境生产授权。

2. [DuckDock 使用手册](./duckdock-handbook.zh-CN.md)
   - 面向管理员、员工和普通使用者。
   - 说明控制台、个人工作台、Reporter 接入、资产确认和交接视图如何使用。

3. [DuckDock 运维手册](./operations-runbook.zh-CN.md)
   - 面向部署和运维人员。
   - 说明单机 Docker Compose、环境变量、组件管理、备份恢复、健康检查和常见故障。

4. [生产部署 Runbook](./production-deployment.md)
   - 面向上线部署人员。
   - 说明 Compose + SOPS/age、首次部署、analysis-worker profile、备份与恢复演练。

5. [Langfuse 升级与回滚 Runbook](./langfuse-upgrade-runbook.zh-CN.md)
   - 记录 DuckDock 接受的 Langfuse/SDK/ClickHouse 精确基线。
   - 覆盖兼容性门禁、隔离备份恢复、v5 新适配器和回滚条件。

6. [API v1 → v2 迁移策略](./api-v1-v2-migration.zh-CN.md)
   - 说明 v1 在 DuckDock 2.x 的兼容承诺、响应头和迁移原则。
   - 关联冻结的 `2.0.0-rc.1` OpenAPI 契约与契约漂移门禁。

7. [Reporter 集成验收指南](./workbuddy-reporter-validation.zh-CN.md)
   - 提供 Hermes、WorkBuddy 与其他 Agent 的可复现验收流程。
   - 覆盖结构化日报/周报、交接包、凭证安全、失败场景和验收记录模板。

8. [DuckDock Runtime MCP 接入说明](./duckdock-runtime-mcp.zh-CN.md)
   - 说明如何把 DuckDock Reporter 接入流程 MCP 化。
   - 覆盖 WorkBuddy 第一版 MCP 工具、首次接入、最小校验上报、定时任务和长期预置计划。

9. [Analysis Worker 生产运行说明](./agent-control-plane-prd/analysis-worker-production-runbook.md)
   - 面向专属 OpenClaw / Analysis Worker 部署。
   - 说明 Worker token、任务租约、并行扩容和结果制品。

10. [专属 OpenClaw Worker 领取与归档流程](./agent-control-plane-prd/openclaw-worker-lease-archive-flow.zh-CN.md)
   - 说明 Worker 如何 lease 任务、下载 report pack、产出结果文件、finalize、失败重试和并行处理。
   - 明确 MinIO 只保存完整内容、MySQL 只保存精简索引的实现边界。

11. [Reporter Skill 设计](./agent-control-plane-prd/12-duckdock-reporter-skill-design.zh-CN.md)
   - 面向 Reporter skill 维护者。
   - 说明采集范围、隐私边界、上报包格式和运行时侧部署原则。

## 工程规格与历史验收材料

- [DuckDock 2 Foundation 规格](../specs/008-duckdock-2-foundation/spec.md)
- [DuckDock 2.0 技术候选规格](../specs/015-ga-candidate/spec.md)
- [历史租户处置与 Contract Gate](../specs/009-foundation-tenant-contract/spec.md)
- `specs/*/evidence/` 中的记录只证明记录日期、记录环境和明确列出的检查，不是当前部署或生产授权的事实源。

## 历史设计材料

`docs/agent-control-plane-prd/` 下的 `01` 到 `13` 号文档是产品和架构演进期的 PRD / 设计稿，适合追溯决策，不一定代表当前 UI 的逐项操作入口。

## 文档维护原则

- 当前可执行流程优先维护在 `duckdock-handbook.zh-CN.md` 和 `operations-runbook.zh-CN.md`。
- 公共文档只记录可复现步骤和不含环境身份信息的能力状态；环境专属验收结果保存在部署方的受控记录系统。
- 不在 README 或手册中硬编码测试通过数、端点数量、开发进度百分比或某台机器的 READY 状态。
- PRD 文档可以保留历史上下文，但不要把临时 token、真实密钥或一次性上传 URL 写入仓库。
