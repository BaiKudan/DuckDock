# Feature Specification: Foundation Tenant Remediation and Contract

**Feature Branch**: `009-foundation-tenant-contract`
**Created**: 2026-07-28
**Status**: S1-D completed; revision 0029 applied and verified

## 1. Summary

Foundation `0027/0028` 已阻止新的无租户写入，但历史数据仍可能缺少
`namespace_id` 或可信 `EvidenceItem.work_trace_id`。现有 resolver 只能消费已批准的
typed evidence；当历史库没有这些证据时，它必须 fail-closed。

本特性补齐 Expand → Backfill → Contract 中间的人工处置能力：

1. 管理员用逐实体、逐 ID 的版本化 manifest 明确历史 Namespace 归属。
2. 平台在写入前验证管理员身份、目标存在性、关系一致性和 manifest 完整性。
3. 应用使用条件更新并在同一事务写审计，支持安全重放并拒绝冲突。
4. contract preflight 对 NULL 和跨 Namespace 关系给出可操作计数。
5. 只有完整 preflight 为零时，revision `20260728_0029` 才执行 non-null contract migration。

## 2. User Stories

### US-1 显式处置无法自动解析的历史记录（P0）

作为系统管理员，我可以提交一个经过变更单批准的 JSON manifest，为明确列出的历史实体指定
Namespace，而系统不会根据 Membership、名称、Provider、URL 或 metadata 猜测。

**Acceptance**

- Manifest 必须包含版本、唯一 ID、变更单、原因、批准管理员和 assignments。
- 每个 assignment 只包含 `target_type`、`target_id`、`namespace_id` 和可选说明。
- 不支持默认 Namespace、范围选择器、通配符或“所有剩余记录”。
- 批准人必须是 active system admin；Namespace 和目标必须存在。
- 同一 manifest 内目标不能重复。
- Dry-run 不写库；apply 需要显式确认关系写入已静默。
- 同值重放为 `already_assigned`；不同值重放/并发写入明确失败。

### US-2 在写入前验证跨实体一致性（P0）

作为数据治理人员，我希望 manifest 在任何写入前验证最终关系，使一次错误 assignment 不会制造
Runtime/Asset/Binding/WorkTrace/Evidence 的跨 Namespace 关系。

**Acceptance**

- RuntimeBinding 的 Namespace 必须等于 Runtime 和 Asset。
- WorkTrace 的 Namespace 必须等于所有已关联 Runtime/Asset。
- Evidence 有 typed WorkTrace 时必须与 WorkTrace 同 Namespace。
- Runtime 有绑定资产时，所有绑定资产必须已存在或在同一 manifest 中得到一致归属。
- 任一错误使整个 apply 回滚。

### US-3 Contract preflight（P0）

作为运维人员，我可以运行只读 preflight，得到每张目标表的 NULL 数和每类关系不一致数。

**Acceptance**

- 输出为稳定 JSON/人类可读格式，零阻塞退出 0，否则退出 2。
- 报告至少覆盖五张表 NULL、Binding、WorkTrace 和 Evidence typed link 不一致。
- 报告不输出用户内容、Prompt、摘要正文或凭据。
- Contract migration 复用同一组 blocker 定义，并在不安全时给出 remediation 命令。

## 3. Requirements

- **FR-001**: Manifest MUST conform to `tenant-remediation-manifest-v1.schema.json`.
- **FR-002**: Unknown fields MUST be rejected.
- **FR-003**: Every target MUST be enumerated by type and numeric ID.
- **FR-004**: Manifest application MUST be atomic and replay-safe.
- **FR-005**: Every changed target MUST have an audit entry containing only manifest metadata and hashes.
- **FR-006**: Assignment validation MUST use current/planned typed relationships, never weak inference.
- **FR-007**: Direct explicit assignment is an approved historical governance fact, not runtime-derived telemetry.
- **FR-008**: Preflight MUST validate both NULL values and non-NULL cross-tenant relationships.
- **FR-009**: Non-null contract DDL MUST remain blocked until preflight is clean on the target database.
- **FR-010**: Runtime behavior and default Compose MUST remain compatible while contract is pending.
- **FR-011**: An explicitly disposable non-production dataset MAY be removed instead of assigned only with
  Owner approval, a verified full backup, write quiescence, exact target counts and a retained zero-blocker
  report.

## 4. Non-goals

- 不自动把全部历史数据放进单一默认 Namespace。
- 不从 Namespace Membership、用户名、名称、Provider 或 JSON metadata 推断。
- 不自动创建或伪造 Evidence → WorkTrace 关系。
- 默认不删除历史记录或执行破坏性 downgrade；仅允许按 FR-011 精确清理已明确可丢弃的
  非生产测试数据。
- 不在 preflight 非零时强行执行 non-null DDL。

## 5. Success Criteria

- Manifest dry-run 与 apply 使用同一验证逻辑。
- 无效 manifest 在任何目标或审计写入前失败。
- 合法 apply 后再次运行得到全量 `already_assigned`。
- Contract preflight 对已知 NULL/跨租户夹具给出精确计数。
- Preflight 同时检测重复 Binding tenant key，避免 unique contract DDL 才暴露冲突。
- 真实 MySQL 验证原子 apply、冲突失败、unsafe migration 无部分 DDL，以及零阻塞后的 0029 升级。
