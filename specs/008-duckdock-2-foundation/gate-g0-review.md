# G0 Architecture Gate Review

**Gate**: G0 — Architecture Freeze

**Review date**: 2026-07-17

**Technical result**: PASS

**Effective gate status**: APPROVED / ACTIVE

**Implementation authorization**: GRANTED FOR THE BOUNDED S1 BATCH IN SECTION 6

## 1. Decision Summary

DuckDock 2.0 的产品边界、数据归属、v1→v2 迁移范围、核心领域模型、四类版本化契约、Reporter API v2、Adapter conformance 和测试/回滚方案已达到 G0 技术冻结标准。独立复核中发现的信任模型、Reporter 身份注入、metadata 绕过、waiver、EvaluationResult 生命周期、Problem envelope、API/Quickstart 漂移和 Sprint 边界问题均已关闭。

Product/Architecture Owner 于 2026-07-17 明确回复“批准 G0”。G0 自该批准起生效，并且只授权第 6 节定义的 S1 首批工作；超出该边界的实现仍需后续 Gate 或单独批准。

## 2. Gate Evidence Matrix

| G0 criterion | Result | Evidence | Review note |
|---|---|---|---|
| 产品定位与非目标 | PASS | [Constitution 2.0](../../.specify/memory/constitution.md), [spec](spec.md), [progress SSOT](../../docs/duckdock-2-upgrade-progress.zh-CN.md) | DuckDock 是 provider-neutral Evidence/Eval/Release/Handover Control Plane，不建设通用 Runtime/IDE |
| 核心架构决策 | PASS | [ADR-0201～0210](../../docs/adr/README.md) | 模块化单体；MySQL 治理事实、MinIO artifact、外部 telemetry；transactional outbox |
| v1→v2 inventory | PASS | [inventory](v1-v2-inventory.md) | 19 个路由相关模块、202 decorators、57 表、8 Celery tasks、2 Beat entries、5 Worker/dispatch seams |
| 强类型领域与迁移模型 | PASS | [data model](data-model.md), [implementation plan](plan.md) | WorkTrace 与 AgentRun 分离；expand/backfill/contract；ambiguous tenant fail-closed |
| Agent Package v2 | PASS | [schema](contracts/agent-package-v2.schema.json), [example](contracts/examples/agent-package-v2.example.json) | immutable components、digest、provenance；required telemetry attributes 使用 allowlist |
| Telemetry Envelope v1 | PASS | [schema](contracts/telemetry-envelope-v1.schema.json), [example](contracts/examples/telemetry-envelope-v1.example.json) | metadata-only example；attribute key/size/item bounds；raw content 不进入 MySQL |
| Evaluation Result v1 | PASS | [schema](contracts/evaluation-result-v1.schema.json), [example](contracts/examples/evaluation-result-v1.example.json) | 只表达 immutable completed evidence；queued/running 生命周期留给 EvalRun |
| Release Manifest v1 | PASS | [schema](contracts/release-manifest-v1.schema.json), [example](contracts/examples/release-manifest-v1.example.json) | waiver 必须有原因和到期时间；人工批准与 rollback evidence 必需 |
| Reporter/OpenAPI v2 | PASS | [OpenAPI](contracts/openapi-v2.yaml), [Quickstart](quickstart.md) | 6 paths；ReporterCredential 派生 Namespace/Runtime；写 schema 不接受客户端治理身份 |
| Adapter compatibility | PASS | [conformance v0.2](adapter-conformance.md) | OpenClaw、Generic OTLP、Pack/ATIF 共用 identity/run/replay/error contract；AgentLoop/WorkBuddy 完成可行性映射 |
| 隐私与信任边界 | PASS | [ADR-0207](../../docs/adr/0207-collector-trust-boundary.md), [ADR-0209](../../docs/adr/0209-metadata-first-content-policy.md) | `trust_level` 与 `trust_source` 分离；checksum 不等于 attestation；Secret Canary 跨 sink 门禁已定义 |
| 测试、迁移与回滚计划 | PASS | [tasks](tasks.md), [plan](plan.md), [quickstart](quickstart.md) | S1-S2 test-first；真实 MySQL；应用先回滚、schema forward-fix；不删除已写 evidence |

## 3. Closed Review Findings

| Finding | Resolution |
|---|---|
| Reporter 请求可提交 `namespace_id` / `runtime_instance_id` | 从所有 Reporter 写 schema 删除；严格 unknown-field rejection；服务端从 credential 派生 |
| Trust 枚举混合来源和保证强度 | 固定 `CHANNEL_AUTHENTICATED / PRODUCER_ATTESTED / UNVERIFIED`，另设 `REPORTER / COLLECTOR / IMPORT / ADMIN` source |
| Run complete 混入完整 telemetry 和 EvaluationResult | 写接口只接受 lifecycle aggregate；外部 trace/eval 通过 provider-neutral refs 和异步流程关联 |
| metadata 可借别名携带 prompt/tool/secret | OpenAPI、Telemetry 和 Package annotations/required attributes 使用明确 allowlist 与大小边界 |
| Release waiver 可无期限、无理由 | `decision=waived` 强制 `waiver_reason` + `waiver_expires_at`；pass 禁止 waiver 字段 |
| EvaluationResult 同时表达 queued/running 与终态证据 | v1 Result 固定 `status=completed`；运行态留给后续 EvalRun |
| Adapter 与 OpenAPI 使用两套 error envelope | 统一 `application/problem+json` 顶层 Problem，并强制 `retryable` 与受限 `details` |
| request replay 与 Outbox/consumer replay 混淆 | 分成 request、publisher、consumer、external receipt 四层，不宣称 exactly-once transport |
| Adapter G0 条件越过 S0 进入真实实现 | G0 只验收 contract/mapping/fixture plan；真实 DD-C1/DD-C2/DD-C3 认证归 G1/G2 |
| AgentLoop/WorkBuddy mapping 被整体延期 | G0 已建立 provider-neutral concept/profile mapping；私有 connector 代码仍延期 |
| Spec、OpenAPI 与 Quickstart 路径/payload 漂移 | 统一 `/api/v2/reporter/*`，新增自动解析四个 Quickstart HTTP block 的回归测试 |

## 4. Verification Record

Executed from repository root on 2026-07-17:

```powershell
conda run -n base python -m pytest backend\tests\test_duckdock_v2_contracts.py backend\tests\test_duckdock_v2_openapi_contract.py -q --noconftest
# 27 passed in 0.78s

conda run -n base python -m ruff check backend\tests\test_duckdock_v2_contracts.py backend\tests\test_duckdock_v2_openapi_contract.py
# All checks passed!
```

Independent Draft 2020-12 validation result:

```text
schemas=4 examples_valid=4 openapi_paths=6 internal_refs=80 external_refs=4
```

Scope note: this batch changes architecture documents, schemas and focused tests only. It does not change application behavior, models, migrations, dependencies or deployment configuration. The normal backend suite loads the application test fixture and requires `aiomysql`, which is absent in the current base environment; full backend and real-MySQL lanes remain mandatory before any S1 behavior merge.

OpenAPI note: the local environment does not contain Spectral, Prance or `openapi-spec-validator`. G0 validation therefore uses PyYAML parsing, complete internal/external `$ref` resolution, path-parameter checks and focused contract tests. A full OAS linter is a required S1 CI setup item before the first v2 endpoint merges; it is not a blocker for freezing this draft.

## 5. Residual Risks and Conditions

| ID | Severity | Condition | Required closure |
|---|---|---|---|
| G0-C01 | CLOSED | Product/Architecture Owner approved the frozen package on 2026-07-17 | No further action |
| G0-F01 | P1 pre-merge | Full OpenAPI metaschema/style linter is not installed locally | Add Spectral or equivalent CI check before first `/api/v2` endpoint merge |
| G0-F02 | CLOSED | Behavior baseline and populated real-MySQL migration lane were required before the first S1 expand merge | Closed by the post-approval evidence in Section 8 |

No technical design blocker or approval blocker remains open. `G0-F01` and `G0-F02` remain implementation conditions for the bounded S1 batch, not blockers to G0 activation.

## 6. Authorized Next Batch After Approval

Approval authorizes only the first S1 batch:

1. Add failing characterization tests for current WorkTrace/structured-report behavior.
2. Add failing characterization tests for current Release Gate pass/fail/no-evaluation behavior.
3. Add tenant isolation and architecture boundary tests, including “no raw Span/Prompt/ToolEvent MySQL model”.
4. Make those baseline tests green without changing behavior.
5. Prepare the nullable Namespace expand migration and its failing real-MySQL fixtures; do not apply contract/non-null migration yet.

It does not authorize Kafka/Temporal, a second business database, raw trace storage in MySQL, automated production Gate enforcement, AgentLoop private SDK coupling or destructive v1 removal.

## 7. Sign-off

| Role | Decision | Date | Note |
|---|---|---|---|
| Technical review | PASS | 2026-07-17 | Contract, consistency, security and scope findings closed |
| Independent contract review | PASS | 2026-07-17 | 27 tests, Draft 2020-12 examples, OpenAPI refs and Quickstart verified |
| Product/Architecture Owner | APPROVE | 2026-07-17 | Explicit owner response: “批准 G0”; Section 6 is authorized |

Owner decision:

- [x] **APPROVE G0** — freeze the package and authorize Section 6.
- [ ] **APPROVE WITH CONDITIONS** — list explicit conditions and due dates before implementation.
- [ ] **REJECT / REVISE** — identify the contract, boundary or milestone that must change.

## 8. Post-approval S1 Batch Evidence

The bounded Section 6 batch was executed after Owner approval and did not cross the authorized contract/non-null boundary.

| Authorized item | Result | Evidence |
|---|---|---|
| WorkTrace/structured-report characterization | PASS | 2 new tests; current projection and first-write dedupe preserved |
| Release Gate characterization | PASS | 3 new tests; pass, below-threshold and no-evaluation results preserved |
| Tenant and architecture guards | PASS | 10 new tests; legacy tenant paths, raw-storage boundary and optional vendor imports locked |
| Unchanged behavior baseline | PASS | Foundation 21 passed/1 MySQL skip locally; full backend 644 passed/5 skips with CI-equivalent security env |
| Nullable Namespace expand | PASS | Five model FKs plus revision 0027; 8 migration tests on real MySQL 8.4 |
| Real-MySQL and Alembic gates | PASS | Complete MySQL marker lane 5 passed; empty `upgrade head`; `alembic check` reported no new operations |
| Static/type gates | PASS | Ruff and compile clean; mypy 80 errors remains below repository ceiling 82 |

At this checkpoint the next batch began at FND-011 Tenant Resolver tests. Sections 9–10 now record the later S1-B authorization and completion; mandatory Namespace writes, same-tenant enforcement and contract/non-null migration remain outside both completed batches.

## 9. S1-B Authorization

Product/Architecture Owner explicitly replied “批准 S1-B” on 2026-07-19. This authorizes only:

1. FND-011 resolver tests for precedence, repeatability, unresolved and conflict classification.
2. FND-016 deterministic `TenantResolutionService` implementation.
3. FND-017 idempotent backfill/audit command with batching, checkpointing, JSON/console reports and non-zero exit when unresolved/conflict remains.

S1-B does **not** authorize FND-012/013/018 mandatory new-write enforcement, cross-Namespace relationship rejection, FND-014/019 contract/non-null migration, or destructive cleanup of unresolved legacy rows.

## 10. S1-B Post-implementation Evidence

The authorized S1-B batch is complete and remains inside the Section 9 boundary.

| Authorized item | Result | Evidence |
|---|---|---|
| FND-011 resolver contract | PASS | 17 tests cover five entity types, precedence, repeatability, unresolved/conflict, sorted complete candidates, historical soft-deleted Namespace identity and forbidden weak evidence |
| FND-016 deterministic resolver | PASS | Read-only `TenantResolutionService`; typed FK evidence only; no Membership/name/URL/provider/metadata/default inference |
| FND-017 bounded backfill service | PASS | Dependency order, NULL-only scan, CAS writes, dry-run, idempotency, deterministic versioned reports, checkpoint resume and non-zero blocker exit |
| FND-017 operator CLI | PASS | Explicit dry-run/apply modes; apply write-quiescence acknowledgement; atomic checkpoint bound to mode, database identity hash and Alembic revision; commit/rollback tests |
| Evidence schema boundary | PASS / BLOCKER VISIBLE | NULL Evidence remains unresolved because the current model has no typed WorkTrace FK; CollectionJob, creator Membership, reverse ownership, URI and JSON are not used as substitutes |
| Real MySQL | PASS | 10-test marker lane; five S1-B fixtures cover empty DB, deterministic chain plus rerun, unresolved Evidence, multi-Namespace propagation and a real REPEATABLE READ CAS race |
| Repository regression | PASS | Full backend `675 passed, 10 skipped`; Foundation/S1-B focused tests green; Ruff and compile clean; mypy `80 <= 82` |
| Migration drift | PASS | Clean MySQL `upgrade head`; `alembic check` reported no new upgrade operations |

Operational semantics are deliberately transitional:

1. FND-017 scans only NULL `namespace_id` rows and never overwrites an existing direct value.
2. Existing direct values have rerun priority in S1-B. Full non-NULL relationship consistency is still owned by FND-013 and the FND-019 contract preflight.
3. Apply is safe only under the write-quiescence condition documented in `tenant-backfill-runbook.md`; FND-018 must enforce Namespace-safe new writes before the final pass.
4. A bounded batch exit code describes that batch, not the entire database. Contract approval requires a complete fresh pass and zero blockers across all five tables.

No S1-C work is implied by this S1-B completion record. At the 2026-07-19
checkpoint, FND-012/013/014/018/019 and any Evidence typed-link schema change
still required a later explicit instruction.

## 11. S1-C Publication Authorization and Boundary

The available owner record does not support the previous claim that the
Product/Architecture Owner explicitly authorized S1-C implementation on
2026-07-20. That claim is withdrawn.

On 2026-07-28, the Product/Architecture Owner instructed:
“在哪个分支上开发，请把现有已经完成的做一个完整的远程github合并提交。本地远程保持代码最新。”
This is the verifiable authorization to package the already completed work in
a feature branch, submit it through a GitHub pull request, merge it through the
protected `main` branch, and synchronize the local checkout. It is a
publication authorization for existing work, not retrospective evidence of a
2026-07-20 implementation approval and not an authorization to expand the
implemented scope.

The publication batch contains these already implemented items:

1. FND-012 tests for mandatory authorized Namespace on new writes.
2. FND-013 tests and implementation rejecting cross-Namespace typed
   relationships.
3. FND-018 enforcement across current management, ingestion, materialization
   and handover write paths.
4. The expand-only Evidence typed WorkTrace link recorded by ADR-0211 and
   revision `20260720_0028`.

FND-014 and FND-019 contract preflight/non-null remain deferred. This
publication authorization does not authorize destructive cleanup, automatic
reassignment of historical rows, weak tenant inference, or any contract
migration.

## 12. S1-C Implementation Evidence

- `tenant_write_service.py` centralizes active Namespace and same-tenant
  invariants for RuntimeBinding, WorkTrace and EvidenceItem.
- Management schemas and callers require explicit Namespace for Runtime,
  Reporter enrollment, Asset and Handover creation; reporter-derived service
  writes inherit and validate the Runtime Namespace.
- Existing legacy rows with `namespace_id IS NULL` fail closed on live
  ingestion/materialization paths; they are never silently claimed outside the
  audited backfill command.
- Handover state writes, Runtime report-token management, Reporter heartbeat,
  Asset feedback and the admin compatibility ingest endpoint enforce the
  target active Namespace and typed same-tenant authorization before mutation.
- [ADR-0211](../../docs/adr/0211-evidence-worktrace-typed-link.md) and revision
  `20260720_0028` add a nullable indexed `EvidenceItem.work_trace_id` FK with
  `ON DELETE SET NULL`; the resolver uses only that typed link.
- Release-candidate validation is green: backend `701 passed, 10 skipped`
  with `76.90%` gated-module coverage; real MySQL 8.4 marker lane `10 passed`;
  Foundation/contracts `104 passed, 6 deselected`; frontend `21 passed`,
  lint with zero errors and production build; Ruff/compile/import clean; mypy
  `80 <= 82`; clean MySQL upgrade reaches `20260720_0028` and `alembic check`
  reports no new operations.
- FND-019 still requires a fresh full-database zero-blocker audit and its own
  explicit authorization; these release checks do not authorize contract or
  non-null DDL.
