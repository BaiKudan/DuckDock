# Feature Specification: DuckDock 2.0 Foundation Implementation Phase

**Feature Branch**: `008-duckdock-2-foundation`

**Status**: G0 approved; S1-D tenant contract implemented; remaining Foundation work in progress

**Created**: 2026-07-17

**Target**: S1-S2 implementation after Architecture Gate G0

**G0 evidence**: [v1/v2 inventory](v1-v2-inventory.md), [data model](data-model.md), [adapter conformance](adapter-conformance.md), [contracts](contracts/README.md), [ADR index](../../docs/adr/README.md), [progress SSOT](../../docs/duckdock-2-upgrade-progress.zh-CN.md)

## 1. Summary

DuckDock 2.0 把现有的企业 Agent 资产、责任、审批和证据管理能力扩展为可承接运行轨迹的控制平面。本规格覆盖 G0 通过后的 S1-S2 Foundation 实现：明确租户归属、不可变 Deployment/Session/Run 索引、可信 Reporter 采集、可替换 Provider Port、事务 Outbox，以及安全迁移护栏。S0 只冻结架构、契约、迁移与测试计划，不实现本规格中的业务行为。

DuckDock 仍是企业治理事实源（system of record）。OpenTelemetry/Langfuse 等系统负责高吞吐执行遥测，MinIO 负责轨迹与评测内容制品，DeepEval 等评测引擎通过异步任务接入。原始 span、prompt、tool event 不进入 DuckDock MySQL。

## 2. Problem Statement

当前模型可以管理 Runtime、AI Asset、WorkTrace、Evidence 和发布门禁，但缺少以下基础能力：

- 核心控制平面实体没有稳定、直接的 `namespace_id`，跨租户查询和约束过度依赖间接关系。
- `WorkTrace` 表达的是工作摘要或管理轨迹，不能准确表达一次可重放、可评测的 Agent 执行。
- Runtime 上线后没有不可变部署快照，Run 无法回答“由哪一个 Agent/Skill/配置版本执行”。
- Reporter 上报缺少统一的 Session/Run envelope、状态机、幂等冲突检测和信任等级。
- 业务事务与异步投递之间没有事务 Outbox，存在提交成功但后续处理丢失的窗口。
- 若直接把 Langfuse、DeepEval 或某个外部轨迹 schema 写进核心模型，会造成供应商锁定和迁移困难。

## 3. Product Decision

采用“治理控制平面 + 可插拔遥测/评测数据平面”的结构：

1. MySQL 仅保存租户、部署、Session/Run 索引、状态、摘要计数、Provider 引用、审计和决策。
2. OpenTelemetry Collector 是遥测信任与治理边界；它完成身份映射、属性清洗、脱敏、采样和路由。
3. Langfuse 是可选的 trace provider，不是 DuckDock 的领域事实源。
4. ATIF 用于离线轨迹制品的导入、导出和重放，不作为实时消息总线。
5. DeepEval 等评测器在后续 Sprint 通过 `EvaluationEnginePort` 和 Celery 接入。
6. `WorkTrace` 与 `AgentRun` 并存；二者可关联，但含义和生命周期不得合并。

## 4. Goals

- 为 Runtime、Asset、Binding、WorkTrace、Evidence 建立可审计的直接租户归属。
- 建立不可变的 Agent Deployment 版本快照和低容量 Session/Run 控制索引。
- 提供由 `ReporterCredential` 认证的 Session/Run start/complete 写入通道。
- 由服务端推导 Runtime 和 Namespace，阻止客户端伪造治理属性。
- 对重复上报提供严格幂等；对同键不同载荷返回可诊断冲突。
- 在业务写入同一事务内产生 Outbox 事件，支持可靠异步扩展。
- 建立 Telemetry、Trajectory 和 Attestation 的 Provider Port，避免核心领域依赖具体厂商。
- 保持默认 Docker Compose、现有 WorkTrace 和现有 Release Gate 行为不变。

## 5. Non-Goals

本 Sprint 不包含：

- 在 MySQL 中保存原始 span、prompt、completion、tool arguments 或 tool results。
- 部署或强制启用 Langfuse、ClickHouse、OpenTelemetry Collector。
- 实现完整 ATIF 编解码、轨迹回放或跨平台导入器。
- 实现 DeepEval 指标、Dataset、Evaluator、Evaluation、Experiment 或评测 UI。
- 用运行评测结果替换当前 Release Gate 判定逻辑。
- 实现跨 Region 遥测复制、计费、成本归因或高基数分析。
- 将旧 `WorkTrace` 自动转换为可信 `AgentRun`。

## 6. Actors and User Stories

### US-1: 平台管理员登记不可变部署版本（P0）

作为平台管理员，我可以把 Runtime、Agent Asset、Skill 版本和配置摘要登记为一个 Deployment revision，使后续 Run 始终可定位到执行时版本。

**Acceptance**:

- Deployment 激活后，组件、revision、digest 和运行时关联不可修改。
- 变更配置必须创建新 revision；旧 revision 可退役但不可覆盖。
- Deployment、Runtime、Asset 和组件引用必须属于同一 Namespace。

### US-2: Reporter 可信登记 Session 和 Run（P0）

作为已注册 Runtime 的 Reporter，我可以用低容量 metadata envelope 开始 Session/Run 并完成 Run，DuckDock 自动绑定正确的 Runtime 和 Namespace。

**Acceptance**:

- 认证上下文来自 `ReporterCredential`，请求中的租户或 Runtime 声明不作为授权依据。
- 同一幂等键和同一规范化载荷重复请求返回原结果。
- 同一幂等键但载荷摘要不同返回 HTTP 409，并写安全审计事件。
- 默认 `content_capture_mode` 为 `metadata_only`，原始内容字段被拒绝。

### US-3: 治理人员查看跨系统 Run 索引（P0）

作为治理人员，我可以按 Namespace、Runtime、Deployment、Session、状态和时间查询 Run，并跳转到可选遥测后端，而 DuckDock 不复制原始 trace。

**Acceptance**:

- 列表和详情接口始终按当前 Namespace 过滤。
- 返回版本引用、状态、信任级别、摘要计数和 `TraceBackendRef`，不返回原始 prompt/span。
- 无权访问其他 Namespace 的标识时返回 404 或一致的拒绝响应，避免资源枚举。

### US-4: 运维人员安全迁移历史数据（P0）

作为运维人员，我可以先扩展 schema，再运行确定性租户回填审计，修复歧义后才收紧非空约束，系统不会猜测租户。

**Acceptance**:

- 回填结果区分 resolved、unresolved、conflict，并输出实体类型和 ID。
- Membership、名称或自由文本 metadata 不得作为租户推断依据。
- Contract migration 在仍有 unresolved/conflict 时明确失败，不静默填入默认 Namespace。

### US-5: 异步消费者可靠接收领域事件（P0）

作为后续评测或投影任务的开发者，我可以消费事务 Outbox 中的 Run 事件，不需要在请求事务中直接调用外部系统。

**Acceptance**:

- 业务状态和 Outbox event 在同一 MySQL 事务提交或回滚。
- Dispatcher 可以重复执行；相同 event 不会产生重复副作用。
- 失败包含次数、最近错误和下次重试信息，可由管理员重试。

### US-6: 平台工程师替换遥测 Provider（P1）

作为平台工程师，我可以配置 Namespace 级 Telemetry Sink，并通过稳定 Port 解析外部 trace 引用，而不让核心服务导入 Langfuse SDK。

**Acceptance**:

- Provider 配置只保存 `credential_ref`，不保存明文 secret。
- 未配置 Sink 时 Run 写入仍然成功。
- Provider 不可用只影响引用确认状态，不回滚已提交 Run。

## 7. Functional Requirements

### 7.1 Tenant ownership and migration

- **FR-001**: `RuntimeInstance`、`AIAsset`、`RuntimeBinding`、`WorkTrace`、`EvidenceItem` 必须新增直接 `namespace_id` 外键和索引。
- **FR-002**: 新写入路径必须显式提供由服务端授权上下文解析出的 Namespace；应用层不得创建无租户的新记录。
- **FR-003**: RuntimeBinding 的 Namespace 必须同时等于 Runtime 和 Asset 的 Namespace。
- **FR-004**: WorkTrace 关联 Runtime/Asset 时必须验证同租户；EvidenceItem 必须与其 WorkTrace 同租户。
- **FR-005**: 迁移采用 expand/backfill/contract 三阶段；contract 前必须执行可重复的审计命令。
- **FR-006**: 歧义历史记录必须保持未解析并进入人工处置清单；不得依据用户 Membership、名称或 metadata 猜测。
- **FR-007**: 所有新唯一约束都必须包含 `namespace_id`，除真正全局唯一的随机 public ID 和 event ID。

### 7.2 Immutable deployment inventory

- **FR-008**: 系统必须提供 `AgentDeployment`，记录 Namespace、Runtime、可选 Agent Asset、environment、external deployment ID、revision、configuration digest 和生命周期。
- **FR-009**: 系统必须提供 `DeploymentComponent`，记录 deployment-local `component_key`、component role、Asset/SkillVersion 引用、external version、content digest 和配置摘要。
- **FR-010**: Deployment 从 `REGISTERED` 激活为 `ACTIVE` 后，其 identity、revision、digest、Runtime 和 components 不可修改。
- **FR-011**: Deployment 仅允许 `REGISTERED -> ACTIVE -> RETIRED`，激活失败可进入 `FAILED`；退役不可恢复。
- **FR-012**: 激活前必须验证所有关联对象同租户，且每个组件至少存在一个可验证的版本引用或 content digest。

### 7.3 Session and Run control envelopes

- **FR-013**: 系统必须提供 `AgentSession`，以 `(namespace_id, runtime_id, external_session_id)` 唯一标识 Reporter 会话。
- **FR-014**: 系统必须提供 `AgentRun`，以 `(namespace_id, runtime_id, external_run_id)` 唯一标识一次执行尝试。
- **FR-015**: AgentRun 可选关联 AgentSession、AgentDeployment 和 WorkTrace；关联对象必须同租户。
- **FR-016**: Run 状态只允许 `STARTED -> SUCCEEDED|FAILED|CANCELLED|TIMED_OUT`；终态不可变。
- **FR-017**: Run start/complete 必须分别保存 idempotency key 和 canonical envelope SHA-256。
- **FR-018**: 同一幂等键、同一摘要的重放必须返回既有资源；同键不同摘要必须返回 409。
- **FR-019**: Run envelope 只允许固定白名单 metadata、时间、状态、错误分类、计数和外部 trace 标识；未知高风险内容字段必须拒绝。
- **FR-020**: `otel_trace_id`、`root_span_id` 如提供必须通过格式校验，但不得被视为授权或信任凭证。
- **FR-021**: Run 必须记录 source schema/name/version、normalizer version、`trust_level`、`trust_source` 和 content capture mode。
- **FR-022**: Bearer、mTLS 或受控 API key 最多赋予 `trust_level=CHANNEL_AUTHENTICATED`，并按入口记录 `trust_source=REPORTER|COLLECTOR|IMPORT`；只有 `AttestationVerifierPort` 成功才能升级为 `PRODUCER_ATTESTED`。Checksum 不得升级 trust level。

### 7.4 API behavior

- **FR-023**: 提供以下 metadata-only API：
  - `POST /api/v2/reporter/sessions`
  - `POST /api/v2/reporter/sessions/{session_public_id}/complete`
  - `POST /api/v2/reporter/runs`
  - `POST /api/v2/reporter/runs/{run_public_id}/complete`
  - `GET /api/v2/agent-runs`
  - `GET /api/v2/agent-runs/{run_public_id}`
- **FR-024**: 写接口必须使用 `ReporterCredential` 认证，并要求适用的 Run 写入 scope；服务端从 credential 解析 Runtime，再从 Runtime 解析 Namespace。
- **FR-025**: Reporter 写入 schema 不接受 `namespace_id`、`runtime_id` 或 `runtime_instance_id`；未知字段必须返回 422。服务端发现任何伪造治理身份尝试时必须审计。
- **FR-026**: 管理查询必须使用现有用户认证、Namespace 上下文和 RBAC，且不得暴露跨租户存在性。
- **FR-027**: 单个 envelope 必须设置大小上限，metadata 必须限制键数、嵌套深度和值长度。

### 7.5 Artifacts and provider references

- **FR-028**: 系统必须提供 `AgentRunArtifact` 索引，保存 object URI、SHA-256、size、schema/version、sensitivity、redaction policy version 和 completeness；内容保存在 MinIO。
- **FR-029**: Foundation implementation 只允许登记 artifact metadata，不要求实现 ATIF 内容解析。
- **FR-030**: 系统必须提供 Namespace 级 `TelemetrySink` 配置和 `TraceBackendRef`；核心模型使用 provider-neutral 字段。
- **FR-031**: `TelemetrySink` 只能保存 secret manager key/credential reference，不得保存 API key/token 明文。
- **FR-032**: 必须定义 `TelemetrySinkPort`、`TrajectoryCodecPort`、`AttestationVerifierPort`；核心 service 不得直接导入 Langfuse、DeepEval 或 Harbor/ATIF SDK。

### 7.6 Transactional outbox

- **FR-033**: Session/Run 状态变更必须在同一数据库事务写入 `OutboxEvent`。
- **FR-034**: Foundation implementation 至少产生 `AgentSessionStarted`、`AgentRunRegistered`、`AgentRunCompleted`、`AgentRunTrustDegraded`、`TraceArtifactStored`。
- **FR-035**: Outbox payload 只能包含路由和低敏摘要，不得复制 prompt、tool payload 或 artifact 内容。
- **FR-036**: Dispatcher 必须使用数据库租约或 `FOR UPDATE SKIP LOCKED` 安全并发认领，失败按退避策略重试。
- **FR-037**: 消费方必须用 event ID 或 idempotency key 去重；发布成功时间和 attempt 必须可查询。

### 7.7 Compatibility and release gates

- **FR-038**: WorkTrace API 和既有 structured report 流程保持兼容；AgentRun 不替代 WorkTrace。
- **FR-039**: Foundation implementation 不改变现有 Release Gate 结果，但必须在服务边界预留“按 release candidate 固定证据读取”的接口。
- **FR-040**: 后续 Release Gate 不得读取“Namespace 最新一次评测”作为候选版本证据；该限制必须作为架构决策和回归测试待办记录。
  EH-05/revision 0044 已通过 exact-selector
  `ReleaseCandidateEvaluationBinding` 和 fail-closed Candidate Release Gate
  实现该后续契约；旧 Skill/Clinic Gate 仍保持 Foundation 兼容行为。
- **FR-041**: 默认 Compose 不启动 observability profile，未安装可选 Provider 时核心 API 必须可用。

## 8. Security and Privacy Requirements

- **SR-001**: 所有 tenant-scoped repository 方法必须要求 `namespace_id` 参数，禁止无范围 `get(id)` 用于请求路径。
- **SR-002**: Reporter token 只以现有 hash 机制保存和验证，日志不得输出 token、Authorization header 或 credential ref 的解析值。
- **SR-003**: Envelope canonicalization 和 hash 计算发生在字段校验之后，防止不同 JSON 表达绕过幂等冲突检测。
- **SR-004**: 默认不采集内容；`content_capture_mode=metadata_only` 是 Foundation implementation 唯一可写模式。
- **SR-005**: 错误文本必须分类和截断；不得把 stack trace、prompt 或 tool result 写入 `error_message`。
- **SR-006**: Artifact URI 必须是受支持的内部对象存储 scheme/key，不允许任意 `file://` 或外部 URL 注入。
- **SR-007**: Telemetry Sink endpoint 必须经过 allowlist/SSRF 防护；Foundation implementation 不在同步请求中访问该 endpoint。
- **SR-008**: 所有跨租户、幂等冲突、信任降级、Deployment 激活和 Provider 配置变更必须进入现有审计链路。

## 9. Acceptance Scenarios

### A. Tenant migration

1. 在包含单一可解析关系的历史数据上运行 backfill，两次结果一致且第二次不产生额外变更。
2. 一个 Runtime 同时绑定两个不同 Namespace 的 Asset 时，Runtime 标记为 conflict，contract migration 明确中止。
3. 修复 conflict 后重跑审计，所有目标实体为 resolved，contract migration 成功设置非空和外键约束。

### B. Trusted ingestion

1. Reporter A 的 credential 启动 Run，数据库中的 Runtime/Namespace 来自 credential，不来自请求体。
2. Reporter A 声明 Reporter B 的 Runtime 或其他 Namespace，请求被拒绝且无 Run 写入。
3. 相同 start envelope 重放返回同一 Run；同键不同 `external_run_id` 返回 409。
4. complete 请求首次把 Run 置为终态；相同完成 envelope 重放成功；不同终态重放返回 409。
5. 含 `prompt`、`messages`、`tool_arguments` 或 `tool_result` 的请求返回 422。

### C. Transaction safety

1. 强制 Outbox insert 失败时，Run insert/transition 同时回滚。
2. Dispatcher 在发布后、确认前崩溃，重启后消费者按 event ID 去重，不产生重复投影。
3. Provider 不可达时，Run complete 仍提交，TraceBackendRef 保持 pending/error 并可重试。

### D. Compatibility

1. 既有 structured report 创建 WorkTrace 的测试保持通过。
2. 未配置 observability profile 或 Telemetry Sink 时，默认 Compose 和核心 API 启动成功。
3. 现有 Release Gate 的输入和输出在 Foundation implementation 前后保持一致。

## 10. Success Metrics

- 100% 新建 Runtime、Asset、Binding、WorkTrace、Evidence 具有直接 Namespace。
- 100% Session/Run 写入可追溯到 ReporterCredential、Runtime、Namespace 和 idempotency hash。
- P0 测试中跨租户读取/写入泄漏为 0。
- 幂等重放不增加 Session、Run 或 Outbox 记录数量。
- 业务事务与 Outbox 原子性测试覆盖全部 Foundation implementation 事件。
- MySQL 中新增的 prompt/span/tool content 列和表数量为 0。
- 默认 Compose 的服务集合和既有 Release Gate 行为无变化。

## 11. Deferred Work

- S3-S4: OTel Collector reference pipeline、OpenInference/GenAI normalization、Langfuse adapter、OpenClaw/Generic OTLP/Pack-ATIF conformance 实现与 trace reconciliation。
- S5-S6: ATIF codec、MinIO trajectory lifecycle、Dataset/Evaluator/Evaluation/Experiment、DeepEval adapter 和 baseline comparison。
- S7-S8: release-candidate-pinned evidence、运行评测 Gate、人工复核、Promotion/Canary/Rollback。

## 12. Assumptions

- MySQL 继续作为唯一业务主存储；MinIO、Celery 和 optional observability profile 的既有宪法约束保持有效。
- ReporterCredential 已能稳定关联 Runtime、User 和 Device；Foundation implementation 在其上增加 scope 和审计约束，不引入第二套身份体系。
- 外部 telemetry trace ID 可能晚于 Run 完成到达，因此 TraceBackendRef 采用最终一致性。
- 旧数据可能存在无法自动确定租户的记录，生产迁移必须预留人工修复窗口。
