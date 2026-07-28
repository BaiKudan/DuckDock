# ADR-0210: 业务事件使用 Transactional Outbox

- Status: Accepted
- Date: 2026-07-17

## Context

直接在业务事务提交后调用 Celery 属于 best-effort，进程崩溃会造成状态已经更新但任务未投递。评测、Gate 和证据物化不能接受这种静默缺口。

## Decision

- 新增通用 outbox_events 表。
- 业务聚合和 OutboxEvent 在同一 MySQL 事务中提交。
- Publisher 使用 lease、重试和 published_at 投递 Celery。
- 消费者按 event_id 幂等。
- 达到重试上限的事件进入可查询死信状态。
- 不在 Sprint 0 引入 Kafka；只有吞吐或消费者解耦达到量化阈值后重新评审。

## Consequences

- 异步状态不会因进程边界静默丢失。
- 需要 Outbox 清理、监控、重放和死信管理。
- 事件 payload 必须版本化并避免存入敏感大文本。

## Validation

- 在事务提交后、Celery 投递前模拟崩溃，事件仍能恢复发布。
- 重复发布不会产生重复 Evaluation 或 Gate 结果。
- backlog、最老未发布事件和死信数量具备告警。
