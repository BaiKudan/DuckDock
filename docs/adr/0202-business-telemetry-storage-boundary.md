# ADR-0202: 业务事实与遥测存储边界

- Status: Accepted
- Date: 2026-07-17

## Context

DuckDock 使用 MySQL 作为业务主库，现有 Langfuse v3 使用 PostgreSQL、ClickHouse 和对象存储。执行级轨迹具有高吞吐、高基数和长文本特征，不适合复制到 MySQL。

## Decision

- MySQL 保存资产、部署、Run 索引、Dataset/Eval 元数据、Gate、审批、审计和交接。
- Langfuse/ClickHouse 保存原始 Trace、Span、Token、成本和 Tool/Model I/O。
- MinIO 保存 ATIF、输入快照、详细结果、制品和 Evidence Pack。
- DuckDock 不直接查询 Langfuse 数据库，只使用公开 API/SDK 或 Provider Port。

## Consequences

- 避免 MySQL 被遥测流量压垮。
- 需要跨存储删除、备份和对账流程。
- Langfuse 不可用时治理控制面仍可运行，但完整轨迹暂不可见。

## Validation

- Langfuse 停止时，资产、审批和历史 Gate 仍可查询。
- 任何 MySQL Migration 不创建 Span/Prompt/Tool Result 大表。
- Retention 测试覆盖三个存储面的删除回执。
