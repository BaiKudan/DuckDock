# ADR-0205: Langfuse 是可替换 Telemetry Provider

- Status: Accepted
- Date: 2026-07-17

## Context

DuckDock 已经部署 Langfuse v3，继续使用它可以最快补齐 Trace、Session、Dataset 和 Experiment。但将治理逻辑绑定到 Langfuse 内部模型会产生锁定。

## Decision

- Langfuse 是 DuckDock 2.0 的默认 Telemetry Provider。
- 核心领域只依赖 TelemetrySinkPort。
- Provider 负责 Trace 深链、摘要同步和可选 Score 镜像。
- Namespace、资产、责任、Gate 和审批永远由 DuckDock 管理。
- 不并行长期维护 Langfuse、Opik、Phoenix 多套数据面。

## Consequences

- 能复用现有 observability profile。
- 需要实现 Provider Contract Test。
- Langfuse 专有能力不能未经抽象进入核心 API。

## Validation

- Fake Provider 可运行全部核心领域测试。
- 禁用 Langfuse 后 DuckDock 核心启动和治理流程不失败。
- Provider API 变更由兼容测试在 Merge/Nightly 阶段发现。
