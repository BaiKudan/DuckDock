# ADR-0201: WorkTrace 与 AgentRun 分离

- Status: Accepted
- Date: 2026-07-17

## Context

现有 WorkTrace 表达员工或 Agent 的企业工作摘要，包含标题、摘要、时间、敏感级别和资产关联。执行级轨迹需要 Trace ID、部署版本、Token、成本、Tool 错误和可信等级。把两种语义合并会迫使 MySQL 承担高吞吐 Span，并破坏现有交接语义。

## Decision

- WorkTrace 保持企业工作/任务聚合。
- 新增 AgentSession、AgentRun 和 TraceBackendRef。
- WorkTrace 可以关联零到多个 AgentRun。
- 原始 Span 留在遥测后端，AgentRun 只保存索引、摘要和治理关系。
- 没有真实 Trace ID 或 ATIF 的历史 WorkTrace 不回填为 AgentRun。

## Consequences

- 企业工作视角和执行调试视角可以独立演进。
- 查询完整轨迹需要通过 Telemetry Provider。
- 前端需要提供 WorkTrace 与 AgentRun 双向跳转。

## Validation

- 一个 WorkTrace 可关联多次重试 Run。
- 一次 Run 必须定位到确切 Deployment revision。
- MySQL 中不得出现逐 Span 或逐 Tool I/O 明细表。
