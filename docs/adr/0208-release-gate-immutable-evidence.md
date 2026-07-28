# ADR-0208: ReleaseGate 只消费固定且不可变的证据

- Status: Accepted
- Date: 2026-07-17

## Context

读取 Namespace 最新 Clinic 或 Evaluation 可能把其他版本的结果错误用于当前候选。可审计发布要求决策能够事后重放。

## Decision

- GateCheckResult 必须绑定 candidate digest。
- Runtime Regression 必须固定 DatasetVersion、EvaluatorVersion、Judge 配置、阈值和结果 hash。
- ReleaseDecision 保存完整 check snapshot 和 Policy version。
- 缺少强制证据时正式发布 Fail-closed。
- Judge 不可用时进入 REVIEW，不得默认通过。
- 人工 override 必须有原因、审批人和到期时间。

## Consequences

- 发布结果可复现并可审计。
- 不能用不断增长的 Dataset 或最新结果快捷放行。
- 评测和 Gate 需要清晰的生命周期协调。

## Validation

- 修改 Dataset draft 不改变既有 ReleaseDecision。
- 同一决策输入可重算得到相同确定性检查结果。
- 旧 Evaluation 不能匹配新的 candidate digest。
