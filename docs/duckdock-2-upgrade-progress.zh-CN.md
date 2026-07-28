# DuckDock 2.0 24 周升级总进度台账

> 文档状态：Active
>
> 计划周期：2026-07-20 ～ 2027-01-03（24 周，12 个双周 Sprint）
>
> 当前基线日期：2026-07-17
>
> 最后更新时间：2026-07-28
>
> 升级起点发布基线：`v0.1.0` / `33b4eec54b6ea9a44f612b886dfd6ce46c5870f6`
>
> 当前发布候选：`codex/agentloop-foundation-s1` / [PR #6](https://github.com/BaiKudan/DuckDock/pull/6)（G0 + S1-B + 现有 S1-C 实现；经受保护 `main` 合并）
>
> 2.0 已验收加权进度：`10.4%`（E00=`100%`；E01=`30%`，已完成兼容基线、nullable Namespace expand 与确定性 Tenant Resolver 三个验收切片；S1 进行中）

## 1. 唯一事实源（SSOT）

本文件是 DuckDock 2.0 的**唯一执行状态事实源**，统一记录：

- 24 周范围、里程碑、Epic 和 Sprint 状态；
- Gate 是否通过及其可复核证据；
- 风险、阻塞、关键决策和计划变更；
- 当前基线、实际进度、负责人和下一步。

维护规则：

1. 其他 PRD、ADR、Spec 和 Issue 可以描述需求或设计，但不得单独宣称项目状态；状态变化必须回写本文件。
2. “完成”只依据已合并代码、测试结果、迁移/运行记录和 Gate 证据，不依据会议结论、代码已写但未合并或主观百分比。
3. 每个状态变化必须填写 `证据`，至少关联 commit、PR、测试日志、演练记录或已批准 ADR 之一。
4. 每个 Sprint 结束时更新一次；发生范围、架构、时间或 Gate 变化时即时更新。
5. 若本文件与代码/自动化测试冲突，以代码和可重复测试结果为准，并在一个工作日内修正本文件。
6. Epic 百分比按已通过验收的任务权重计算；没有验收证据的任务按 `0%` 计。

状态枚举：

| 状态 | 含义 |
|---|---|
| 未开始 | 尚无已验收交付物 |
| 进行中 | 已开始实施，但尚未通过对应 Gate |
| 阻塞 | 无法继续推进，必须登记风险或决策项 |
| 待验收 | 实施完成，等待 Gate 证据或签字 |
| 已完成 | 验收标准全部满足且证据已登记 |
| 已取消 | 经决策日志批准后从范围移除 |

## 2. DuckDock 2.0 目标与边界

### 2.1 目标定位

DuckDock 2.0 从“企业 Agent 资产、工作记录与交接治理平台”升级为：

> **跨 Agent Runtime 的 Evidence + Eval + Release + Handover Control Plane**

目标闭环：

```text
Runtime 执行
  → 可信证据采集
  → Run 治理索引 + 外部 Span/ModelCall/ToolCall 引用 + Artifact 归档
  → 可复现评测与版本对比
  → 策略和人工发布门禁
  → 分环境晋级、Canary、回滚
  → 基于证据的资产与人员交接
```

### 2.2 本周期非目标

- 不自研通用 Agent Runtime、多 Agent 编排器或 Agent IDE。
- 不复制 AgentLoop/OpenClaw/WorkBuddy 的开发与执行能力。
- 不默认采集完整对话、个人文件或原始记忆正文。
- 不在缺少真实 Provider 执行、lease、审批和回执时开放自治操作。
- 不在本周期建设公有云多租户 SaaS；继续以“一企业一实例”的私有化控制面为主。
- 不为追求新架构一次性替换所有现有组件；优先稳定协议和兼容迁移。

## 3. 当前基线（2026-07-17）

### 3.1 已有可复用能力

| 能力 | 当前实现 | 2.0 处理 |
|---|---|---|
| Skill Registry | Git tag/SHA、MinIO 制品、包校验、公开分发 | 扩展为 Agent Package Registry v2 |
| 发布门禁 | Scanner、Sandbox、Clinic、人工审核、Namespace policy | 泛化为 Agent Release Policy Engine |
| Reporter | Structured Report 与 Pack 两条 Push-only 通路 | 演进为 Evidence Collector SDK |
| 分析 Worker | token、lease、heartbeat、attempt、标准结果文件、物化 | 演进为 Evidence Materializer + Eval Worker |
| 资产治理 | AIAsset、Ownership、RuntimeBinding、WorkTrace、Evidence | 强类型化为 Package/Deployment/AgentRun/TraceRef/Artifact/Attestation |
| 交接 | 建议、审批、人工回执、敏感证据门禁、验收、交接包 | 升级为 evidence-driven handover |
| IAM | SYSTEM/ORG/NAMESPACE RBAC、OIDC/LDAP、identity link | 完成真实 IdP、SCIM、service/device identity |
| 安全 | Fernet 凭证、敏感过滤、签名 URL、请求关联 ID | 增加签名证据、审计 outbox、不可变归档 |
| 部署 | 单机 Compose、健康检查、SOPS/age、备份/恢复脚本 | 补可观测性、真实恢复演练和 HA 路线 |

### 3.2 已确认差距

- Runtime capability 仍有硬编码和“无真实握手却返回可用”的实现。
- OpenClaw 等 Provider 主要依赖 Push/backup/sample metadata，不是实时双向 Adapter。
- 缺少标准 Run、Span、ModelCall、ToolCall、PolicyDecision 和 Provenance 模型。
- Clinic 是 Skill 静态质量评估，不是数据集/轨迹/生产指标 Eval Hub。
- 缺少 Agent Package、Environment、ReleaseCandidate、Promotion、Canary 和 Rollback 一等模型。
- 自动交接执行仍为 `501`，当前闭环依赖人工回执。
- 审计写入允许静默失败，且默认保留期后物理删除，不满足不可抵赖审计。
- 缺少 Prometheus、OpenTelemetry、统一告警、模型成本和轨迹观测。
- 当前仍为 Alpha，尚无独立安全审计和真实 backup→restore 演练记录。

### 3.3 基线规模

| 指标 | 当前值 |
|---|---:|
| API Endpoint 路由模块 | 18（另有 1 个聚合 router.py） |
| Endpoint decorators | 202 |
| SQLAlchemy 业务表 | 57 |
| Alembic revisions | 26 |
| 后端测试文件 / test functions | 56 / 503 |
| 前端单元测试文件 / Playwright 文件 | 5 / 2 |
| mypy baseline ceiling | 82 |
| 生产部署形态 | 单机 Docker Compose |

## 4. 24 周里程碑与 Sprint 路线

| Sprint | 周期 | 主题 | 关键交付 | 里程碑/Gate | 状态 |
|---|---|---|---|---|---|
| S0 | W1–W2，07-20～08-02 | 架构冻结与契约 | 2.0 边界、ADR、Evidence/Eval/Package/Release schema、迁移方案 | M0 / G0 | 已完成 |
| S1 | W3–W4，08-03～08-16 | Domain Foundation | Namespace 直接归属、不可变 Deployment、Transactional Outbox | M1a | 进行中 |
| S2 | W5–W6，08-17～08-30 | Run Index Alpha | AgentSession/AgentRun、Reporter envelope、TraceRef、旧模型兼容 | M1 / G1 | 未开始 |
| S3 | W7–W8，08-31～09-13 | OpenClaw Shadow Telemetry | OTel Collector、OpenClaw Bridge、metadata-only、可信属性注入 | M2a | 未开始 |
| S4 | W9–W10，09-14～09-27 | Generic OTLP + Fleet | Generic OTLP、Pack/ATIF 兼容、动态 capability、心跳与漂移 | M2 / G2 | 未开始 |
| S5 | W11–W12，09-28～10-11 | Eval Hub 数据面 | Dataset/Case/Suite/Evaluator/EvalRun、版本与存储 | M3a | 未开始 |
| S6 | W13–W14，10-12～10-25 | Eval 执行与比较 | Eval worker、case result、baseline、comparison、regression | M3 / G3 | 未开始 |
| S7 | W15–W16，10-26～11-08 | Package v2 + Release Gate | Agent manifest、component graph、SBOM、Policy shadow | M4a | 未开始 |
| S8 | W17–W18，11-09～11-22 | 晋级/Canary/回滚 | Environment、Promotion、DeploymentReceipt、Canary、Rollback | M4 / G4 | 未开始 |
| S9 | W19–W20，11-23～12-06 | Handover 2.0 + Identity | 依赖图、readiness、义务清单、SCIM/设备注册策略 | M5 / G5 | 未开始 |
| S10 | W21–W22，12-07～12-20 | 安全、观测与迁移 | audit outbox、OTel/Prometheus、v1/v2 对账、恢复预演 | M6a | 未开始 |
| S11 | W23–W24，12-21～01-03 | GA 候选验收 | 三 Runtime 闭环、负载/故障测试、恢复演练、文档与发布 | M6 / G6 | 未开始 |

里程碑定义：

| 里程碑 | 目标日期 | 结果定义 | 状态 |
|---|---|---|---|
| M0 Architecture Freeze | 2026-08-02 | 2.0 协议、边界、数据模型和迁移 ADR 获批 | 已完成 |
| M1 Evidence Alpha | 2026-08-30 | 可信 Run envelope、版本索引、Outbox 和 TraceRef 闭环 | 未开始 |
| M2 Runtime Beta | 2026-09-27 | OpenClaw、Generic OTLP、Pack/ATIF 三条接入路径与 Fleet 状态可用 | 未开始 |
| M3 Eval Beta | 2026-10-25 | 可复现 Eval、版本比较和回归判定可用 | 未开始 |
| M4 Release RC | 2026-11-22 | 评测驱动的分环境晋级、Canary 和回滚可用 | 未开始 |
| M5 Handover RC | 2026-12-06 | 基于运行证据的交接和身份联动闭环 | 未开始 |
| M6 2.0 GA Candidate | 2027-01-03 | 安全、SLO、灾备和三 Runtime 端到端 Gate 通过 | 未开始 |

## 5. Epic 总台账

| Epic | 能力域 | 目标 | 周期 | 依赖 | Owner | 状态 | 进度 | 验收证据 |
|---|---|---|---|---|---|---|---:|---|
| E00 | 产品/架构契约 | 冻结 2.0 边界、schema、API 和迁移策略 | W1–W2 | 无 | Architecture Owner | 已完成 | 100% | [G0 review](../specs/008-duckdock-2-foundation/gate-g0-review.md)、[Constitution](../.specify/memory/constitution.md)、[ADR](adr/README.md)、27 项 focused tests、Owner 批准 |
| E01 | Evidence Fabric | Namespace/Deployment/Run 索引、可信 envelope、TraceRef 和 Outbox | W3–W6 | E00 | Backend Architecture | 进行中 | 30% | 兼容基线；0027/0028 expand；resolver/backfill；S1-C 强制 Namespace 新写与 typed Evidence link；contract/non-null 仍后置 |
| E02 | Runtime Adapter | OpenClaw、Generic OTLP、Pack/ATIF 兼容与 conformance | W7–W10 | E00、E01 | Integration/Observability | 未开始 | 0% | — |
| E03 | Eval Hub | Dataset、Suite、Evaluator、EvalRun、Comparison、Regression | W11–W14 | E01 | Eval Architecture | 未开始 | 0% | — |
| E04 | Package Registry v2 | Agent manifest、component graph、SBOM、签名与 provenance | W15–W16 | E00、E01 | Registry Architecture | 未开始 | 0% | — |
| E05 | Release Control | Policy、审批、Environment、Promotion、Canary、Rollback | W15–W18 | E03、E04 | Release Architecture | 未开始 | 0% | — |
| E06 | Handover 2.0 | evidence snapshot、依赖图、readiness、义务与签名交接包 | W19–W20 | E01、E05 | Handover Domain | 未开始 | 0% | — |
| E07 | Identity/Security | SCIM、service/device identity、audit outbox、签名轮换 | W19–W22 | E01 | Security/IAM | 未开始 | 0% | — |
| E08 | Observability/Ops | OTel、Prometheus、告警、SLO、真实恢复演练 | W21–W24 | E01–E07 | Platform/Ops | 未开始 | 0% | — |
| E09 | API/UI/Migration | `/api/v2`、v1 兼容、双写对账、新控制台信息架构 | W1–W24 | 全部 | API/UI/Migration | 未开始 | 0% | — |

总体进度计算：

```text
总体进度 = Σ（Epic 权重 × Epic 已验收任务权重）
```

初始 Epic 权重：E00 5%、E01 18%、E02 12%、E03 15%、E04 8%、E05 15%、E06 8%、E07 7%、E08 7%、E09 5%。权重变更必须登记决策日志。

S0 的 14 个 P0 任务各计 1 个验收点。Product/Architecture Owner 于 2026-07-17 明确“批准 G0”，因此 E00 已验收进度为 `14/14=100%`。E01 使用下表 10 个等权验收切片；当前 EF-01、EF-02、EF-03 完成，所以 E01=`3/10=30%`。总体为 `5% × 100% + 18% × 30% = 10.4%`。

E01 验收切片：

| Slice | 结果定义 | 状态 | 证据 |
|---|---|---|---|
| EF-01 | WorkTrace、Release Gate、旧租户路径与架构边界兼容基线 | 已完成 | `backend/tests/foundation/` 4 个兼容测试文件，15 项通过 |
| EF-02 | 五表 nullable/indexed Namespace FK expand，可在 populated MySQL 安全升级 | 已完成 | revision 0027；8 项 migration tests；5 项 MySQL lane；`upgrade head` + `alembic check` |
| EF-03 | 确定性 Tenant Resolver 与 unresolved/conflict 分类 | 已完成 | `tenant_resolution_service.py`；17 项 resolver 测试；完整排序候选与禁止弱推断 |
| EF-04 | Backfill/audit、Namespace 强制新写与 contract preflight | 进行中 | FND-012/013/017/018 已完成；ADR-0211 typed link 与 0028 expand 已落地；FND-014/019 contract preflight/non-null 继续后置，因此本切片仍不计完成 |
| EF-05 | 不可变 AgentDeployment/Component inventory | 未开始 | — |
| EF-06 | AgentSession/AgentRun metadata-only 生命周期 | 未开始 | — |
| EF-07 | Reporter API v2、credential-derived identity 与幂等冲突 | 未开始 | — |
| EF-08 | AgentRunArtifact、TraceBackendRef 与 provider-neutral ports | 未开始 | — |
| EF-09 | Transactional Outbox、dispatcher、retry/DLQ/replay | 未开始 | — |
| EF-10 | v1/v2 对账、完整回滚演练、G1 Evidence Alpha 证据包 | 未开始 | — |

## 6. Sprint 0 任务清单（W1–W2）

Sprint 0 目标：在不大规模写业务实现前，冻结 2.0 产品边界、核心契约、数据归属、兼容策略和可执行验收计划。

计分规则：S0-01～S0-14 各 1 点；“待验收”表示交付物已就绪但不计完成分，“已完成”必须有批准记录或对应 Gate 证据。

| ID | 任务 | 交付物 | Owner | 优先级 | 状态 | 依赖 | 验收/证据 |
|---|---|---|---|---|---|---|---|
| S0-01 | 批准 2.0 产品定位与非目标 | Product Charter | Product/Architecture | P0 | 已完成 | 无 | [Constitution](../.specify/memory/constitution.md)、[008 spec](../specs/008-duckdock-2-foundation/spec.md)、Owner G0 批准 |
| S0-02 | 盘点并标注 v1 API/表/任务的保留、迁移、废弃 | v1→v2 inventory matrix | Backend Architecture | P0 | 已完成 | S0-01 | [Inventory](../specs/008-duckdock-2-foundation/v1-v2-inventory.md)：19 路由相关模块、202 decorators、57 表、8 Celery task、2 Beat、5 Worker/dispatch seam |
| S0-03 | 定义 Agent Evidence Protocol v1 | JSON Schema、事件枚举、示例、兼容规则 | Protocol Architecture | P0 | 已完成 | S0-01 | [telemetry-envelope-v1](../specs/008-duckdock-2-foundation/contracts/telemetry-envelope-v1.schema.json) + tests |
| S0-04 | 定义 Runtime/Agent/Run/TraceRef/Artifact 强类型模型 | ERD 与字段字典 | Domain Architecture | P0 | 已完成 | S0-03 | [data-model.md](../specs/008-duckdock-2-foundation/data-model.md) |
| S0-05 | 定义 Agent Package Manifest v2 | Schema、component graph、hash/signature 规则 | Protocol Architecture | P0 | 已完成 | S0-01 | [agent-package-v2](../specs/008-duckdock-2-foundation/contracts/agent-package-v2.schema.json) + tests |
| S0-06 | 定义 Eval Dataset/Suite/Run/Result v1 | Schema、Evaluator 契约、可复现要求 | Eval Architecture | P0 | 已完成 | S0-04 | [evaluation-result-v1](../specs/008-duckdock-2-foundation/contracts/evaluation-result-v1.schema.json) + Golden example |
| S0-07 | 定义 Release Manifest 与状态机 | Environment、Gate、Promotion、Receipt、Rollback | Release Architecture | P0 | 已完成 | S0-05、S0-06 | [release-manifest-v1](../specs/008-duckdock-2-foundation/contracts/release-manifest-v1.schema.json) + evidence-chain test |
| S0-08 | 决定存储与事件架构 | MySQL/MinIO/Langfuse/Outbox ADR | Architecture | P0 | 已完成 | S0-03、S0-04 | [ADR-0201～0210](adr/README.md) |
| S0-09 | 定义 `/api/v2` 与 v1 兼容窗口 | API versioning/deprecation policy | API Architecture | P0 | 已完成 | S0-02、S0-03 | [OpenAPI v2](../specs/008-duckdock-2-foundation/contracts/openapi-v2.yaml) + 14 focused tests |
| S0-10 | 定义隐私、脱敏、签名和保留策略 | Data classification & retention spec | Security/Privacy | P0 | 已完成 | S0-03 | [Constitution](../.specify/memory/constitution.md)、[ADR-0207/0209](adr/README.md) |
| S0-11 | 建立 Adapter conformance 规范 | SDK interface、fixture、contract test plan | Integration Architecture | P0 | 已完成 | S0-03 | [Conformance v0.2](../specs/008-duckdock-2-foundation/adapter-conformance.md)：三通路 + AgentLoop/WorkBuddy feasibility mapping |
| S0-12 | 建立迁移、回滚和对账计划 | 双写、backfill、replay、reconciliation plan | Migration Architecture | P0 | 已完成 | S0-02、S0-08、S0-09 | [plan/research/quickstart](../specs/008-duckdock-2-foundation/plan.md) |
| S0-13 | 建立 24 周测试与环境计划 | CI、负载、故障、安全、真实 Runtime 环境清单 | Release Engineering | P0 | 已完成 | S0-03～S0-12 | [Implementation plan](../specs/008-duckdock-2-foundation/plan.md) + Gate matrix |
| S0-14 | 完成 G0 评审 | Gate 记录与未决项清单 | Product/Architecture Owner | P0 | 已完成 | 全部 S0 任务 | [G0 review](../specs/008-duckdock-2-foundation/gate-g0-review.md)；Owner 于 2026-07-17 批准 |

Sprint 0 退出条件：所有 P0 契约有版本号、Owner、兼容策略、测试样例和批准记录；不得以“后续边做边定”替代 G0。

### 6.1 Sprint 1 当前执行清单（W3–W4）

| ID | 对应任务 | 交付/验收 | 优先级 | 状态 | 证据 |
|---|---|---|---|---|---|
| S1-01 | FND-001 | WorkTrace/Structured Report 字段与首写幂等兼容 | P0 | 已完成 | `test_work_trace_compatibility.py`，与旧测试合跑通过 |
| S1-02 | FND-002 | Release Gate pass/fail/no-evaluation 兼容 | P0 | 已完成 | `test_release_gate_compatibility.py`，与旧测试合跑通过 |
| S1-03 | FND-003 | Runtime/Asset/Binding/Trace/Evidence 旧租户路径基线 | P0 | 已完成 | `test_tenant_path_compatibility.py`，5 项通过并登记现存缺口 |
| S1-04 | FND-004/005 | 禁止 raw execution MySQL 模型和核心 vendor SDK 耦合 | P0 | 已完成 | `test_architecture_boundaries.py`，5 项通过 |
| S1-05 | FND-015 | 五表 nullable/indexed Namespace FK 与 0027 expand revision | P0 | 已完成 | model + revision 0027；单元/静态测试通过 |
| S1-06 | FND-010 | empty/populated migration、deterministic/unresolved/conflict/CAS 真实 MySQL 夹具 | P0 | 已完成 | MySQL 8.4：8 项 migration file、10 项完整 marker lane、Alembic check |
| S1-07 | FND-011 | Tenant Resolver precedence/repeatability/unresolved/conflict 先红测试 | P0 | 已完成 | `test_tenant_resolution_service.py` 17 项；缺模块及四项安全边界均有真实红→绿记录 |
| S1-08 | FND-016/017 | Resolver 与幂等 backfill/audit command | P0 | 已完成 | Resolver、backfill service、显式安全 CLI、[runbook](../specs/008-duckdock-2-foundation/tenant-backfill-runbook.md)；31 项 focused tests + 5 项 S1-B MySQL fixtures |
| S1-09 | FND-012/013/018 | 强制 Namespace 新写、跨租户关系拒绝 | P0 | 已完成 | `tenant_write_service.py`、management/ingestion/materialization/handover 写路径、Foundation 与现有回归测试 |
| S1-10 | ADR-0211 / revision 0028 | Evidence typed WorkTrace link expand | P0 | 已完成 | nullable indexed FK、`ON DELETE SET NULL`、typed-only resolver/adapter；无 backfill/contract DDL |
| S1-11 | FND-014/019 | contract blocker preflight、non-null 与 tenant-scoped indexes | P0 | 未开始 | S1-C 明确后置；须先完成历史 typed-link remediation 与全量零 blocker 审计 |

## 7. Gate 标准

### G0：Architecture Freeze

- 2.0 产品边界与非目标获批。
- Agent Package v2、Telemetry Envelope v1、Evaluation Result v1、Release Manifest v1 均有 JSON Schema、示例和兼容规则。
- 核心 ERD、API v2、存储职责、隐私分类和迁移 ADR 获批。
- OpenClaw、Generic OTLP、ATIF/Pack 均完成字段映射；AgentLoop Connector 完成可行性映射，无关键不可表达对象。
- S1/S2 的测试、负载和回滚方案可执行。

### G1：Evidence Alpha

- Metadata-only Run envelope 可被接收、验证，并物化为 AgentRun、TraceRef 和 Artifact 索引；原始 Span 不进入 MySQL。
- `event_id` 与幂等键重放不产生重复数据；同键不同 payload 明确冲突。
- Run、Deployment、TraceBackendRef 和 artifact hash 可双向追溯。
- Outbox 具备可观测失败原因、重试、死信状态和管理员 replay 工具。
- Run 控制信封持续 100 req/s、短时 500 req/s；Collector 遥测另行执行 Span 吞吐压测。
- v1 Reporter 输入保持兼容，并通过新旧数据对账测试。

### G2：Runtime Beta

- OpenClaw、Generic OTLP、现有 Pack/ATIF 兼容三条路径均完成真实接入验证。
- OpenClaw/OTLP 可观测 Agent、Run、ModelCall、ToolCall 和 Artifact；批量通道保留完整度标识。
- 所有正式支持的 Adapter 通过同一 conformance suite；AgentLoop Connector 作为后续 Provider Adapter，不是 G2 硬依赖。
- capability 来自动态探测；Generic Adapter 不再无连接返回 `ok`。
- Fleet 页面展示心跳历史、版本、能力、collector 和配置漂移。

### G3：Eval Beta

- Dataset/Suite/Evaluator/Run/CaseResult 全部版本化。
- 同一 package、dataset 和 evaluator 配置可重复执行并追溯差异。
- 支持 deterministic、LLM Judge 和人工复核；Judge model/prompt/token/cost 全留痕。
- 支持版本 A/B、baseline、绝对阈值和相对回归阈值。
- Clinic 现有维度至少迁移为一个 Static Eval suite。
- 失败、超时、部分失败、重试和人工覆盖均有测试。

### G4：Release RC

- 可完成 DEV→STAGING→CANARY→PRODUCTION 晋级。
- Eval 回归、权限扩大、Tool 增加和高风险漏洞可触发策略门禁。
- Policy 支持 shadow/warn/enforce，所有决策可解释到规则、版本和证据。
- Runtime 必须返回实际部署版本和结果回执。
- Canary 失败可在 5 分钟内完成回滚动作、回执和审计。
- Promotion、Rollback 和重试具备幂等测试。

### G5：Handover RC

- 交接可自动生成资产依赖图、生产版本、最近运行、Eval baseline、风险、权限和 runbook。
- 所有生产 Agent 均有 owner、receiver 或 fallback owner。
- 高关键/敏感交接项继续执行证据门禁。
- 接收人权限检查、失败确认、验收和签名交接包闭环通过 E2E。
- 至少一个真实 IdP/SCIM 或等价 staging directory 流程完成禁用用户→撤销凭证→触发交接。

### G6：2.0 GA Candidate

- OpenClaw 真实 Runtime、Generic OTLP Fixture 和 Pack/ATIF 兼容路径完成 Evidence→Eval→Release→Rollback/Handover 端到端场景。
- API、worker 和关键 workflow 故障恢复测试通过。
- Evidence ingest accepted p95 <200ms；30 天 Run 时间线 p95 <2s；Policy decision p95 <100ms。
- Prometheus/OTel、关键 dashboard 和告警就绪。
- 无未处理 Critical 安全问题；High 问题必须有批准的期限化豁免。
- 完成真实 MySQL、对象存储、Git/manifest 备份→恢复演练，目标 RPO ≤15 分钟、RTO ≤4 小时。
- v1/v2 对账无未解释差异，弃用清单和至少两个版本的兼容承诺已发布。
- 运维手册、安全手册、Adapter SDK、API 和迁移文档完成。

### 所有 Gate 共用 Definition of Done

- 行为变更先有失败测试，再提交实现；新增功能包含单元、集成和必要的 E2E。
- MySQL 真实 lane、Alembic `upgrade head`/`check`、后端 lint/type/test 和前端 lint/test/build 全绿。
- 新增迁移具备上一正式版升级测试、forward-fix 和应用回滚兼容方案；不得把破坏性数据库 downgrade 作为唯一回滚手段。
- 所有外部动作具备 idempotency key、最小权限、超时、重试边界和 receipt。
- AI 路径必须暴露 model/prompt/mode；降级不得伪装为 LLM 成功。
- 敏感数据默认最小采集、边缘脱敏、传输加密、权限过滤和访问审计。
- 文档、OpenAPI、schema 示例和本台账同步更新。

## 8. 北极星指标与每 Sprint 报告项

| 指标 | 当前基线 | W24 目标 |
|---|---:|---:|
| 生产 Agent Evidence 覆盖率 | 0%（无标准 Run evidence） | ≥90% 试点范围 |
| 具备可验证 provenance 的发布占比 | 0% | 100% 2.0 发布 |
| 经过 EvalGate 的生产发布占比 | 0% | 100% 2.0 发布 |
| 正式支持的接入路径 | 0 个完整执行级路径 | OpenClaw、Generic OTLP、Pack/ATIF 兼容 |
| 关键 Agent owner/fallback owner 覆盖率 | 待盘点 | 100% 试点范围 |
| Release rollback 用时 | 无统一流程 | ≤5 分钟 |
| Evidence ingest accepted p95 | 无基线 | <200ms |
| 30 天 Run 时间线 p95 | 无基线 | <2s |
| 未解释的 v1/v2 对账差异 | 不适用 | 0 |
| 真实恢复演练 | 未完成 | 1 次完整通过 |

每个 Sprint 必须记录：计划完成率、已通过验收的任务、Gate 变化、未解决阻塞、缺陷趋势、性能/容量数据、下 Sprint 目标。

## 9. 风险台账

### 9.1 当前风险

| ID | 风险 | 概率 | 影响 | 触发信号 | 缓解措施 | Owner | 状态 | 复审日期 |
|---|---|---|---|---|---|---|---|---|
| R-001 | 同时建设 Evidence、Eval、Release、Handover 导致范围失控 | 高 | 高 | Sprint 连续两期完成率 <70% | 坚守非目标；Gate 切片；禁止临时加入 IDE/Runtime 功能 | Product Owner | 开放 | 2026-08-02 |
| R-002 | 错把原始 Span/内容复制进 MySQL | 中 | 高 | 出现 Span/Prompt/Tool I/O 业务表或写延迟异常 | 架构守卫；MySQL 只存 Run 索引；Langfuse/MinIO 承载详情 | Data Architecture | 开放 | 2026-08-02 |
| R-003 | Provider API 不稳定使 Adapter 难以长期维护 | 高 | 高 | conformance 持续失败或 API 频繁漂移 | SDK/contract tests；支持等级；版本兼容矩阵 | Integration Architecture | 开放 | 2026-09-13 |
| R-004 | 采集范围扩大造成隐私和合规风险 | 中 | 高 | 出现原始对话、凭证或个人数据 | allowlist、边缘脱敏、签名摘要、敏感分级和审计 | Security/Privacy | 开放 | 2026-08-02 |
| R-005 | v1/v2 双写不一致导致资产和交接结论错误 | 中 | 高 | 对账出现未解释差异 | outbox、稳定映射 ID、replay、每日 reconciliation | Migration Architecture | 开放 | 2026-08-30 |
| R-006 | Eval/Policy 误报阻断生产 | 中 | 高 | shadow 模式高误报或频繁人工豁免 | shadow→warn→enforce；版本化阈值；期限化 exception | Eval/Policy Owner | 开放 | 2026-11-08 |
| R-007 | 一次性引入 Event Bus/高容量库/Workflow 引擎增加运维负担 | 中 | 中 | 环境搭建阻塞业务交付 | 契约先行、组件后置、基准达到阈值再引入 | Platform/Ops | 开放 | 2026-08-02 |
| R-008 | 现有审计静默失败和保留删除削弱证据可信度 | 中 | 高 | 审计缺口或无法证明连续性 | transactional outbox、不可变归档、连续性校验 | Security/Ops | 开放 | 2026-12-20 |
| R-009 | 历史 EvidenceItem 可能缺少可信 typed WorkTrace 关系，NULL tenant 无法解析 | 中 | 高 | 全量审计仍出现无 `work_trace_id` 或目标 unresolved/conflict 的 Evidence | ADR-0211/0028 已提供 typed FK；只从可信 lineage 补链并重跑审计；继续禁止 CollectionJob/Membership/URI/JSON 猜测 | Data Architecture | 缓解中 | 2026-08-09 |

### 9.2 风险模板

| ID | 风险 | 概率 | 影响 | 触发信号 | 缓解措施 | Owner | 状态 | 复审日期 |
|---|---|---|---|---|---|---|---|---|
| R-XXX | 描述 | 低/中/高 | 低/中/高 | 可观测触发条件 | 预防与应对 | 姓名/团队 | 开放/缓解中/关闭 | YYYY-MM-DD |

风险关闭必须记录实际结果和剩余风险，不得只删除行。

## 10. 决策日志

### 10.1 已建立决策

| ID | 日期 | 决策 | 原因 | 影响 | Owner | 状态 | 关联证据 |
|---|---|---|---|---|---|---|---|
| D-001 | 2026-07-17 | 2.0 定位为 Evidence/Eval/Release/Handover Control Plane，不建设通用 Runtime/IDE | 避免与 AgentLoop/OpenClaw 重复并聚焦企业治理优势 | 所有 Epic 必须服务控制面闭环 | Architecture | 已批准 | Constitution 2.0 |
| D-002 | 2026-07-17 | 本周期继续一企业一实例 | 降低多租户 SaaS 和计费复杂度，同时保留 Namespace 隔离 | 不建设 SaaS tenant billing；新 v2 实体仍直接绑定 Namespace | Architecture | 已批准 | Constitution 2.0、008 spec |
| D-003 | 2026-07-17 | `/api/v2` 与 v1 双轨迁移，兼容窗口不少于两个正式版本 | 避免破坏现有 Reporter、Worker 和控制台 | 必须建设双写、replay 和对账 | Architecture | 已批准 | Constitution 2.0、008 spec |
| D-004 | 2026-07-17 | 默认不采集原始对话和个人记忆正文 | 最小暴露和企业隐私要求 | Contract 以索引、摘要、hash、artifact ref 为主 | Architecture | 已批准 | ADR-0209 |
| D-005 | 2026-07-17 | Provider 自动动作在真实执行、审批、lease、幂等和 receipt 完成前保持关闭 | 防止把治理控制面变成不可审计的自治执行器 | 现有 `AUTO 501` 不作为产品能力宣传 | Architecture | 已批准 | Constitution 2.0 |
| D-006 | 2026-07-17 | 新基础设施按容量和恢复需求分阶段引入 | 控制 24 周交付与运维风险 | S0 不引入 Kafka/Temporal/第二套 Trace 平台 | Architecture | 已批准 | ADR-0210、R-007 |
| D-007 | 2026-07-17 | WorkTrace 与 AgentRun 分离，MySQL 不保存原始 Span | 保留企业工作语义并避免高吞吐遥测侵入主库 | 新增 AgentRun/TraceRef，详情留在 Provider | Architecture | 已批准 | ADR-0201/0202 |
| D-008 | 2026-07-17 | S0 只完成 G0 架构冻结；008 Foundation 实现在 S1-S2 启动 | 遵守分批放行和 P0 硬门禁 | G0 前不执行业务模型和迁移改造 | Architecture | 已批准 | 008 spec/plan |
| D-009 | 2026-07-17 | 信任拆为 `trust_level` 保证强度与 `trust_source` 入口来源 | 避免把 bearer/mTLS/checksum 误称为生产者证明 | 只有 AttestationVerifier 成功才能得到 PRODUCER_ATTESTED | Security Architecture | 已批准 | ADR-0207、OpenAPI v2 |
| D-010 | 2026-07-17 | AgentLoop 是可选 Provider connector；WorkBuddy 等 Harness 复用三类公共接入 profile | 保持核心供应商中立，避免每个产品产生一套 Run/身份模型 | G0 只冻结可行性映射，connector 实现在 G2 后按 conformance 认证 | Integration Architecture | 已批准 | adapter-conformance v0.2 |
| D-011 | 2026-07-17 | 批准 G0，并只放行 G0 Review 第 6 节定义的 S1 首批 | 架构与契约已通过技术复核，进入测试先行的兼容迁移阶段 | S0/E00/M0 完成；S1/E01 启动；暂不授权 contract/non-null、自动生产 Gate 或破坏性 v1 移除 | Product/Architecture Owner | 已批准 | G0 review；Owner 明确回复“批准 G0” |
| D-012 | 2026-07-17 | 0027 只做 nullable Namespace expand；不回填、不推断、不设 non-null | 先保持 v1 写入/读取兼容，再以可审计 resolver 处理历史归属 | 五个旧实体增加可空直接归属；downgrade 检测到任一已赋值记录即拒绝删除 | Backend Architecture | 已批准实施 | data-model、plan、revision 0027、MySQL migration tests |
| D-013 | 2026-07-19 | 批准 S1-B，仅放行 FND-011/016/017 | 在 non-null contract 前先建立确定性、可审计、可重复的历史租户归属处理能力 | Resolver 与 backfill/audit 可实施；FND-012/013/014/018/019 继续禁止提前执行 | Product/Architecture Owner | 已批准 | Owner 明确回复“批准 S1-B”；G0 Review §9 |
| D-014 | 2026-07-19 | S1-B 定位为 NULL tenant 回填与 blocker inventory，不是最终全表租户一致性审计 | 同时满足 direct rerun 优先级与跨关系 invariant 的分阶段迁移边界；当前 Evidence 又无 typed WorkTrace FK | 非空 direct 值在本批不覆盖；FND-013/019 必须复核全表关系；Evidence 缺口保持 fail-closed；apply 必须确认关系写静默窗口 | Backend/Data Architecture | 已实施 | 17 项 resolver、14 项 backfill/CLI、5 项 S1-B MySQL fixtures、tenant backfill runbook |
| D-015 | 2026-07-20 | 形成 S1-C 实现候选：FND-012/013/018 与可空 Evidence→WorkTrace typed link；FND-014/019 后置 | 先关闭新增脏数据和跨租户关系入口，再为历史 Evidence 提供确定性 lineage；避免提前 contract 锁死未修复数据 | 新写必须指向 active/authorized Namespace；typed link expand-only；历史无 link 继续 unresolved，contract/non-null 不变 | Backend/Data Architecture | 已实施；发布授权见 D-016 | ADR-0211；revision 0028；tenant write tests |
| D-016 | 2026-07-28 | 将当前已完成的 G0、S1-B 与 S1-C 实现候选通过功能分支、PR 和受保护 `main` 完整发布并同步本地 | Owner 要求把现有成果形成完整远程 GitHub 合并提交并保持本地远端最新 | 这是现有成果的发布授权，不是对 2026-07-20 实施的追溯批准，也不放行 FND-014/019 或其他延期范围 | Product/Architecture Owner | 已批准发布 | Owner 2026-07-28 发布指令；G0 Review §11；PR #6 |

### 10.2 决策模板

| ID | 日期 | 决策 | 备选方案 | 原因 | 影响 | Owner | 状态 | 关联证据 |
|---|---|---|---|---|---|---|---|---|
| D-XXX | YYYY-MM-DD | 决策内容 | A/B/C | 选择理由 | 范围、进度、兼容、安全影响 | 姓名/团队 | 提议/批准/废弃 | ADR/PR/会议记录 |

影响产品边界、核心 schema、存储、兼容期、Gate 或里程碑的决策必须进入本表。

## 11. 变更日志

### 11.1 计划变更模板

| 变更 ID | 日期 | 提出人 | 原计划 | 新计划 | 原因 | 对范围/日期/Gate 的影响 | 批准人 | 状态 |
|---|---|---|---|---|---|---|---|---|
| C-XXX | YYYY-MM-DD | 姓名/团队 | 原内容 | 新内容 | 证据和背景 | 量化影响 | 姓名 | 提议/批准/拒绝/实施 |

变更控制规则：

- P0 范围、里程碑日期或 Gate 降级必须有批准的变更记录。
- 不得通过修改验收文案掩盖未完成工作。
- 延期必须同步风险、依赖、Owner 和新的 Gate 日期。
- 已批准变更实施后，必须同步更新里程碑、Epic 和 Sprint 表。

### 11.2 文档修订记录

| 日期 | 版本 | 变更 | 作者 | 证据 |
|---|---|---|---|---|
| 2026-07-17 | 0.1 | 创建 DuckDock 2.0 24 周唯一进度台账；登记基线、路线、Epic、Sprint 0、Gate 和初始风险/决策 | Codex | 本次文件变更 |
| 2026-07-17 | 0.2 | 启动 S0；落地 Constitution 2.0、ADR-0201～0210、008 Foundation 规格和四类可校验契约 | Codex | 9 项 contract tests + Ruff |
| 2026-07-17 | 0.3 | 完成 v1→v2 API/表/任务 inventory、OpenAPI v2、Adapter conformance v0.2、G0 技术审查；修正 trust、metadata、waiver、EvaluationResult、Problem 与 Quickstart 契约 | Codex | 27 项 focused tests + Ruff + Draft 2020-12 validation |
| 2026-07-17 | 0.4 | Product/Architecture Owner 批准 G0；关闭 S0/E00/M0，启动受限 S1 首批 characterization 与 nullable Namespace expand 工作 | Codex | Owner 回复“批准 G0” + G0 sign-off |
| 2026-07-17 | 0.5 | 完成 S1 兼容基线与 0027 nullable Namespace expand；建立 E01 十切片计分；真实 MySQL populated upgrade、完整后端与 Alembic 门禁通过 | Codex | 644 backend passed、5 skipped；5 MySQL passed；`alembic check` clean；mypy 80≤82 |
| 2026-07-19 | 0.6 | Product/Architecture Owner 批准 S1-B；启动 FND-011/016/017，继续保持 contract/non-null 与强制新写边界关闭 | Codex | Owner 回复“批准 S1-B” + G0 Review §9 |
| 2026-07-19 | 0.7 | 完成 S1-B：确定性 NULL tenant resolver、批量 backfill/audit、安全 CLI 与 runbook；登记 Evidence typed-link blocker，保持 EF-04 与 contract 边界未完成 | Codex | 675 backend passed、10 skipped；10 MySQL passed；31 focused tests；Ruff/compile clean；mypy 80≤82 |
| 2026-07-20 | 0.8 | 形成 S1-C 实现候选：强制 Namespace 新写、拒绝跨租户 typed 关系；采用 ADR-0211 并增加 Evidence→WorkTrace expand-only typed link；FND-014/019 继续后置 | Codex | backend 685 passed；MySQL lane 10 skipped（未配置 TEST_MYSQL_URL）；frontend 21 passed + production build；Ruff/compile clean；mypy 80≤82；Alembic head=0028 |
| 2026-07-28 | 0.9 | 修正 S1-C 授权记录并完成发布加固：历史 NULL 资源 live path fail-closed；补齐 Handover、report token、heartbeat、feedback 与兼容 ingest 的 Namespace 写门禁；形成受保护 PR 发布候选 | Codex | PR #6；backend 701 passed、10 skipped，覆盖率 76.90%；MySQL 10 passed；Foundation/contracts 104 passed；frontend 21 passed + build；Ruff/compile/import clean；mypy 80≤82；Alembic 0028/check clean；Owner 发布授权 |

## 12. Sprint 更新模板

复制以下内容到本节末尾，并按 Sprint 追加，不覆盖历史记录。

```markdown
### Sprint SX（YYYY-MM-DD ～ YYYY-MM-DD）

- Sprint 目标：
- Owner：
- 状态：未开始 / 进行中 / 阻塞 / 待验收 / 已完成
- 计划任务权重：
- 已验收任务权重：
- 完成率：
- 对应 Epic：
- 对应 Gate：

已完成：

- [ ] TASK-ID — 结果；证据：PR/commit/test/runbook

未完成/移交：

- [ ] TASK-ID — 原因；新 Owner；新日期

质量与运行数据：

- 测试：
- 性能/容量：
- 安全：
- 迁移/对账：

阻塞与风险变化：

- R-XXX：

决策与变更：

- D-XXX / C-XXX：

下一 Sprint：

- 目标：
- Gate 前置条件：
```

## 13. 当前行动

截至 2026-07-28：

1. Product/Architecture Owner 已于 2026-07-17 批准 G0、2026-07-19 批准 S1-B，并于 2026-07-28 授权把当前已完成的 S1-C 实现候选随本发布包提交、PR 合并和同步；这不是对 2026-07-20 实施的追溯批准。S0、E00 与 M0 已完成；E01 完成 3/10 个验收切片，当前加权进度仍为 `10.4%`。
2. Constitution 2.0、ADR-0201～0211、008 规格包、全量 inventory、OpenAPI v2 和 Adapter conformance v0.2 已落地。
3. Agent Package v2、Telemetry Envelope v1、Evaluation Result v1、Release Manifest v1、OpenAPI 和 Quickstart 已由 27 项 focused tests 锁定。
4. [G0 Review](../specs/008-duckdock-2-foundation/gate-g0-review.md)已记录第 8 节首批证据与第 9～10 节 S1-B 授权/验收；`G0-F01`、`G0-F02` 作为实现条件继续跟踪。
5. S1-B 已完成：FND-010/011/016/017 关闭；五类 NULL tenant 按 Asset→Runtime→Binding→WorkTrace→Evidence 顺序确定性解析，歧义和缺证据保持 NULL。
6. 验证记录：backend `675 passed, 10 skipped`；MySQL marker lane `10 passed`（含真实 REPEATABLE READ CAS race）；空库 `upgrade head` 与 `alembic check` clean；Ruff/compile clean；mypy `80` 未超过 baseline `82`。
7. 运维入口为 `backend/scripts/backfill_foundation_tenants.py`；dry-run/apply checkpoint 按模式、数据库身份哈希和 Alembic revision 隔离；apply 必须显式确认关系写静默窗口。
8. S1-C 已完成 FND-012/013/018：Runtime/Reporter/Asset/Handover 管理 API 与现有 ingestion/materialization/package 服务要求 active Namespace，并拒绝 Binding/WorkTrace/Evidence 及交接关系跨 Namespace。
9. ADR-0211 与 revision 0028 已增加 nullable `EvidenceItem.work_trace_id` typed FK；resolver 仅沿 typed WorkTrace 解析。历史无可信 link 的 Evidence 继续 unresolved，禁止用 CollectionJob、Membership、URI 或 JSON 代替。
10. EF-04 仍为进行中且不计完成：FND-014/019 contract blocker preflight、non-null constraints 与 tenant-scoped indexes 继续后置；不删除或猜测 unresolved/conflict 历史记录。
11. 发布候选验证：backend `701 passed, 10 skipped`，受控模块覆盖率 `76.90%`；真实 MySQL 8.4 marker `10 passed`；Foundation/contracts `104 passed, 6 deselected`；frontend `21 passed`、lint 0 error 且 production build 通过；Ruff/compile/import clean；mypy `80≤82`；空 MySQL 升级至 `20260720_0028` 且 `alembic check` clean。
