# ADR-0204: OTel GenAI 与 OpenInference 兼容层

- Status: Accepted
- Date: 2026-07-17

## Context

OpenTelemetry GenAI 语义仍在演进，OpenInference 对 Agent、LLM、Tool、Retriever、Guardrail 和 Evaluator 提供了更完整的当前分类。把外部属性名直接写死进数据库会造成长期迁移负担。

## Decision

- 接收 OTel GenAI 和 OpenInference 两类属性。
- 在 telemetry domain 内建立版本化 Normalizer。
- 每个 AgentRun 记录 source_schema、source_schema_version 和 normalizer_version。
- 原始属性留在遥测后端或 Artifact，不展开为长期业务字段。
- 内部只保留稳定摘要字段和组件使用关系。

## Consequences

- 可以接入不同 Harness 和 SDK。
- 需要维护 Golden Contract Fixture 和兼容矩阵。
- 新规范字段不会自动成为治理事实。

## Validation

- 同一标准 Fixture 经不同源格式归一后得到等价 AgentRun 摘要。
- 未知属性被保留但不影响消费。
- Breaking Schema 版本必须显式拒绝或进入 quarantine。
