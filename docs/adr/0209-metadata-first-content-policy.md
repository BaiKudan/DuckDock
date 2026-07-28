# ADR-0209: 内容采集默认关闭

- Status: Accepted
- Date: 2026-07-17

## Context

Prompt、Response、Tool 参数和检索文档可能包含凭据、个人信息和商业秘密。为了调试便利而默认全量采集不符合 DuckDock 的最小暴露原则。

## Decision

- Content Policy 支持 disabled、metadata_only、sampled_content 和 full_content。
- 默认 metadata_only。
- Namespace 管理员必须显式授权 sampled/full，并设置保留期。
- Secret/PII 脱敏在 Collector 或边缘 Reporter 中完成。
- 隐藏 Chain-of-Thought 永不采集。
- Dataset 提升只保存完成评测所需的最小脱敏快照。

## Consequences

- 默认调试信息少于全量记录模式。
- 合规边界清晰，降低遥测数据泄漏风险。
- 必须提供内容缺失和采样完整度标识。

## Validation

- 默认配置下 Secret Canary 不进入 Langfuse、MySQL 或 MinIO。
- Policy 从 full 降级后新数据立即遵守新策略。
- Retention 到期能生成跨存储删除回执。
