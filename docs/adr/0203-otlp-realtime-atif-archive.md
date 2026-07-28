# ADR-0203: OTLP 用于实时，ATIF 用于归档

- Status: Accepted
- Date: 2026-07-17

## Context

OTLP 适合实时分布式追踪，ATIF 适合表达完整、可移植、可重放的 Agent 轨迹。用单一格式同时承担实时传输和长期档案会降低兼容性。

## Decision

- Runtime 实时遥测通过 OTLP HTTP/gRPC 进入 Collector。
- ATIF 作为可选的完整轨迹制品上传 MinIO。
- Run 控制信封只保存低频生命周期、版本 digest、幂等和 ATIF 引用。
- ATIF 不替代 W3C Trace Context，OTLP JSON 不作为长期业务 Schema。

## Consequences

- 实时观测和离线评测可以独立扩展。
- Reporter/Adapter 需要维护 Trace ID 与 ATIF artifact 的关联。
- ATIF Schema 版本必须固定并经过 Codec Port 验证。

## Validation

- 同一 Run 可以同时解析 OTLP Trace 和 ATIF Artifact。
- 只有 OTLP 时仍可完成运行索引。
- 只有通过来源 attestation 的 ATIF 才能标记 `PRODUCER_ATTESTED + IMPORT`；checksum-only 导入最多是 `CHANNEL_AUTHENTICATED + IMPORT`。
