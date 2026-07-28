# DuckDock 2.0 v1 → v2 API 与数据资产盘点

> 状态：Sprint 0 inventory baseline
>
> 基线日期：2026-07-17
>
> 基线版本：`v0.1.0` / `33b4eec54b6ea9a44f612b886dfd6ce46c5870f6`
>
> 范围：当前 19 个 API 路由相关模块、57 个 SQLAlchemy 业务表、8 个 Celery task、2 个 beat schedule、3 类 Worker/Scheduler 进程入口和 2 个直接 dispatch seam
>
> 目的：为 expand/backfill/contract、`/api/v2`、兼容窗口和废弃计划提供唯一逐项清单

## 1. 盘点口径

### 1.1 API 计数

`backend/app/api/v1/router.py` 当前聚合 18 个 endpoint router；18 个 endpoint 文件共定义 202 个 HTTP endpoint decorator。本文将聚合模块本身计入路由相关模块，因此总数为：

```text
1 个聚合 router.py + 18 个 endpoint router = 19 个 API 路由相关模块
```

`backend/app/api/v1/endpoints/__init__.py` 只是包标记，不作为可执行 router 单独计数。FastAPI `main.py` 中的 `/health`、`/readyz` 和 well-known 路由也不计入 v1 业务路由模块。

### 1.2 数据表计数

表清单来自 `backend/app/models/*.py` 中实际存在的 `__tablename__`，共 57 个。2.0 Foundation 计划新增的 `AgentDeployment`、`DeploymentComponent`、`AgentSession`、`AgentRun`、`AgentRunArtifact`、`TelemetrySink`、`TraceBackendRef` 和 `OutboxEvent` 尚不存在，因此不计入 57 个当前表。

### 1.3 后台任务与 Worker 计数

静态 AST 与配置枚举得到以下当前基线：

```text
7 个 Celery task 模块 -> 8 个唯一注册 task
2 个 beat schedule -> 分别引用 2 个已注册 task
9 个可执行 .delay() 调用点 -> 引用其余 6 个已注册 task
3 类可执行进程入口 -> Celery worker、Celery beat、独立 analysis-worker poller
2 个命名的直接 dispatch seam -> webhook dispatch、report ingestion enqueue
```

`backend/app/workers/celery_app.py` 的 `include` 是 task 模块注册源；task 数量以 `@celery_app.task` 装饰器和 Celery 最终任务名为准。无显式 `name=` 的两个 task 使用 Celery 默认全限定名。Beat 条目、`.delay()` 调用点和 Compose/dev script 中的重复启动声明不重复计为新 task。

第三方 `langfuse-worker` 属于可选 telemetry provider，不是 DuckDock v1 领域 Worker；Celery signal hook、task 内部 async helper、数据库迁移 one-shot service 也不计为独立任务。当前代码中尚无可执行 `Outbox dispatcher`，其属于 Foundation 待实现项，不计入当前基线。

### 1.4 分类含义

| 分类 | 含义 | 迁移动作 |
|---|---|---|
| Keep | 继续作为 2.x 核心事实源，语义不替换 | 仅修复、索引或兼容性维护 |
| Extend | 保留当前身份和主要语义，增加租户、证据、策略或 v2 关联 | expand/backfill/contract；v1 行为保持兼容 |
| Migrate | 目标语义转移到 v2 新模型/服务，旧对象作为兼容投影或历史入口 | 双写、回填、对账、切读，最后冻结旧写入 |
| Deprecate | 已是 legacy，不再增加新能力 | 尽快冻结新写；保留受控读取/迁移工具 |
| Remove | 无保留价值且确认无外部依赖后删除 | 必须先有零引用、备份和回退证据 |

本轮没有任何 API 模块、业务表或后台入口直接标为 `Remove`。DuckDock 管理审计、证据和交接历史，Sprint 0 不允许仅凭代码观感做破坏性删除。

## 2. 兼容窗口

| 窗口 | 适用分类 | 承诺 |
|---|---|---|
| W0 | Keep | 2.x 全周期继续支持；无已计划移除版本 |
| W1 | Extend | `/api/v1` 和现有表语义至少支持至 2.0 GA 后两个 minor；新增能力优先走 v2；无已计划物理删除 |
| W2 | Migrate | 2.0 与 2.1 保持兼容读写/双写；至少一个成功发布窗口完成对账后才能切读；旧写最早在 2.2 默认关闭，物理删除不得早于 3.0 |
| W3 | Deprecate | v2 等价能力可用后立即冻结新功能；保留 admin/read/replay 至少两个正式版本；物理删除不得早于 3.0 且须连续两个版本零使用 |
| W4 | Remove | 仅限内部、无数据保留要求的对象；一个成功发布窗口、零引用证明和回退演练后删除 |

任何窗口缩短都必须有 ADR、使用量证据、数据导出/回退方案和 Gate 批准。

## 3. 风险代码

| 代码 | 风险 | 默认级别 |
|---|---|---|
| TENANT | 直接 Namespace 归属缺失或历史关系冲突 | 高 |
| COMPAT | v1 客户端、Reporter、Worker 或 UI 行为破坏 | 高 |
| DATA | 双写、回填、去重或历史数据丢失 | 高 |
| AUDIT | 审计/证据连续性和不可抵赖性不足 | 高 |
| SECURITY | 凭证、身份、权限扩大或越权 | 高 |
| PRIVACY | 原始对话、Prompt、Tool 参数或个人数据进入错误存储 | 高 |
| GATE | Release Gate 语义漂移、读取错误候选证据 | 高 |
| EXTERNAL | Provider/Runtime API 漂移或能力误报 | 中高 |
| VOLUME | Run/Span/Artifact 容量和查询性能 | 中高 |
| ORDERING | 异步事件乱序、重复、lease 或重放问题 | 中高 |
| OPS | 新组件、迁移、恢复和运行复杂度 | 中 |
| UX | v1/v2 同时存在造成入口和状态理解混乱 | 中 |

## 4. API 路由模块清单（19/19）

| ID | 当前模块 | 端点数 | 分类 | v2 目标领域/动作 | 窗口 | 主要风险 | 当前证据 |
|---|---|---:|---|---|---|---|---|
| API-01 | `api/v1/router.py` | 聚合 18 组 | Keep | 保留 v1 聚合；新增独立 `api/v2/router.py`，禁止把 v2 路由混入 v1 | W0 | COMPAT | `backend/app/api/v1/router.py:1-41` |
| API-02 | `auth` | 9 | Extend | Identity；保留登录/刷新，增加 production bootstrap、service/device identity 与可信 collector enrollment | W1 | SECURITY、COMPAT | `backend/app/api/v1/endpoints/auth.py:45` |
| API-03 | `iam` | 27 | Extend | Enterprise IAM；增加 SCIM、真实 IdP E2E、v2 service identity 和更严格作用域 | W1 | SECURITY、TENANT | `backend/app/api/v1/endpoints/iam.py:60` |
| API-04 | `namespaces` | 10 | Extend | Tenant Ownership；成为 Runtime/Asset/Binding/WorkTrace/Evidence 的直接租户边界 | W1 | TENANT、COMPAT | `backend/app/api/v1/endpoints/namespaces.py:25` |
| API-05 | `skills` | 22 | Migrate | Agent Package Registry；Skill v1 映射为 Package Component，发布入口逐步转向 PackageVersion | W2 | DATA、COMPAT、GATE | `backend/app/api/v1/endpoints/skills.py:56` |
| API-06 | `clawhub` | 13 | Extend | Distribution；继续提供 Skill 公共分发，同时支持签名 Package manifest 和 provenance | W1 | COMPAT、SECURITY | `backend/app/api/v1/endpoints/clawhub.py:50` |
| API-07 | `registry` | 5 | Extend | Registry Read API；增加 Agent Package、component graph 和不可变 manifest 查询 | W1 | COMPAT、DATA | `backend/app/api/v1/endpoints/registry.py:35` |
| API-08 | `scans` | 7 | Extend | Static Evaluation；扫描继续作为包级检查，结果可投影到未来 Eval/Release evidence | W1 | GATE、COMPAT | `backend/app/api/v1/endpoints/scans.py:26` |
| API-09 | `clinic` | 4 | Migrate | Eval Hub / Static Eval Suite；保持现有结果兼容，不再把 Namespace 最新评测等同候选版本证据 | W2 | GATE、DATA、UX | `backend/app/api/v1/endpoints/clinic.py:17` |
| API-10 | `components` | 4 | Migrate | Package Components；迁入 Agent Package component graph、依赖和版本锁定 API | W2 | DATA、COMPAT | `backend/app/api/v1/endpoints/components.py:10` |
| API-11 | `control_plane` | 59 | Migrate | Runtime Evidence/Fleet/Release/Handover；拆分到 v2 专域路由，v1 保持兼容投影 | W2 | TENANT、COMPAT、SECURITY、UX | `backend/app/api/v1/endpoints/control_plane.py:156` |
| API-12 | `agent_overview` | 3 | Deprecate | Fleet Read Model；由 `/api/v2/agent-runs`、deployment/session/run 查询和异步投影替代 | W3 | COMPAT、UX、DATA | `backend/app/api/v1/endpoints/agent_overview.py:14` |
| API-13 | `analysis` | 15 | Migrate | Evidence Materialization / Eval Worker；旧 Pack 分析保留兼容，新的 EvalRun 使用独立契约 | W2 | DATA、ORDERING、COMPAT | `backend/app/api/v1/endpoints/analysis.py:60` |
| API-14 | `people` | 2 | Extend | People & Handover；增加 evidence-driven readiness、责任图和 directory lifecycle 触发 | W1 | SECURITY、TENANT | `backend/app/api/v1/endpoints/people.py:13` |
| API-15 | `audit` | 1 | Migrate | Audit & Provenance；读取面保持兼容，写入迁至 transactional outbox + 不可变归档 | W2 | AUDIT、DATA | `backend/app/api/v1/endpoints/audit.py:10` |
| API-16 | `webhooks` | 6 | Extend | Event Delivery；Webhook 由 OutboxEvent 驱动，保留 HMAC、重试和现有管理 API | W1 | ORDERING、SECURITY、COMPAT | `backend/app/api/v1/endpoints/webhooks.py:10` |
| API-17 | `robots` | 4 | Migrate | Service Identity；Robot 收敛到统一 service/collector identity 与 scoped credential | W2 | SECURITY、COMPAT | `backend/app/api/v1/endpoints/robots.py:14` |
| API-18 | `lifecycle` | 5 | Extend | Data Lifecycle；配额/保留扩展到 Run 索引、artifact ref、legal hold 和 tombstone | W1 | DATA、AUDIT、VOLUME | `backend/app/api/v1/endpoints/lifecycle.py:20` |
| API-19 | `replication` | 6 | Extend | Package/Evidence Projection；扩展复制 manifest、provenance 和经批准的 evidence metadata | W1 | DATA、ORDERING、PRIVACY | `backend/app/api/v1/endpoints/replication.py:22` |

API 分类核对：`Keep=1`、`Extend=10`、`Migrate=7`、`Deprecate=1`、`Remove=0`，合计 `19`。

## 5. SQLAlchemy 业务表清单（57/57）

| ID | 当前表 | 分类 | v2 目标领域/动作 | 窗口 | 主要风险 | 当前证据 |
|---|---|---|---|---|---|---|
| DB-001 | `adapter_cursors` | Extend | Adapter State；增加 namespace、provider version、v2 stream/checkpoint 语义 | W1 | TENANT、ORDERING | `backend/app/models/control_plane.py:613` |
| DB-002 | `adapter_errors` | Extend | Adapter Operations；关联 ingest/outbox event、重试分类和 Runtime 版本 | W1 | ORDERING、EXTERNAL | `backend/app/models/control_plane.py:671` |
| DB-003 | `adapter_run_steps` | Extend | Adapter Operations；关联可信 ingestion run 和 outbox，不作为 Agent execution span | W1 | ORDERING、COMPAT | `backend/app/models/control_plane.py:631` |
| DB-004 | `agent_insight_jobs` | Migrate | Fleet Read Projection；转为基于 Deployment/Session/Run 的投影任务 | W2 | DATA、UX | `backend/app/models/control_plane.py:528` |
| DB-005 | `ai_assets` | Extend | Asset System of Record；增加直接 `namespace_id`、稳定 public id 和 Package/Deployment 关联 | W1 | TENANT、DATA | `backend/app/models/control_plane.py:585` |
| DB-006 | `analysis_result_artifacts` | Migrate | Run/Eval Artifact；迁入 AgentRunArtifact 或未来 EvaluationArtifact，保留对象 hash | W2 | DATA、PRIVACY | `backend/app/models/control_plane.py:503` |
| DB-007 | `analysis_workers` | Migrate | Worker Pool；统一为 materializer/evaluator worker identity 和 capability | W2 | SECURITY、ORDERING | `backend/app/models/control_plane.py:436` |
| DB-008 | `approval_tasks` | Extend | Release/Handover Approval；支持 release candidate、policy exception 和现有交接审批 | W1 | SECURITY、GATE | `backend/app/models/control_plane.py:838` |
| DB-009 | `asset_ownerships` | Extend | Responsibility Graph；关联 Package、Deployment、Agent 和 fallback owner | W1 | TENANT、DATA | `backend/app/models/control_plane.py:726` |
| DB-010 | `audit_logs` | Migrate | Audit Read Model；写入迁至事务 Outbox/不可变归档，表保留查询投影 | W2 | AUDIT、DATA | `backend/app/models/audit.py:8` |
| DB-011 | `clinic_evaluations` | Migrate | Static Eval Suite；映射未来 Evaluation/EvaluationMetricResult，保留历史分数 | W2 | GATE、DATA | `backend/app/models/clinic.py:30` |
| DB-012 | `collection_jobs` | Migrate | Evidence Ingestion Job；旧 Structured Report/Pack job 继续投影，v2 由 session/run/outbox 驱动 | W2 | ORDERING、COMPAT | `backend/app/models/control_plane.py:380` |
| DB-013 | `credential_records` | Extend | Secret Reference；保留 Fernet/db/env，增加外部 KMS/Vault port 和 key version | W1 | SECURITY | `backend/app/models/control_plane.py:294` |
| DB-014 | `evidence_items` | Extend | Evidence System of Record；增加直接 `namespace_id`、attestation、run/artifact ref | W1 | TENANT、AUDIT、PRIVACY | `backend/app/models/control_plane.py:788` |
| DB-015 | `execution_actions` | Extend | Controlled Action；保留交接回执，未来增加 lease、provider receipt 和强幂等 | W1 | SECURITY、AUDIT | `backend/app/models/control_plane.py:851` |
| DB-016 | `handover_cases` | Extend | Handover 2.0；增加 evidence snapshot、readiness 和 immutable package version | W1 | DATA、AUDIT | `backend/app/models/control_plane.py:804` |
| DB-017 | `handover_items` | Extend | Handover Obligation；关联 Deployment/Package/Run 风险和接收责任 | W1 | DATA、TENANT | `backend/app/models/control_plane.py:822` |
| DB-018 | `identity_links` | Extend | Enterprise Identity；支持真实 IdP/SCIM lifecycle 和可信 external identity | W1 | SECURITY、DATA | `backend/app/models/iam.py:269` |
| DB-019 | `memory_candidates` | Migrate | Finding/Knowledge Candidate；区分风险、知识候选与运行证据，不存原始 memory body | W2 | PRIVACY、DATA | `backend/app/models/control_plane.py:553` |
| DB-020 | `namespace_governance_policies` | Extend | Policy Engine；增加 evidence/eval/release gate、shadow/warn/enforce 和 exception 规则 | W1 | GATE、COMPAT | `backend/app/models/governance.py:12` |
| DB-021 | `namespace_members` | Keep | Namespace Membership；继续作为 namespace 授权兼容基础 | W0 | SECURITY | `backend/app/models/namespace.py:40` |
| DB-022 | `namespace_quotas` | Extend | Quota；扩展 Run、artifact、Eval、retention 和 ingest rate 配额 | W1 | VOLUME、COMPAT | `backend/app/models/lifecycle.py:25` |
| DB-023 | `namespaces` | Keep | Tenant Root；继续作为单企业实例内的治理隔离根 | W0 | TENANT | `backend/app/models/namespace.py:17` |
| DB-024 | `org_units` | Extend | Organization；供 ownership、handover、policy scope 和 directory sync 使用 | W1 | SECURITY、DATA | `backend/app/models/iam.py:84` |
| DB-025 | `permissions` | Extend | Authorization；增加 evidence ingest/read、eval、release、deployment 和 policy 权限 | W1 | SECURITY、COMPAT | `backend/app/models/iam.py:170` |
| DB-026 | `provider_principals` | Extend | Runtime Principal；映射 collector/service identity，并约束 namespace/runtime | W1 | SECURITY、TENANT | `backend/app/models/control_plane.py:687` |
| DB-027 | `public_skill_releases` | Extend | Public Package Distribution；保留 Skill release，增加签名 manifest/provenance 引用 | W1 | COMPAT、SECURITY | `backend/app/models/public_release.py:19` |
| DB-028 | `raw_collection_records` | Migrate | Evidence Envelope Metadata；原始高容量 payload 留 MinIO/telemetry backend，MySQL 保存 hash/ref | W2 | VOLUME、PRIVACY、DATA | `backend/app/models/control_plane.py:645` |
| DB-029 | `registry_sync_events` | Migrate | Outbox Projection；由事务 `OutboxEvent` 驱动 Registry/Webhook/Replication 投影 | W2 | ORDERING、DATA | `backend/app/models/sync_event.py:29` |
| DB-030 | `replication_jobs` | Extend | Replication Execution；支持 Package manifest、provenance 和受控 evidence metadata | W1 | ORDERING、PRIVACY | `backend/app/models/replication.py:47` |
| DB-031 | `replication_rules` | Extend | Replication Policy；增加对象类型、敏感级别和目标能力约束 | W1 | PRIVACY、GATE | `backend/app/models/replication.py:21` |
| DB-032 | `report_analysis_jobs` | Migrate | Materialization/Eval Job；旧 Pack job 保持兼容，未来 EvalRun 使用独立领域模型 | W2 | ORDERING、DATA | `backend/app/models/control_plane.py:461` |
| DB-033 | `report_upload_sessions` | Extend | Artifact Upload Compatibility；保留 presigned upload，增加 v2 run/evidence artifact 绑定 | W1 | SECURITY、DATA | `backend/app/models/control_plane.py:395` |
| DB-034 | `reporter_credentials` | Migrate | Collector Credential；迁入统一 service/device identity，保留 scope/rotation/revoke 语义 | W2 | SECURITY、COMPAT | `backend/app/models/control_plane.py:352` |
| DB-035 | `retention_policies` | Extend | Data Lifecycle；增加 Run index、artifact、legal hold、audit tombstone 和 provider retention | W1 | AUDIT、PRIVACY、DATA | `backend/app/models/lifecycle.py:9` |
| DB-036 | `robot_accounts` | Migrate | Service Identity；与 Reporter/Adapter/Worker 身份统一，旧 token 仅作兼容 | W2 | SECURITY、COMPAT | `backend/app/models/robot.py:9` |
| DB-037 | `role_bindings` | Extend | Scoped Authorization；强化 system/org/namespace 与 service identity 绑定 | W1 | SECURITY、TENANT | `backend/app/models/iam.py:218` |
| DB-038 | `role_permissions` | Keep | RBAC Relation；继续作为 Role 与 Permission 的实时关系源 | W0 | SECURITY | `backend/app/models/iam.py:206` |
| DB-039 | `roles` | Extend | RBAC Roles；增加 2.0 内置角色和不可越权 scope 校验 | W1 | SECURITY、COMPAT | `backend/app/models/iam.py:184` |
| DB-040 | `runtime_bindings` | Extend | Runtime/Asset Binding；增加直接 `namespace_id`、Deployment 和有效版本引用 | W1 | TENANT、DATA | `backend/app/models/control_plane.py:741` |
| DB-041 | `runtime_capability_snapshots` | Extend | Fleet Capability；改为真实 handshake 产生的版本化快照，禁止硬编码成功 | W1 | EXTERNAL、DATA | `backend/app/models/control_plane.py:712` |
| DB-042 | `runtime_instances` | Extend | Runtime System of Record；增加直接 `namespace_id`，产生 Run 后禁止无审计转租户 | W1 | TENANT、COMPAT | `backend/app/models/control_plane.py:306` |
| DB-043 | `runtime_report_tokens` | Deprecate | Legacy Report Token；由 Reporter/Collector service credential 替代，仅保留 admin 兼容和迁移 | W3 | SECURITY、COMPAT | `backend/app/models/control_plane.py:333` |
| DB-044 | `sandbox_validation_runs` | Extend | Static/Runtime Validation；继续服务 Package Gate，并关联 package/release candidate | W1 | GATE、OPS | `backend/app/models/sandbox_validation.py:19` |
| DB-045 | `scan_results` | Extend | Static Evaluation Result；保持当前 Gate 兼容，未来绑定精确 Package/ReleaseCandidate | W1 | GATE、DATA | `backend/app/models/scan.py:27` |
| DB-046 | `scanner_rule_suppressions` | Keep | Scanner Governance；保留现有 namespace 级抑制语义和审计要求 | W0 | GATE、SECURITY | `backend/app/models/scanner_suppression.py:17` |
| DB-047 | `skill_versions` | Migrate | PackageVersion/ComponentVersion；双写版本、hash、Gate 状态和 provenance | W2 | DATA、COMPAT、GATE | `backend/app/models/skill.py:52` |
| DB-048 | `skills` | Migrate | AgentPackage/Component；保留 Skill 作为一种组件类型和 v1 兼容视图 | W2 | DATA、COMPAT | `backend/app/models/skill.py:28` |
| DB-049 | `sso_provider_configs` | Extend | Enterprise Identity Provider；完成真实 OIDC/LDAP 验证、secret rotation 和出站安全 | W1 | SECURITY、OPS | `backend/app/models/iam.py:238` |
| DB-050 | `sso_role_mappings` | Extend | Identity Provisioning；支持 SCIM/group claim 到 v2 role/scope 的确定性映射 | W1 | SECURITY、TENANT | `backend/app/models/iam.py:307` |
| DB-051 | `user_affiliations` | Extend | People/Organization；支持 directory lifecycle、owner/fallback owner 和交接触发 | W1 | DATA、SECURITY | `backend/app/models/iam.py:114` |
| DB-052 | `user_handover_profiles` | Extend | Handover Identity；增加 readiness、receiver policy 和自动触发状态 | W1 | DATA、TENANT | `backend/app/models/iam.py:141` |
| DB-053 | `users` | Extend | Enterprise User；完成 SCIM 状态、production bootstrap 关闭和统一 enterprise identity | W1 | SECURITY、COMPAT | `backend/app/models/user.py:26` |
| DB-054 | `webhook_deliveries` | Extend | Outbox Delivery Projection；保留 delivery history，关联 outbox event 和 provider receipt | W1 | ORDERING、AUDIT | `backend/app/models/webhook.py:44` |
| DB-055 | `webhooks` | Extend | Event Subscription；增加 v2 evidence/eval/release 事件和敏感字段策略 | W1 | SECURITY、PRIVACY | `backend/app/models/webhook.py:22` |
| DB-056 | `work_artifacts` | Migrate | AgentRunArtifact；迁移 artifact metadata/hash/ref，v1 WorkTrace 继续可读投影 | W2 | DATA、PRIVACY | `backend/app/models/control_plane.py:773` |
| DB-057 | `work_traces` | Extend | Management Work Summary；增加直接 `namespace_id`，继续保留，不升级为 span tree；可选关联 AgentRun | W1 | TENANT、COMPAT、PRIVACY | `backend/app/models/control_plane.py:755` |

数据表分类核对：`Keep=4`、`Extend=37`、`Migrate=15`、`Deprecate=1`、`Remove=0`，合计 `57`。

## 6. 后台任务、Beat 与 Worker 入口清单

### 6.1 Celery 注册任务（8/8）

| ID | 当前 Celery task 名 | 当前触发/调度入口 | 分类 | v2 目标领域/动作 | 窗口 | 主要风险 | 当前证据 |
|---|---|---|---|---|---|---|---|
| TSK-001 | `run_agent_insight_job` | Agent Overview 创建 job 后直接 `.delay()` | Deprecate | Fleet Read Projection；v2 Deployment/Session/Run 投影等价后冻结独立 LLM insight job 新写，保留历史结果读取 | W3 | DATA、UX、COMPAT | `backend/app/workers/agent_insight_tasks.py:8`；`backend/app/api/v1/endpoints/agent_overview.py:52` |
| TSK-002 | `reap_expired_analysis_jobs` | Beat `reap-expired-analysis-jobs`，固定 60 秒 | Migrate | Materializer/Eval lease maintenance；W2 内继续清理 legacy AnalysisJob，后续按新任务类型和 lease 状态迁移 | W2 | ORDERING、DATA、OPS | `backend/app/workers/analysis_tasks.py:8`；`backend/app/workers/celery_app.py:46` |
| TSK-003 | `run_clinic_evaluation` | Clinic 创建 evaluation 后直接 `.delay()` | Migrate | Static Eval execution；保留当前 Clinic 输出兼容，后续映射版本化 EvalRun/MetricResult，禁止读取“Namespace 最新评测”作为候选证据 | W2 | GATE、DATA、COMPAT | `backend/app/workers/clinic_tasks.py:27`；`backend/app/api/v1/endpoints/clinic.py:46` |
| TSK-004 | `app.workers.lifecycle_tasks.run_gc` | 管理 API 手动触发；Retention beat 按 Namespace fan-out | Extend | Data Lifecycle executor；扩展到 Run index、artifact ref、legal hold、audit tombstone 和 provider retention 协调 | W1 | AUDIT、PRIVACY、DATA | `backend/app/workers/lifecycle_tasks.py:15`；`backend/app/api/v1/endpoints/lifecycle.py:243`；`backend/app/workers/lifecycle_tasks.py:41` |
| TSK-005 | `run_retention_gc` | Beat `run-retention-gc`，周期来自 `RETENTION_GC_SCHEDULE_SECONDS` | Extend | Data Lifecycle scheduler；继续按已配置 policy 扫描并 fan-out，同时纳入 v2 对象和可观测失败状态 | W1 | AUDIT、PRIVACY、OPS | `backend/app/workers/lifecycle_tasks.py:20`；`backend/app/workers/celery_app.py:52` |
| TSK-006 | `ingest_report_upload_session` | Report upload FSM 经 `_enqueue_report_ingestion()` 直接 `.delay()` | Extend | Artifact Upload Compatibility；保留 presigned upload/ingest FSM，增加 AgentRunArtifact、hash、Namespace/Runtime 绑定；完整 ATIF importer 后置 | W1 | DATA、SECURITY、COMPAT | `backend/app/workers/report_upload_tasks.py:8`；`backend/app/services/report_upload_service.py:381` |
| TSK-007 | `scan_skill_version` | Scans、Skills、ClawHub 发布路径直接 `.delay()` | Extend | Package Static Evaluation；保留当前 Scanner/Gate 行为，增加精确 PackageVersion/ReleaseCandidate、provenance 和 policy 关联 | W1 | GATE、DATA、COMPAT | `backend/app/workers/scan_tasks.py:70`；`backend/app/api/v1/endpoints/scans.py:150`；`backend/app/api/v1/endpoints/skills.py:890`；`backend/app/api/v1/endpoints/clawhub.py:713` |
| TSK-008 | `app.workers.webhook_tasks.deliver_webhook` | `webhook_service.dispatch_event()` 按订阅逐个 `.delay()` | Extend | Event Delivery consumer；保留 HMAC、SSRF 防护、重试和 delivery history，改为消费 Outbox event 并记录 event/receipt 关联 | W1 | ORDERING、SECURITY、AUDIT | `backend/app/workers/webhook_tasks.py:68`；`backend/app/services/webhook_service.py:12` |

Celery task 分类核对：`Keep=0`、`Extend=5`、`Migrate=2`、`Deprecate=1`、`Remove=0`，合计 `8`。

### 6.2 Beat schedule（2/2，不重复计入 task 总数）

| ID | Beat entry | 引用 task | 当前周期 | 分类 | v2 目标动作 | 窗口 | 主要风险 | 当前证据 |
|---|---|---|---|---|---|---|---|---|
| BEAT-001 | `reap-expired-analysis-jobs` | `reap_expired_analysis_jobs` / TSK-002 | `60.0s` | Migrate | 在兼容窗口维持 legacy lease 回收；迁移后按 Materializer/Eval job 类型和队列执行同等可观测回收 | W2 | ORDERING、OPS | `backend/app/workers/celery_app.py:46` |
| BEAT-002 | `run-retention-gc` | `run_retention_gc` / TSK-005 | `RETENTION_GC_SCHEDULE_SECONDS` | Extend | 保留单实例调度，扩展 v2 retention 范围并记录每轮 fan-out/失败/跳过数量 | W1 | AUDIT、OPS、DATA | `backend/app/workers/celery_app.py:52`；`backend/app/core/config.py:125` |

Beat 分类核对：`Keep=0`、`Extend=1`、`Migrate=1`、`Deprecate=0`、`Remove=0`，合计 `2`；两个 `task` 值均可解析到 6.1 的已注册任务。

### 6.3 Worker、Scheduler 与直接 dispatch seam（5/5）

| ID | 当前入口 | 形态 | 分类 | v2 目标领域/动作 | 窗口 | 主要风险 | 当前证据 |
|---|---|---|---|---|---|---|---|
| WRK-001 | `celery -A app.workers.celery_app worker` | 通用 Celery worker 进程 | Keep | 继续作为 2.x 异步执行 runtime；新增队列/路由不得破坏现有 task 名、late ack 和 lost-worker 重投语义 | W0 | ORDERING、OPS、COMPAT | `docker-compose.yml:122-150`；`docker-compose.prod.yml:177-202`；`scripts/dev-start.ps1:63-70` |
| WRK-002 | `celery -A app.workers.celery_app beat` | 单实例 Celery scheduler 进程 | Keep | 继续承载明确登记的周期任务；生产保持单实例，新增 schedule 必须进入本清单并有重复调度防护 | W0 | ORDERING、OPS | `docker-compose.yml:152-179`；`docker-compose.prod.yml:208-236`；`scripts/dev-start.ps1:72-79` |
| WRK-003 | `duckdock_analysis_worker.py --poll` | 可选外部 polling worker，claim/heartbeat/finalize/fail | Migrate | Evidence Materializer / Eval Worker；统一 service identity、capability、lease、artifact/result contract，旧 `/api/v1/analysis` 协议保留 W2 兼容 | W2 | SECURITY、ORDERING、DATA | `docs/agent-control-plane-prd/duckdock-analysis-worker/scripts/duckdock_analysis_worker.py:116`；`docker-compose.yml:181-219`；`docker-compose.prod.yml:242-285` |
| DSP-001 | `webhook_service.dispatch_event()` | 查询订阅后直接 enqueue `deliver_webhook` | Migrate | Transactional Outbox projection；业务事务只写 OutboxEvent，dispatcher/consumer 再创建 delivery，避免 commit 与 enqueue 丢失窗口 | W2 | ORDERING、AUDIT、DATA | `backend/app/services/webhook_service.py:12-36` |
| DSP-002 | `report_upload_service._enqueue_report_ingestion()` | Upload FSM 后直接 enqueue ingest task | Migrate | Evidence ingestion dispatch；以提交后的 Outbox/幂等 command 驱动，保留同一 report session 的重放和失败可诊断性 | W2 | ORDERING、DATA、COMPAT | `backend/app/services/report_upload_service.py:381-387` |

Worker/dispatch 分类核对：`Keep=2`、`Extend=0`、`Migrate=3`、`Deprecate=0`、`Remove=0`，合计 `5`。其中 `WRK-001`～`WRK-003` 是 3 类可执行进程入口；`DSP-001`～`DSP-002` 是 2 个命名的直接 dispatch seam。当前不存在可执行 Outbox dispatcher，不能把 FND-068 计划项计为已实现入口。

## 7. 目标领域映射

| v2 领域 | 主要 v1 来源 | Foundation 新对象/接口 | 边界 |
|---|---|---|---|
| Tenant & Identity | Namespace、IAM、User、Robot、ReporterCredential | 直接 `namespace_id`、service/device identity | 不默认建设 SaaS 多租户 |
| Deployment Inventory | RuntimeInstance、AIAsset、RuntimeBinding、SkillVersion | AgentDeployment、DeploymentComponent | Deployment revision 不可变 |
| Runtime Evidence Index | WorkTrace、WorkArtifact、CollectionJob、RawCollectionRecord | AgentSession、AgentRun、AgentRunArtifact | 原始 span/prompt/tool payload 不进入 MySQL |
| Provider Integration | AdapterCursor/Step/Error、CapabilitySnapshot | TelemetrySink、TraceBackendRef、Provider Ports | 核心不得直接依赖 Langfuse/DeepEval/ATIF SDK |
| Reliable Events | RegistrySyncEvent、WebhookDelivery、直接 Celery enqueue | OutboxEvent | 领域写入与 outbox 同事务 |
| Evaluation | ClinicEvaluation、ScanResult、AnalysisJob/Artifact | 后续 Dataset/Evaluator/Evaluation/Experiment | Foundation 不改变当前 Release Gate |
| Release Control | SkillVersion、GovernancePolicy、Sandbox、Approval | 后续 ReleaseCandidate 与 candidate-pinned evidence | 禁止读取 Namespace“最新评测”冒充候选证据 |
| Handover | HandoverCase/Item、ExecutionAction、EvidenceItem | evidence snapshot、readiness、signed package | LLM 仍只建议，执行需审批和回执 |

## 8. 迁移与兼容规则

### 8.1 Expand / Backfill / Contract

1. 先为 `runtime_instances`、`ai_assets`、`runtime_bindings`、`work_traces`、`evidence_items` 增加 nullable、indexed `namespace_id`。
2. 仅使用已批准的确定性关系回填；无法确定的记录标为 `unresolved`，多租户冲突标为 `conflict`。
3. Contract migration 在 `unresolved/conflict > 0` 时必须中止并输出可操作修复清单。
4. 新写路径从 expand 发布开始强制显式 Namespace；禁止用默认 Namespace 静默填充。
5. 至少经过一个成功发布窗口和真实 MySQL 对账后，才能增加 non-null/tenant consistency 约束。

### 8.2 v1/v2 双轨

- Foundation 阶段 WorkTrace、Structured Report、Clinic 和当前 Release Gate 行为保持兼容。
- AgentRun 是执行索引，不替代 WorkTrace 的管理摘要语义。
- v1 Reporter/Pack 通过兼容 consumer 产生 v2 session/run/artifact ref；不得让 v2 反向依赖 v1 JSON 结构。
- Migrate 对象必须保存稳定 `legacy_source_type/legacy_source_id` 或等价映射。
- 切换 v2 读路径前，每日对账数量、public id、hash、Namespace、Runtime、Asset 和 Artifact 关联。
- incident backout 只关闭新 ingestion/dispatcher/读路由，不删除已写 Run、Outbox 或 Evidence。

### 8.3 Release Gate 防漂移

- Foundation 实现前后，已刻画的当前 Gate 输入/输出必须保持一致。
- 新 runtime evaluation 只有绑定到精确 AgentDeployment/PackageVersion/ReleaseCandidate 后才能成为 Gate 输入。
- `ClinicEvaluation` 的 Namespace 最新记录不得被自动解释为当前候选版本评测。
- 每次 Gate 决策保存 policy version、candidate identity、evidence refs 和 evaluator identity。

## 9. Remove 候选（不计入当前 19/57/8 删除项）

以下是实现行为或兼容路径，不是本轮直接删除的业务表/模块：

| 候选 | 当前问题 | 删除前置条件 | 最早窗口 |
|---|---|---|---|
| Generic Adapter 无真实连接却返回 `status=ok` | 能力误报 | 动态 handshake 上线、conformance 通过、UI 使用新状态 | G2 后 |
| 硬编码 Runtime capability matrix | 与真实 Provider 能力脱节 | CapabilitySnapshot 全部来自探测并可降级 | G2 后 |
| OpenClaw `sample_collection` 作为产品采集路径 | 测试数据被误认为 live collection | 官方 Adapter 或受信 backup importer 就绪 | G2 后 |
| `RuntimeReportToken` 新建入口 | 与 ReporterCredential 双轨 | 全部活跃客户端迁移并连续两个版本零使用 | 3.0 最早 |
| admin direct report ingest | 绕过 worker-first 标准路径 | replay 工具和 worker pipeline 覆盖全部调试场景 | 3.0 最早 |
| `agent_overview` 独立 LLM job 写路径 | 与 Run/Fleet 投影重复 | v2 read model 等价、对账和 UI 切换完成 | 3.0 最早 |

## 10. 盘点完成与后续更新标准

本文件只有同时满足以下条件才可标记为已完成：

- API 表恰好包含 `API-01`～`API-19`，无重复和缺号。
- 数据表恰好包含 `DB-001`～`DB-057`，与代码中 `__tablename__` 集合完全一致。
- Celery task 表恰好包含 `TSK-001`～`TSK-008`，与 `backend/app/workers` 中实际 `@celery_app.task` 的最终任务名集合完全一致。
- Beat 表恰好包含 `BEAT-001`～`BEAT-002`，每个 `task` 都能解析到已登记的 TSK；Compose 与本地脚本必须继续提供独立 beat 进程。
- Worker/dispatch 表恰好包含 `WRK-001`～`WRK-003` 和 `DSP-001`～`DSP-002`；新增队列消费者、周期任务、直接 `.delay()` seam 或 Outbox dispatcher 时必须同步登记。
- 每项都有分类、目标领域/动作、兼容窗口、风险和当前证据路径。
- API、表、Celery task、Beat、Worker/dispatch 各自的 `Keep + Extend + Migrate + Deprecate + Remove` 分类合计必须与各自实际总数一致。
- 后续新增、删除、重命名路由、表、task、schedule、Worker 或 dispatch seam 的 PR 必须同步更新本文件。
- 任何 `Deprecate → Remove` 变化必须关联使用量、迁移、数据保留和回退证据。
