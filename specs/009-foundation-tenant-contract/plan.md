# Implementation Plan: Foundation Tenant Remediation and Contract

## 1. Sequence

1. 冻结 manifest v1 JSON Schema 和 ADR-0212。
2. 先写 manifest 解析、权限、关系一致性、原子性和重放测试。
3. 实现 `tenant_remediation_service.py` 与 operator CLI。
4. 先写 contract preflight 的 SQLite/真实 MySQL 测试。
5. 实现只读 preflight service/CLI。
6. 对目标环境执行 manifest dry-run/apply 和完整 backfill。
7. 仅当完整 preflight 为零时增加并执行 contract migration。

## 2. Safety Model

- manifest 不提供 selector，只允许逐 ID assignment。
- apply 使用行锁和 NULL 条件更新；不同 Namespace 永不覆盖。
- 所有 assignment 先验证，随后在单一事务写入。
- 变更审计与目标写入共享事务，不调用会吞异常的审计 helper。
- CLI 从应用 `DATABASE_URL` 读取连接，不接受数据库密码参数。
- apply 必须携带 `--ack-write-quiescence`。

## 3. Files

```text
specs/009-foundation-tenant-contract/
  spec.md
  plan.md
  tasks.md
  data-model.md
  quickstart.md
  contracts/tenant-remediation-manifest-v1.schema.json
backend/app/schemas/tenant_remediation.py
backend/app/services/tenant_remediation_service.py
backend/app/services/tenant_contract_service.py
backend/scripts/remediate_foundation_tenants.py
backend/scripts/check_foundation_tenant_contract.py
backend/tests/foundation/test_tenant_remediation_*.py
backend/tests/foundation/test_tenant_contract_*.py
```

## 4. Contract Migration Gate

Migration is deliberately the final task. Merely implementing the preflight is not authorization to
alter columns. Before adding the migration, retain:

- complete backfill reports;
- remediation manifest hash and apply report;
- preflight JSON with every blocker count equal to zero;
- fresh and populated MySQL test evidence.

If any environment still has blockers, the application remains on expand-compatible schema and development continues
without destructive cleanup.
