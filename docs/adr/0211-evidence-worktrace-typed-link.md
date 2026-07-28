# ADR-0211: Evidence 使用可空 typed WorkTrace link

- Status: Accepted
- Date: 2026-07-20

## Context

Foundation tenant resolver 只允许从可审计的 typed relationship 推导历史
`namespace_id`。`EvidenceItem` 原先只有 `collection_job_id`、`created_by`、对象 URI
和自由文本/JSON；这些字段都不能证明某条 Evidence 属于哪条 WorkTrace，也不能安全
替代租户关系。因此无 direct Namespace 的历史 Evidence 必须保持 unresolved，并阻断最终
contract/non-null migration。

## Decision

- 为 `EvidenceItem` 增加可空、带索引的 `work_trace_id` 外键，指向
  `work_traces.id`，删除策略为 `SET NULL`。
- revision `20260720_0028` 只做 expand，不回填、不设置 non-null，也不启动
  FND-014/019 contract 工作。
- Evidence 新写若声明 WorkTrace，应用层必须验证
  `evidence.namespace_id == work_trace.namespace_id`；跨 Namespace 关系直接拒绝。
- Tenant resolver 只在 `work_trace_id` 存在且该 WorkTrace 可确定解析时使用此关系。
  WorkTrace conflict 继续向 Evidence 传播为 conflict；无 link 或 WorkTrace unresolved
  继续保持 unresolved。
- `CollectionJob`、Membership、owner/creator、对象 URI、名称、provider 和 JSON metadata
  不得作为 Evidence tenant 推断的替代证据。
- 导入 adapter 只接受显式的 WorkTrace external session id，并在同一规范化批次内解析；
  声明了但找不到 typed target 时整次写入失败，不静默降级为无 link Evidence。

## Consequences

- 新 Evidence 可以建立可验证的 WorkTrace lineage，历史 NULL tenant 可在具备可信 link
  后由现有 backfill resolver 确定性处理。
- 没有可信 WorkTrace 对应关系的历史 Evidence 仍需人工处置，不能因本 ADR 自动消除
  contract blocker。
- `SET NULL` 保留 Evidence 记录，但删除 WorkTrace 后会失去该条租户推断证据；删除和
  contract preflight 必须把这种退化纳入审计。
- 关系保持可空是 expand/backfill/contract 分阶段迁移的兼容要求；是否最终要求每条
  Evidence 都链接 WorkTrace 不在 S1-C 范围内。

## Validation

- 模型/迁移测试验证 nullable、单列索引、FK 与 `ON DELETE SET NULL`，并证明 0028
  不包含 backfill 或 contract DDL。
- resolver 测试覆盖 typed WorkTrace 的 resolved、unresolved/conflict 传播语义。
- 写服务测试验证缺少 Namespace 与跨 Namespace Evidence→WorkTrace 均被拒绝。
- 真实 MySQL migration lane 在 FND-019 前持续验证 `upgrade head`、schema drift 和
  populated database 兼容性。
