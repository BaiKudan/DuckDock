# DuckDock Constitution

> DuckDock 2.0 企业 Agent 运行证据、评测、发布与交接控制面不可绕过的工程原则。
>
> 本宪法优先于零散设计文档和实现习惯。代码、规格与文档冲突时，以本宪法和当前已批准的 specs 为准。

## Core Principles

### I. 治理事实与遥测数据分离

- MySQL 8.x 是 DuckDock 唯一业务事实主库，保存身份、资产、版本、部署、责任、策略、审批、评测摘要、发布决策、审计与交接状态。
- 原始 Trace、Span、Prompt/Response、Tool I/O 和高吞吐 Token/Cost 数据不得复制到 MySQL 业务表。
- Langfuse/ClickHouse 是可替换的执行级遥测数据面，不是资产、身份、审批或最终发布决策的事实源。
- MinIO/S3 保存不可变制品、ATIF 轨迹、评测输入快照、详细结果和证据包。
- PostgreSQL 与 ClickHouse 只允许作为可选 observability profile 的依赖，不得承载 DuckDock 业务事实。

### II. 版本、部署与证据必须可复现

- 每次生产执行必须能够关联到确切的 AgentPackageVersion、Deployment revision 和组件 digest。
- AgentPackageVersion 必须锁定 Skill、Prompt、Tool/MCP、模型配置、Knowledge 引用、权限、策略和评测套件。
- DatasetVersion、EvaluatorVersion、Release Manifest 和 Gate Policy 一经用于正式决策即不可变。
- ReleaseGate 只能消费绑定当前候选版本的固定证据，不得使用“最新一次评测”替代准确关联。
- 所有正式证据必须记录 schema version、内容 hash、来源、生成器版本和敏感级别。

### III. 串行、幂等与可靠异步写入

- 同一资产或版本的并发发布必须串行化。
- Reporter、Run 控制信封、Artifact、Evaluation、Gate 和执行回执必须使用幂等键。
- 同一幂等键对应不同 payload hash 时必须显式冲突，禁止静默覆盖。
- 业务状态和待发布领域事件必须在同一 MySQL 事务中写入 Transactional Outbox。
- Celery 消费者必须按 event_id 幂等；部分失败必须进入明确状态或死信队列。
- 所有外部副作用必须具有 receipt、重试边界和人工可见的失败原因。

### IV. 测试先行，不可妥协

- 所有行为变更必须先添加失败回归测试，再实现修复或功能。
- Service、状态机、权限、租户隔离、幂等、迁移和 Provider Contract 必须进入自动化测试。
- 关键链路必须在真实 MySQL 8.x 上运行集成测试。
- OTel、ATIF、DuckDock JSON Schema 和 OpenAPI 必须有 Golden Contract 测试。
- 新 DuckDock 2.0 模块从第一天启用严格类型检查；不得继承 Legacy mypy 债务。
- Release、Gate、凭据、跨 Namespace 和敏感内容路径的覆盖率不得低于 90%。

### V. Collector 是遥测信任边界

- Runtime 不得自行决定可信 Namespace、Runtime、Credential 或 Deployment 身份。
- Collector 必须删除客户端提交的同名治理属性，并根据 mTLS、短期 JWT 或受控 API Key 注入可信值。
- 实时轨迹使用 OTLP；完整可移植轨迹使用 ATIF。两者互补，不互相替代。
- OTel GenAI/OpenInference 字段必须经过版本化 Normalizer，不得直接固化为长期数据库 Schema。
- 遥测故障不得阻断 Agent 主业务；正式发布缺少强制证据时必须 Fail-closed。

### VI. 最小暴露与内容默认关闭

- 默认 Content Policy 为 metadata_only。
- Prompt、Response、Tool 参数、检索文档和文件内容只能在 Namespace 明确授权后采集。
- 隐藏 Chain-of-Thought、凭据、Secret 和个人私域数据不得采集。
- 脱敏必须在进入 Langfuse、对象存储或业务服务之前完成。
- 敏感内容访问必须具备权限、理由、审批和审计。
- 保留期删除必须覆盖 MySQL 引用、遥测后端和对象存储，并保留删除回执。

### VII. LLM 只能提供建议，人类保留最终权限

- LLM Judge 必须记录模型、Prompt、参数、版本、Token、成本和校准结果。
- 未经人工 Golden Set 校准的 Judge 不得成为生产强制 Gate 的唯一依据。
- 自动优化只能产生候选版本，不能直接修改或发布生产 Agent。
- 高风险发布、权限扩大、凭据轮换、资产转移和交接必须保留人工审批或显式紧急豁免。
- 豁免必须记录原因、审批人、影响范围和到期时间。

### VIII. Provider 可替换，领域边界稳定

- DuckDock 核心领域不得依赖 Langfuse、DeepEval、AgentLoop 或其他 Provider 的 ORM 与内部表。
- Telemetry、Evaluation、Trajectory、Attestation 和 Gate Evidence 必须通过 Port/Adapter 接口接入。
- WorkTrace 是企业工作聚合；AgentRun 是单次执行事实，两者只能关联，不得混为同一实体。
- Clinic 评价静态资产质量；Runtime Evaluation 评价真实执行质量，二者语义必须分离。
- 继续采用模块化单体，除非容量、故障隔离或团队边界提供了可量化的拆服务收益。

### IX. 分批放行与可回滚演进

- P0 是硬门禁，未完成契约、可信采集、隔离、幂等和迁移验收前不得进入 P1 强制 Gate。
- 数据迁移采用 Expand → Backfill → Dual Read/Write → Reconcile → Cutover → Contract。
- API v1 与 v2 至少并存两个正式版本；删除前必须有使用量和迁移证据。
- 数据库迁移不得在发布事务中执行不可控全表回填。
- 应用回滚不得依赖破坏性数据库 downgrade；新表和兼容字段应允许旧应用安全运行。
- 所有高风险能力使用 off → observe → warn → enforce 灰度。

## Product Boundaries

DuckDock 2.0 负责：

- Agent Package、组件版本、部署和企业责任关系。
- Runtime 身份、运行证据索引与可信程度。
- Dataset 治理、评测配置、结果摘要与证据。
- ReleaseGate、审批、Canary、回滚和豁免。
- IAM、审计、风险、证据与人员/项目交接。

DuckDock 2.0 不负责：

- 通用 Agent IDE 或可视化编排器。
- 自研通用 Agent Runtime 或模型网关。
- 复制 Langfuse 的 Trace 存储与完整 UI。
- 默认桌面监控、eBPF 全量监控或个人私域数据采集。
- 未经审批的自治生产变更。

## Mandatory Quality Gates

每个 Pull Request 必须满足：

1. Ruff、mypy ratchet 和编译通过。
2. 后端单元、真实 MySQL 集成和迁移测试通过。
3. 前端 lint、测试和构建通过。
4. 新增契约通过 Schema/Golden 示例校验。
5. 行为变更具有先红后绿的回归测试。
6. 新外部依赖完成许可证、安全和版本固定检查。

每个 Release Candidate 额外满足：

1. 从上一正式版本升级成功。
2. 完整 Agent → Trace → Dataset → Eval → Gate → Approval E2E 通过。
3. Feature Flag 关闭和回滚演练通过。
4. SBOM、镜像扫描、签名和 Evidence Pack 完成。
5. Staging 运行、备份恢复和 SLO 验证完成。

## Governance

- 新能力必须先进入 specs/<NNN>-<slug>/，至少包含 spec、plan、tasks、data-model、contracts 和 quickstart。
- 影响跨领域边界的决定必须记录 ADR。
- 每个里程碑结束前必须更新 DuckDock 2.0 总进度台账，并执行规格、代码、迁移和文档一致性检查。
- 修改本宪法必须包含新版本号、日期、原因和迁移影响。
- 合理偏离必须写入对应 plan.md 的 Complexity Tracking，并说明更简单方案为何不可行。

## Amendment Record

- v1.0.0，2026-06-08：建立 MySQL、幂等、测试、审计和最小暴露原则。
- v1.1.0，2026-06-12：明确 Push-only Reporter 与现有质量门禁。
- v2.0.0，2026-07-17：建立 DuckDock 2.0 运行证据、评测、发布保障、Provider 边界、可信采集和分批迁移原则。

**Version**: 2.0.0

**Ratified**: 2026-06-08

**Last Amended**: 2026-07-17
