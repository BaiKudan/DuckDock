# DuckDock 2.0 升级工程记录（历史）

> 文档状态：Historical Engineering Record
>
> 本文保留开发阶段的计划、测试和一次性验收记录，不再作为当前发布状态、生产就绪性或部署授权的事实源。当前能力以代码和 API 契约为准；目标环境是否可上线以部署 Runbook、实时门禁、环境验收和变更审批共同决定。
>
> 计划周期：2026-07-20 ～ 2027-01-03（24 周，12 个双周 Sprint）
>
> 当前基线日期：2026-07-17
>
> 最后更新时间：2026-08-04
>
> 升级起点发布基线：`v0.1.0` / `33b4eec54b6ea9a44f612b886dfd6ce46c5870f6`
>
> 历史记录截止基线：`main` / `674bc92` / [PR #6](https://github.com/BaiKudan/DuckDock/pull/6)。其中的 `2.0.0-ga`、测试数字和 READY 结论只描述 2026-08-04 当时的工作区，不代表当前发布身份；当前 `2.0.0-rc.1` 状态见 [`release-2.0-rc1-readiness.zh-CN.md`](./release-2.0-rc1-readiness.zh-CN.md)。
>
> 历史结论：在 2026-08-04 的本地开发环境中，计划内 E00～E09 技术检查曾全部完成。该记录不代表任意后续提交、其他部署环境或生产审批仍然通过。

## 1. 记录用途与证据边界

本文件用于追溯当时的工程执行过程，记录：

- 24 周范围、里程碑、Epic 和 Sprint 状态；
- Gate 是否通过及其可复核证据；
- 风险、阻塞、关键决策和计划变更；
- 当前基线、实际进度、负责人和下一步。

维护规则：

1. 当前发布和生产状态不得从本文中的百分比、`READY` 或历史测试数字推断。
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
| API Endpoint 路由模块 | 23（另有 2 个聚合 router.py） |
| Endpoint decorators | 212 |
| SQLAlchemy 业务表 | 67 |
| Alembic revisions | 34 |
| 后端测试文件 / test functions | 93 / 709 |
| 前端单元测试文件 / Playwright 文件 | 5 / 2 |
| mypy baseline ceiling | 82 |
| 生产部署形态 | 单机 Docker Compose |

## 4. 24 周里程碑与 Sprint 路线

| Sprint | 周期 | 主题 | 关键交付 | 里程碑/Gate | 状态 |
|---|---|---|---|---|---|
| S0 | W1–W2，07-20～08-02 | 架构冻结与契约 | 2.0 边界、ADR、Evidence/Eval/Package/Release schema、迁移方案 | M0 / G0 | 已完成 |
| S1 | W3–W4，08-03～08-16 | Domain Foundation | Namespace 直接归属、不可变 Deployment、Transactional Outbox | M1a | 已完成 |
| S2 | W5–W6，08-17～08-30 | Run Index Alpha | AgentSession/AgentRun、Reporter envelope、TraceRef、旧模型兼容 | M1 / G1 | 已完成 |
| S3 | W7–W8，08-31～09-13 | OpenClaw Shadow Telemetry | OTel Collector、OpenClaw Bridge、metadata-only、可信属性注入 | M2a | 已完成 |
| S4 | W9–W10，09-14～09-27 | Generic OTLP + Fleet | Generic OTLP、Pack/ATIF 兼容、动态 capability、心跳与漂移 | M2 / G2 | 已完成 |
| S5 | W11–W12，09-28～10-11 | Eval Hub 数据面 | Dataset/Case/Suite/Evaluator/EvalRun、版本与存储 | M3a | 已完成 |
| S6 | W13–W14，10-12～10-25 | Eval 执行与比较 | Eval worker、case result、baseline、comparison、regression | M3 / G3 | 已完成 |
| S7 | W15–W16，10-26～11-08 | Package v2 + Release Gate | Agent manifest、component graph、SBOM、Policy shadow | M4a | 已完成 |
| S8 | W17–W18，11-09～11-22 | 晋级/Canary/回滚 | Environment、Promotion、DeploymentReceipt、Canary、Rollback | M4 / G4 | 已完成 |
| S9 | W19–W20，11-23～12-06 | Handover 2.0 + Identity | 依赖图、readiness、义务清单、SCIM/设备注册策略 | M5 / G5 | 已完成 |
| S10 | W21–W22，12-07～12-20 | 安全、观测与迁移 | audit outbox、OTel/Prometheus、v1/v2 对账、恢复预演 | M6a | 已完成 |
| S11 | W23–W24，12-21～01-03 | GA 候选验收 | 三 Runtime 闭环、负载/故障测试、恢复演练、文档与发布 | M6 / G6 | 已完成（技术候选） |

里程碑定义：

| 里程碑 | 目标日期 | 结果定义 | 状态 |
|---|---|---|---|
| M0 Architecture Freeze | 2026-08-02 | 2.0 协议、边界、数据模型和迁移 ADR 获批 | 已完成 |
| M1 Evidence Alpha | 2026-08-30 | 可信 Run envelope、版本索引、Outbox 和 TraceRef 闭环 | 已完成 |
| M2 Runtime Beta | 2026-09-27 | OpenClaw、Hermes Reporter、Generic OTLP、Pack/ATIF 接入路径与 Fleet 状态可用 | 已完成 |
| M3 Eval Beta | 2026-10-25 | 可复现 Eval、版本比较和回归判定可用 | 已完成 |
| M4 Release RC | 2026-11-22 | 评测驱动的分环境晋级、Canary 和回滚可用 | 已完成 |
| M5 Handover RC | 2026-12-06 | 基于运行证据的交接和身份联动闭环 | 已完成 |
| M6 2.0 GA Candidate | 2027-01-03 | 安全、SLO、灾备和三 Runtime 端到端 Gate 通过 | 已完成（技术候选） |

## 5. Epic 总台账

| Epic | 能力域 | 目标 | 周期 | 依赖 | Owner | 状态 | 进度 | 验收证据 |
|---|---|---|---|---|---|---|---:|---|
| E00 | 产品/架构契约 | 冻结 2.0 边界、schema、API 和迁移策略 | W1–W2 | 无 | Architecture Owner | 已完成 | 100% | [G0 review](../specs/008-duckdock-2-foundation/gate-g0-review.md)、[Constitution](../.specify/memory/constitution.md)、[ADR](adr/README.md)、27 项 focused tests、Owner 批准 |
| E01 | Evidence Fabric | Namespace/Deployment/Run 索引、可信 envelope、TraceRef 和 Outbox | W3–W6 | E00 | Backend Architecture | 已完成 | 100% | EF-01～04 Namespace contract；EF-05 immutable Deployment；EF-06 metadata-only Session/Run lifecycle；EF-07 API v2；EF-08 Artifact/Telemetry ports；EF-09 transactional Outbox；EF-10 compatibility/release seam/backout evidence |
| E02 | Runtime Adapter | OpenClaw、Hermes Reporter、Generic OTLP、Pack/ATIF 兼容与 conformance | W7–W10 | E00、E01 | Integration/Observability | 已完成 | 100% | RT-01～08/NEXT-001～003：可信 Collector、生命周期 correlation、版本化 GenAI normalizer、Langfuse export/confirmation、持久队列、Generic OTLP DD-C2、Pack/ATIF DD-C3、Hermes 原生 DD-C1/Fleet/真实 cron；G2 于 2026-07-31 获 Owner 明确批准 |
| E03 | Eval Hub | Dataset、Suite、Evaluator、EvalRun、Comparison、Regression | W11–W14 | E01 | Eval Architecture | 已完成 | 100% | EH-01～06 全部验收，包含 Langfuse-first 数据面、可恢复执行、不可变结果清单、基线比较、Release Gate 证据、UI 与不可变人工评审；G3 于 2026-07-31 获 Owner 明确批准；G3 后续 NEXT-007～017 已补齐 Trace2Dataset、治理策展/采样/标注、Promotion、Golden/Bad Case 路由、失败分类与 Experience、真实语义聚类、离线漂移门禁及定时监控/站内告警；[EH-01](../specs/009-evaluation-hub/evidence/eval-hub-langfuse-v4-20260731.md) / [EH-02](../specs/009-evaluation-hub/evidence/eval-hub-eh02-execution-20260731.md) / [EH-03](../specs/009-evaluation-hub/evidence/eval-hub-eh03-result-manifest-20260731.md) / [EH-04](../specs/009-evaluation-hub/evidence/eval-hub-eh04-comparison-20260731.md) / [EH-05](../specs/009-evaluation-hub/evidence/eval-hub-eh05-release-gate-20260731.md) / [EH-06/G3](../specs/009-evaluation-hub/evidence/eval-hub-eh06-ui-review-g3-20260731.md) / [NEXT-007](../specs/009-evaluation-hub/evidence/eval-hub-trace2dataset-20260731.md) / [NEXT-008](../specs/009-evaluation-hub/evidence/eval-hub-curation-queue-20260731.md) / [NEXT-009](../specs/009-evaluation-hub/evidence/eval-hub-sampling-policy-20260731.md) / [NEXT-010](../specs/009-evaluation-hub/evidence/eval-hub-annotation-queue-20260803.md) / [NEXT-011](../specs/009-evaluation-hub/evidence/eval-hub-promotion-policy-20260803.md) / [NEXT-012](../specs/009-evaluation-hub/evidence/eval-hub-case-routing-20260803.md) / [NEXT-013](../specs/009-evaluation-hub/evidence/eval-hub-failure-taxonomy-experience-20260803.md) / [NEXT-014](../specs/009-evaluation-hub/evidence/eval-hub-experience-asset-activation-20260803.md) / [NEXT-015](../specs/009-evaluation-hub/evidence/eval-hub-semantic-failure-clustering-20260803.md) / [NEXT-016](../specs/009-evaluation-hub/evidence/eval-hub-semantic-regression-20260803.md) / [NEXT-017](../specs/009-evaluation-hub/evidence/eval-hub-semantic-monitor-20260804.md) |
| E04 | Package Registry v2 | Agent manifest、component graph、SBOM、签名与 provenance | W15–W16 | E00、E01 | Registry Architecture | 已完成 | 100% | PKG-01～06：revision 0057；严格 Manifest/DAG；CycloneDX/SPDX MinIO SBOM；Ed25519 公钥信任/吊销；不可变 provenance；Deployment 精确 pin；真实 HTTP/MySQL/MinIO/浏览器；[E04 evidence](../specs/010-package-registry-v2/evidence/package-registry-v2-20260804.md) |
| E05 | Release Control | Policy、审批、Environment、Promotion、Canary、Rollback | W15–W18 | E03、E04 | Release Architecture | 已完成 | 100% | RC-01～08；revisions 0058/0059；真实 Hermes receipt/canary/rollback；986/52 全量回归；[G4 evidence](../specs/011-release-control/evidence/release-control-g4-20260804.md) |
| E06 | Handover 2.0 | evidence snapshot、依赖图、readiness、义务与签名交接包 | W19–W20 | E01、E05 | Handover Domain | 已完成 | 100% | revision 0060；真实 snapshot/obligation/acceptance/外部 Ed25519 signed package；目录停用联动；[G5 evidence](../specs/012-handover-2/evidence/handover-2-g5-20260804.md) |
| E07 | Identity/Security | SCIM、service/device identity、audit outbox、签名轮换 | W19–W22 | E01 | Security/IAM | 已完成 | 100% | revision 0061；真实 SCIM disable→credential revoke→access removal→Handover；DEVICE identity 与 key rotation；[E07 evidence](../specs/013-identity-security/evidence/identity-security-e07-20260804.md) |
| E08 | Observability/Ops | OTel、Prometheus、告警、SLO、真实恢复演练 | W21–W24 | E01–E07 | Platform/Ops | 已完成 | 100% | revision 0062；content-safe metrics/SLO/incident；Prometheus rules；MySQL+MinIO real restore RPO=0/RTO=1；[E08 evidence](../specs/014-observability-ops/evidence/observability-ops-e08-20260804.md) |
| E09 | API/UI/Migration | `/api/v2`、v1 兼容、双写对账、新控制台信息架构 | W1–W24 | 全部 | API/UI/Migration | 已完成 | 100% | frozen `2.0.0-ga` contract；v1 2.x compatibility；14/14 GA checks PASS；1005/53 regression；[G6 evidence](../specs/015-ga-candidate/evidence/ga-candidate-e09-20260804.md) |

总体进度计算：

```text
总体进度 = Σ（Epic 权重 × Epic 已验收任务权重）
```

初始 Epic 权重：E00 5%、E01 18%、E02 12%、E03 15%、E04 8%、E05 15%、E06 8%、E07 7%、E08 7%、E09 5%。权重变更必须登记决策日志。

S0 的 14 个 P0 任务各计 1 个验收点。E00～E05 的计算方式保持不变；E06 的 HO-01～08、E07 的 IAM-01～09、E08 的 OPS-01～08、E09 的 GA-01～08 均已完成并有真实环境证据。最终总体为 `5% + 18% + 12% + 15% + 8% + 15% + 8% + 7% + 7% + 5% = 100.0%`。这里的 100% 表示既定 DuckDock 2.0 技术范围完成，不替代用户后续人工产品验收或生产发布审批。

E01 验收切片：

| Slice | 结果定义 | 状态 | 证据 |
|---|---|---|---|
| EF-01 | WorkTrace、Release Gate、旧租户路径与架构边界兼容基线 | 已完成 | `backend/tests/foundation/` 4 个兼容测试文件，15 项通过 |
| EF-02 | 五表 nullable/indexed Namespace FK expand，可在 populated MySQL 安全升级 | 已完成 | revision 0027；8 项 migration tests；5 项 MySQL lane；`upgrade head` + `alembic check` |
| EF-03 | 确定性 Tenant Resolver 与 unresolved/conflict 分类 | 已完成 | `tenant_resolution_service.py`；17 项 resolver 测试；完整排序候选与禁止弱推断 |
| EF-04 | Backfill/audit、Namespace 强制新写与 contract preflight | 已完成 | FND-012/013/014/017/018/019；ADR-0211/0212；经 Owner 授权、完整备份和写静默精确清理 275 条开发测试数据；保留零 blocker 报告；revision 0029 non-null/index/unique contract 已在真实 MySQL 与开发库通过 |
| EF-05 | 不可变 AgentDeployment/Component inventory | 已完成 | FND-020～028；revision 0030；REGISTERED→ACTIVE→RETIRED/FAILED；跨租户与无版本引用拒绝；激活后快照不可变；Namespace RBAC API；MySQL 并发注册单赢家；严格配置白名单 |
| EF-06 | AgentSession/AgentRun metadata-only 生命周期 | 已完成 | FND-030/032/033/034/037/038/039；revision 0031；canonical JSON v1 + SHA-256；start/complete replay/conflict；终态 row lock；MySQL start/complete race；raw content 与未知/过大 metadata 拒绝 |
| EF-07 | Reporter API v2、credential-derived identity 与幂等冲突 | 已完成 | FND-031/035/036/040～043；`execution.write` scope；credential→Runtime→Namespace→Trust；四个 Reporter lifecycle API；有界签名游标管理读；跨租户 404；拒绝事务回滚后的无内容安全审计 |
| EF-08 | AgentRunArtifact、TraceBackendRef 与 provider-neutral ports | 已完成 | FND-050～058；revision 0032；immutable artifact metadata；SSRF-safe Sink config + credential reference only；Null/InMemory TelemetrySink/TrajectoryCodec/AttestationVerifier ports；RBAC API；provider failure isolation |
| EF-09 | Transactional Outbox、dispatcher、retry/DLQ/replay | 已完成 | FND-060～069；revision 0033；Session/Run/Artifact 同事务事件；低敏 allowlist；MySQL `SKIP LOCKED`；稳定 event ID；lease recovery；指数退避/FAILED；admin health/retry；Beat/Worker live |
| EF-10 | v1/v2 兼容、Release evidence seam、回滚演练、G1 Evidence Alpha 技术验收包 | 已完成 | FND-070～078；candidate + Deployment revision 精确查询端口；当前 Gate 不串线；ingestion switch/Runtime allowlist；Beat 与应用 backout drill；100/s 持续与 500-request 短突发通过；v1/v2 event-driven 对账零未解释差异；[证据包](../specs/008-duckdock-2-foundation/evidence/g1-evidence-alpha-20260728.md) |

E02 验收切片：

| Slice | 结果定义 | 状态 | 证据 |
|---|---|---|---|
| RT-01 | OpenClaw edge Collector 认证、可信 Runtime/Namespace 覆盖、metadata-only allowlist 与 Secret Canary | 已完成 | NEXT-001；Collector 0.157.0 pinned config；未认证 401/认证 200；[S3 evidence](../specs/008-duckdock-2-foundation/evidence/s3-next001-openclaw-shadow-20260730.md) |
| RT-02 | OpenClaw lifecycle instrumentation 与稳定 Run/Session/Trace correlation | 已完成 | Runtime MCP v0.2.0 四个 lifecycle tools；稳定幂等键；external Session/Run 与 OTel trace/root span 同步；10 项测试 |
| RT-03 | OTel GenAI/OpenInference 完整版本化 normalizer | 已完成 | Collector `gen_ai_normalizer`；OpenClaw + built-in OpenInference；OTel schema 1.40.0；双格式 golden 等价与 Secret Canary |
| RT-04 | 可选 Langfuse adapter、TraceBackendRef confirmation 与 provider failure isolation | 已完成 | v4 OTLP overlay；Basic Auth/header/retry/queue；Observations API v2 bounded confirmation；[NEXT-002 evidence](../specs/008-duckdock-2-foundation/evidence/s3-next002-genai-langfuse-20260730.md) |
| RT-05 | OpenClaw durable buffer、ack cursor、loss marker 与离线重放 | 已完成 | Runtime MCP v0.3.0 SQLite WAL/ordered dependency replay；Collector file-storage WAL/KILL restart replay；显式 PARTIAL/DECLARED_LOSS；[RT-05 evidence](../specs/008-duckdock-2-foundation/evidence/s3-rt05-durable-replay-20260730.md) |
| RT-06 | Generic OTLP 可信 bridge、mapping conflict quarantine 与 conformance | 已完成 | revision 0035；per-Runtime authenticated Collector；metadata-only durable dual export；OTL-001～009/CONF-C2-001～009；[RT-06 evidence](../specs/008-duckdock-2-foundation/evidence/s4-rt06-generic-otlp-20260730.md) |
| RT-07 | Pack/ATIF manifest/preflight/import 与 MinIO 完整性闭环 | 已完成 | revision 0036；严格 `duckdock-pack/1.0`；ATIF v1.0–v1.7；PAT-001～012；真实 MinIO PUT/finalize/readback；[RT-07 evidence](../specs/008-duckdock-2-foundation/evidence/s4-rt07-pack-atif-20260730.md) |
| RT-08 | 四路径共享 conformance、动态 capability/Fleet 状态与 G2 evidence | 已完成 | revisions 0037/0039；CONF-C0-001～007；动态 dependency-derived capability；Hermes 原生 DD-C1；心跳历史/版本/Collector/配置漂移；`/fleet` UI；2026-07-31 Owner 明确批准；[RT-08/G2 evidence](../specs/008-duckdock-2-foundation/evidence/s4-rt08-dynamic-fleet-g2-20260730.md) |
| NEXT-003 | Pack ATIF export、resumable multipart、durable batch ack/cursor 与 evaluation replay | 已完成 | revision 0038；真实 MySQL downgrade/upgrade/check；真实 MinIO multipart resume/complete；[NEXT-003 evidence](../specs/008-duckdock-2-foundation/evidence/s4-next003-pack-ddc3-20260730.md) |

E03 验收切片：

| Slice | 结果定义 | 状态 | 证据 |
|---|---|---|---|
| EH-01 | Langfuse-first Dataset/Evaluator/Experiment/Evaluation 治理索引、不可变版本、provider sync 与 DeepEval 端口 | 已完成 | revision 0040；真实 MySQL upgrade/downgrade/check；Langfuse v4 Dataset sync；53 项 focused regression；[EH-01 evidence](../specs/009-evaluation-hub/evidence/eval-hub-langfuse-v4-20260731.md) |
| EH-02 | Eval worker、租约、重试、幂等执行与取消 | 已完成 | revision 0041；同事务 Outbox + recovery Beat；lease/heartbeat/reclaim；稳定 execution key；退避、取消与人工 retry；真实 Langfuse v4 Experiment 得分 0.5；913 项全量回归；[EH-02 evidence](../specs/009-evaluation-hub/evidence/eval-hub-eh02-execution-20260731.md) |
| EH-03 | Provider-hosted case/result artifact 与摘要回写 | 已完成 | revision 0042；不可变 `EvaluationResultManifest`；expected/processed/scored/pass/fail/error 与 `COMPLETE/PARTIAL`；部分结果重试保留旧清单；安全 API/Outbox；真实 Langfuse Public API 重算 digest 一致；914 项全量回归；[EH-03 evidence](../specs/009-evaluation-hub/evidence/eval-hub-eh03-result-manifest-20260731.md) |
| EH-04 | Baseline comparison、回归判定与可复现运行 | 已完成 | revision 0043；不可变 RegressionPolicyVersion 与显式 baseline/candidate manifest pins；PASS/REGRESSION/INCONCLUSIVE；no-latest 锁；真实 Langfuse 同 item 比较和独立 digest 复算；919 项全量回归；[EH-04 evidence](../specs/009-evaluation-hub/evidence/eval-hub-eh04-comparison-20260731.md) |
| EH-05 | Eval 结果绑定 Release Gate evidence | 已完成 | revision 0044；不可变 candidate/Deployment revision/Comparison 精确绑定；target ref/config digest 对齐；绑定后 Deployment freeze；缺失/REGRESSION/INCONCLUSIVE fail-closed；安全 Outbox/审计；真实 Langfuse comparison→Gate `PASS` 与未绑定候选 `BLOCKED`；924 项全量回归；[EH-05 evidence](../specs/009-evaluation-hub/evidence/eval-hub-eh05-release-gate-20260731.md) |
| EH-06 | Eval Hub UI、人工评审与 G3 evidence | 已完成 | revision 0045；每个精确 binding 一次不可变 APPROVED/REJECTED 决定；拒绝 fail closed；安全 Outbox/审计；`/eval-hub` 总览/比较/门禁 UI；真实 MySQL、Langfuse-backed binding、HTTP 与浏览器复验；927 项后端、37 项前端回归；2026-07-31 Owner 明确批准；[EH-06/G3 evidence](../specs/009-evaluation-hub/evidence/eval-hub-eh06-ui-review-g3-20260731.md) |

G3 后续数据飞轮增强（不重复计入 E03 权重）：

| Slice | 结果定义 | 状态 | 证据 |
|---|---|---|---|
| NEXT-007 | 单条 Observation 显式 Trace2Dataset、provider-local 原始 IO 复制与不可变 DatasetVersion | 已完成 | revision 0046；确定性 item、来源链接、时间点 pin、幂等回执；[evidence](../specs/009-evaluation-hub/evidence/eval-hub-trace2dataset-20260731.md) |
| NEXT-008 | metadata-only 候选检索、1～20 项不可变选择、独立审批与一次批量物化一个 DatasetVersion | 已完成 | revision 0047；真实 Langfuse 三条候选、浏览器批量审批/物化、Outbox/Audit 内容边界、隔离 MySQL guard；943 项后端、40 项前端回归；[evidence](../specs/009-evaluation-hub/evidence/eval-hub-curation-queue-20260731.md) |
| NEXT-009 | 不可变生产采样策略版本、确定性选择快照与自动待审批批次 | 已完成 | revision 0048；显式策略版本/Dataset/时间窗、stable hash、最低样本 fail-closed、已治理来源强制排除；真实 Langfuse 5 选 3、浏览器待审批闭环；947 项后端、42 项前端回归；[evidence](../specs/009-evaluation-hub/evidence/eval-hub-sampling-policy-20260731.md) |
| NEXT-010 | 解耦 Langfuse Annotation Queue 绑定、持久派发、异步同步与完成状态对账 | 已完成 | revision 0049；Queue/Score Config 快照、reconcile-before-create、lease/backoff/retry、content-free Outbox/Audit；真实 Hermes 三条 Observation、浏览器 3/3 同步与 3/3 完成对账且不自动审批；951 项后端、43 项前端回归；[evidence](../specs/009-evaluation-hub/evidence/eval-hub-annotation-queue-20260803.md) |
| NEXT-011 | 标注驱动、版本化质量/多样性 Promotion 推荐，保持审批与物化解耦 | 已完成 | revision 0050；精确 Queue/Score Config/评分类型规则；Scores API v3 `ANNOTATION` 证据；`RECOMMENDED/BLOCKED` fail-closed；真实 Hermes 三条 Observation 的 3/3 质量通过、1 桶多样性分别验证 BLOCKED 与 RECOMMENDED，批次仍未审批/物化；955 项后端、44 项前端回归；[evidence](../specs/009-evaluation-hub/evidence/eval-hub-promotion-policy-20260803.md) |
| NEXT-012 | 同一 Promotion 版本跨批次 cluster-balanced Golden/Bad Case 双路由，保持两个审批队列独立 | 已完成 | revision 0051；精确 Promotion version pin、跨运行 Observation 去重、稳定 cluster round-robin、最低数量原子 fail-closed；真实 Hermes 两个运行共六条 Observation 路由为 Golden 4/2 clusters 与 Bad Case 2/1 cluster，两个批次均 PENDING_REVIEW/无 review/materialization；959 项后端、45 项前端回归；[evidence](../specs/009-evaluation-hub/evidence/eval-hub-case-routing-20260803.md) |
| NEXT-013 | 版本化 failure taxonomy 与 metadata-only Experience candidate 提取，独立不可变人工评审 | 已完成 | revision 0052；精确 Case Routing version pin、跨运行/单运行重复/孤立三类确定性分类、isolated 默认排除、候选上限与 no-eligible BLOCKED；真实 Hermes 2 个 Bad Cases/1 cluster 提取为 SINGLE_RUN_RECURRING 并从 PENDING_REVIEW 独立批准，未生成生产变更；963 项后端、46 项前端、57 项 Langfuse v4 gate；[evidence](../specs/009-evaluation-hub/evidence/eval-hub-failure-taxonomy-experience-20260803.md) |
| NEXT-014 | 已批准候选创建 Experience 资产、人工不可变版本与独立四眼激活，保持 Runtime/Provider 解耦 | 已完成 | revision 0053；候选单次认领、版本 digest、DRAFT→PENDING_ACTIVATION→ACTIVE/REJECTED、作者/请求人禁止审批、批准新版本退役旧版本；真实 HTTP 同人 409/第二人 ACTIVE，浏览器看到资产与 ACTIVE；Audit/Outbox 正文泄露为 0；966 项后端、47 项前端、57 项 Langfuse v4 gate；[evidence](../specs/009-evaluation-hub/evidence/eval-hub-experience-asset-activation-20260803.md) |
| NEXT-015 | 可替换的真实语义失败聚类，精确 pin 到 Failure Taxonomy/Experience 提取并保持原文/向量不落 DuckDock | 已完成 | revision 0054；provider-neutral port、Langfuse IO 瞬时读取、OpenAI-compatible embedding、确定性 cosine/centroid、metadata 兼容路径；真实 Hermes BGE 将 2 个 Bad Cases 聚为 1 个合格簇并提取 1 个候选；969 项后端、47 项前端、59 项 Langfuse v4 gate；[evidence](../specs/009-evaluation-hub/evidence/eval-hub-semantic-failure-clustering-20260803.md) |
| NEXT-016 | 同源语义聚类 baseline/candidate 的不可变质量与漂移离线回归门禁 | 已完成 | revision 0055；精确 Case Routing/source/content digest 对齐，pairwise assignment agreement、簇数量变化、合格簇比例下降和 centroid similarity 下降四项阈值，PASS/DRIFTED/INCONCLUSIVE fail-closed；真实 Hermes 稳定候选 PASS、漂移候选 DRIFTED；971 项后端、48 项前端、60 项 Langfuse v4 gate；[evidence](../specs/009-evaluation-hub/evidence/eval-hub-semantic-regression-20260803.md) |
| NEXT-017 | 精确 pin 的定时语义重聚类、离线漂移比较与站内告警/确认 | 已完成 | revision 0056；Beat due discovery、Worker lease/retry、PASS 静默、DRIFTED/INCONCLUSIVE/终态失败告警、一次性确认、pause/resume/run-now；真实 Beat+Worker+Hermes 稳定监控 PASS 无告警、漂移监控 DRIFTED/CRITICAL→ACKNOWLEDGED；973 项后端、49 项前端、60 项 Langfuse v4 gate；[evidence](../specs/009-evaluation-hub/evidence/eval-hub-semantic-monitor-20260804.md) |

E04 验收切片：

| Slice | 结果定义 | 状态 | 证据 |
|---|---|---|---|
| PKG-01 | Namespace-scoped AgentPackage 身份、不可变语义 PackageVersion 与跨租户 fail-closed | 已完成 | revision 0057；Package/Version/Agent 精确约束；同 Namespace name/version unique；focused tests |
| PKG-02 | 严格 Manifest v2、规范化 digest、隐式节点与显式无环组件图 | 已完成 | unknown field/missing node/self-edge/duplicate edge/cycle 拒绝；确定性 graph digest；G0 schema/example 扩展 |
| PKG-03 | CycloneDX/SPDX SBOM digest、完整 artifact hash coverage 与 MinIO 归档 | 已完成 | 2 MiB/2048 上限；content-bearing key 禁止；真实 MinIO 513-byte readback SHA 一致；授权签名 URL |
| PKG-04 | Ed25519 Namespace 公钥信任、指纹、吊销与 detached signature 验证 | 已完成 | 私钥从不进入 DuckDock；canonical PEM/64-byte signature；wrong/revoked/cross-tenant fail closed；真实外部私钥签名 |
| PKG-05 | Provenance、内容安全 Audit/Outbox、全证据复验与 Deployment 精确 PackageVersion pin | 已完成 | 三条 Package 事件均 PUBLISHED；PEM/签名/Manifest/SBOM 正文泄露 0；ACTIVE Deployment exact pin |
| PKG-06 | Registry UI、全量门禁和真实开发环境证据 | 已完成 | backend 980/19；frontend 50；lint 0 error/9 existing warnings；build PASS；mypy 82/82；MySQL head=0057/check clean；真实 HTTP/MinIO/浏览器；[E04 evidence](../specs/010-package-registry-v2/evidence/package-registry-v2-20260804.md) |

E05 验收切片：

| Slice | 结果定义 | 状态 | 证据 |
|---|---|---|---|
| RC-01 | Namespace-scoped Environment、不可变 ReleaseCandidate 与精确 PackageVersion/Deployment revision pin | 已完成 | revisions 0058/0059；Hermes baseline/canary 候选与精确 config digest；[G4 evidence](../specs/011-release-control/evidence/release-control-g4-20260804.md) |
| RC-02 | 版本化 Release Policy、shadow/warn/enforce 与可解释 rule-level decision | 已完成 | ALLOW/BLOCK、规则原因和 evidence ref 全部持久化；focused regression；[G4 evidence](../specs/011-release-control/evidence/release-control-g4-20260804.md) |
| RC-03 | 四眼审批、限时例外与到期 fail-closed | 已完成 | requester/approver 隔离；Exception list/API/UI；Audit/Outbox；[G4 evidence](../specs/011-release-control/evidence/release-control-g4-20260804.md) |
| RC-04 | Environment Promotion、幂等重试与运行时回执状态机 | 已完成 | baseline Promotion SUCCEEDED/APPLIED；重复请求一致；[G4 evidence](../specs/011-release-control/evidence/release-control-g4-20260804.md) |
| RC-05 | 独立最小权限 Runtime receipt credential、撤销与租户隔离 | 已完成 | 仅 `release.receipt` scope；签发后即时可见；真实 receipt 后全部验证凭证已撤销；[G4 evidence](../specs/011-release-control/evidence/release-control-g4-20260804.md) |
| RC-06 | Canary 指标评估、失败自动触发精确 Rollback | 已完成 | 真实 metadata-only failed AgentRun；Canary FAIL/failure_rate_exceeded；自动 rollback；[G4 evidence](../specs/011-release-control/evidence/release-control-g4-20260804.md) |
| RC-07 | Rollback 动作、Runtime APPLIED 回执、环境激活历史与审计闭环 | 已完成 | canary Promotion ROLLED_BACK；rollback SUCCEEDED；Runtime ROLLBACK/APPLIED；激活恢复 baseline；[G4 evidence](../specs/011-release-control/evidence/release-control-g4-20260804.md) |
| RC-08 | Release Control UI、真实 Hermes、本地浏览器、迁移与全量质量门 | 已完成 | backend 986/19；frontend 52；lint/build PASS；MySQL head=0059/check clean；真实 Hermes + 浏览器 console 0 error；[G4 evidence](../specs/011-release-control/evidence/release-control-g4-20260804.md) |

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
| S0-09 | 定义 `/api/v2` 与 v1 兼容窗口 | API versioning/deprecation policy | API Architecture | P0 | 已完成 | S0-02、S0-03 | [OpenAPI v2](../specs/008-duckdock-2-foundation/contracts/openapi-v2.yaml) + 20 focused tests |
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
| S1-06 | FND-010 | empty/populated migration、deterministic/unresolved/conflict/CAS 真实 MySQL 夹具 | P0 | 已完成 | MySQL 8.4：expand/contract migration、12 项完整 marker lane、Alembic check |
| S1-07 | FND-011 | Tenant Resolver precedence/repeatability/unresolved/conflict 先红测试 | P0 | 已完成 | `test_tenant_resolution_service.py` 17 项；缺模块及四项安全边界均有真实红→绿记录 |
| S1-08 | FND-016/017 | Resolver 与幂等 backfill/audit command | P0 | 已完成 | Resolver、backfill service、显式安全 CLI、[runbook](../specs/008-duckdock-2-foundation/tenant-backfill-runbook.md)；31 项 focused tests + 5 项 S1-B MySQL fixtures |
| S1-09 | FND-012/013/018 | 强制 Namespace 新写、跨租户关系拒绝 | P0 | 已完成 | `tenant_write_service.py`、management/ingestion/materialization/handover 写路径、Foundation 与现有回归测试 |
| S1-10 | ADR-0211 / revision 0028 | Evidence typed WorkTrace link expand | P0 | 已完成 | nullable indexed FK、`ON DELETE SET NULL`、typed-only resolver/adapter；无 backfill/contract DDL |
| S1-11 | FND-014/019 | contract blocker preflight、non-null 与 tenant-scoped indexes | P0 | 已完成 | revision 0029；unsafe MySQL migration 在 DDL 前拒绝并报告五类 blocker，修复后成功；开发库零 blocker 升级；733 项默认测试、12 项 MySQL lane、迁移/Compose 门禁通过 |
| S1-12 | FND-020～028 | 不可变 AgentDeployment / DeploymentComponent inventory | P0 | 已完成 | revision 0030、lifecycle/immutable/tenant-safe service、Namespace RBAC API、strict schema；109 Foundation passed；MySQL concurrent registration；空库与开发库 upgrade/check clean |
| S1-13 | FND-030/032～034/037～039 | metadata-only Session/Run 模型、canonical hash 与生命周期 | P0 | 已完成 | revision 0031；strict envelope、UTC/NFC/null/numeric canonicalization、幂等 replay/conflict、终态并发保护；131 Foundation passed；MySQL race 与 upgrade/check clean |
| S1-14 | FND-031/035/036/040～043 | Reporter/management API v2 与安全边界 | P0 | 已完成 | `execution.write`、credential 派生租户/Runtime/Trust、四个 lifecycle 写接口、31 天窗口与签名 cursor 管理读、跨租户 404、可持久化无内容拒绝审计；136 Foundation passed、10 skipped |
| S1-15 | FND-050～069 | Artifact/Telemetry ports 与 Transactional Outbox | P0 | 已完成 | revisions 0032/0033；provider-neutral refs、同事务事件、MySQL lease/retry/FAILED、admin health/replay 与 live Worker |
| S1-16 | FND-070～078 | 兼容性、candidate-pinned release seam 与运维交接 | P0 | 已完成 | 184 Foundation passed/12 skipped；默认 Compose 不隐式启 observability；ingestion/dispatcher backout drill；G1 technical acceptance evidence |

## 7. Gate 标准

### G0：Architecture Freeze

- 2.0 产品边界与非目标获批。
- Agent Package v2、Telemetry Envelope v1、Evaluation Result v1、Release Manifest v1 均有 JSON Schema、示例和兼容规则。
- 核心 ERD、API v2、存储职责、隐私分类和迁移 ADR 获批。
- OpenClaw、Hermes Reporter、Generic OTLP、ATIF/Pack 均完成字段映射；AgentLoop Connector 完成可行性映射，无关键不可表达对象。
- S1/S2 的测试、负载和回滚方案可执行。

### G1：Evidence Alpha

- Metadata-only Run envelope 可被接收、验证，并物化为 AgentRun、TraceRef 和 Artifact 索引；原始 Span 不进入 MySQL。
- `event_id` 与幂等键重放不产生重复数据；同键不同 payload 明确冲突。
- Run、Deployment、TraceBackendRef 和 artifact hash 可双向追溯。
- Outbox 具备可观测失败原因、重试、死信状态和管理员 replay 工具。
- Run 控制信封持续 100 req/s、短时 500 req/s；Collector 遥测另行执行 Span 吞吐压测。
- v1 Reporter 输入保持兼容，并通过新旧数据对账测试。

验收状态：**已批准**。三次隔离 MySQL 8.4 运行均完成
`500/500` 持续请求与 `500/500` 一秒短突发，零错误并精确物化
Run/Audit/Outbox；revision 0034 的 v1 Structured Report Outbox consumer、
幂等 receipt/projection 和 live Worker smoke 均通过，未解释差异为 0。
Product/Architecture Owner 于 2026-07-30 审阅
[G1 Evidence Alpha 技术验收包](../specs/008-duckdock-2-foundation/evidence/g1-evidence-alpha-20260728.md)
后明确回复“确认，继续。”，M1/G1 正式关闭并放行 S3。

### G2：Runtime Beta

- OpenClaw、Hermes Reporter、Generic OTLP、现有 Pack/ATIF 兼容路径均完成真实接入验证。
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

G4 技术验收已于 2026-08-04 完成：DEV/PRODUCTION 与精确 CANARY 晋级、版本化 Policy 决策、四眼审批/例外、最小权限 Runtime 回执、失败 Canary 自动回滚及环境激活恢复均在真实本地 Hermes、MySQL、HTTP 和浏览器链路复验通过；详见 [Release Control G4 evidence](../specs/011-release-control/evidence/release-control-g4-20260804.md)。

### G5：Handover RC

- 交接可自动生成资产依赖图、生产版本、最近运行、Eval baseline、风险、权限和 runbook。
- 所有生产 Agent 均有 owner、receiver 或 fallback owner。
- 高关键/敏感交接项继续执行证据门禁。
- 接收人权限检查、失败确认、验收和签名交接包闭环通过 E2E。
- 至少一个真实 IdP/SCIM 或等价 staging directory 流程完成禁用用户→撤销凭证→触发交接。

G5 技术验收已于 2026-08-04 完成：revision 0060/0061、真实 Hermes release evidence、MySQL/MinIO snapshot、外部 Ed25519 signed package 和 staging-equivalent SCIM disable 联动均通过；详见 [Handover G5 evidence](../specs/012-handover-2/evidence/handover-2-g5-20260804.md) 与 [Identity/Security evidence](../specs/013-identity-security/evidence/identity-security-e07-20260804.md)。

### G6：2.0 GA Candidate

- OpenClaw 真实 Runtime、Generic OTLP Fixture 和 Pack/ATIF 兼容路径完成 Evidence→Eval→Release→Rollback/Handover 端到端场景。
- API、worker 和关键 workflow 故障恢复测试通过。
- Evidence ingest accepted p95 <200ms；30 天 Run 时间线 p95 <2s；Policy decision p95 <100ms。
- Prometheus/OTel、关键 dashboard 和告警就绪。
- 无未处理 Critical 安全问题；High 问题必须有批准的期限化豁免。
- 完成真实 MySQL、对象存储、Git/manifest 备份→恢复演练，目标 RPO ≤15 分钟、RTO ≤4 小时。
- v1/v2 对账无未解释差异，弃用清单和至少两个版本的兼容承诺已发布。
- 运维手册、安全手册、Adapter SDK、API 和迁移文档完成。

G6 技术候选已于 2026-08-04 完成：冻结 contract `2.0.0-ga`，真实 Hermes traffic 得到 SLO `HEALTHY`（81 samples / 0 errors；ingest `4.634ms`、timeline `9.311ms`、policy `49.421ms`），GA Readiness `14 PASS / 0 WARN / 0 BLOCK = READY`，真实恢复演练 RPO `0s` / RTO `1s`，v1/v2 unexplained difference `0`；详见 [E08 evidence](../specs/014-observability-ops/evidence/observability-ops-e08-20260804.md) 与 [G6 evidence](../specs/015-ga-candidate/evidence/ga-candidate-e09-20260804.md)。

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
| 正式支持的接入路径 | 0 个完整执行级路径 | OpenClaw、Hermes Reporter、Generic OTLP、Pack/ATIF 兼容 |
| 关键 Agent owner/fallback owner 覆盖率 | 待盘点 | 100% 试点范围 |
| Release rollback 用时 | 无统一流程 | ≤5 分钟 |
| Evidence ingest accepted p95 | 16.052～17.322ms（Run-control ASGI + real MySQL，不含边缘网络） | <200ms |
| 30 天 Run 时间线 p95 | 无基线 | <2s |
| 未解释的 v1/v2 对账差异 | 0（G1 隔离与 live lane） | 0 |
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
| R-009 | 历史 EvidenceItem 可能缺少可信 typed WorkTrace 关系，NULL tenant 无法解析 | 中 | 高 | 全量审计仍出现无 `work_trace_id` 或目标 unresolved/conflict 的 Evidence | 当前开发库 275 条记录已被 Owner 判定为可丢弃测试数据并在完整备份后精确清理；0029 preflight 对未来目标库继续 fail-closed，生产数据仍只允许 typed evidence/逐 ID manifest | Data Architecture | 当前开发环境已关闭 | 2026-08-09 |

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
| D-017 | 2026-07-28 | 批准 S1-D 按“显式历史补链 → contract preflight → 零阻塞后 contract DDL”实施 | 目标开发库 275 条历史记录无可用 typed Namespace 证据，必须提供不依赖弱推断的人工处置路径 | 采用 ADR-0212 逐 ID manifest；放行 FND-014 与 remediation/preflight 实现；FND-019 DDL 仍以保留的零阻塞报告为执行前提 | Product/Architecture Owner | 已批准实施 | Owner 指令“按照你的建议制定开发计划并执行”；specs/009 |
| D-018 | 2026-07-28 | 将当前 275 条无可信归属记录认定为可丢弃的旧开发测试数据，允许在完整备份和写静默后精确清理 | 数据无可靠 typed evidence，强行映射会制造虚假治理事实；用户明确授权清理旧测试数据 | 保留已校验全库备份与 SHA-256；按五张表精确删除并记录 FK cascade；零 blocker 后才应用 0029；此授权不适用于生产 | Product/Architecture Owner | 已实施 | Owner 指令“旧有的测试数据可以清理掉”；zero-blocker report；revision 0029 |
| D-019 | 2026-07-30 | Hermes 使用原生 `hermes-reporter` Adapter profile，同时复用共享 ReporterCredential、Session、Run、Outbox 和幂等模型 | Fleet 需要准确区分产品适配器，但不得为产品复制一套治理对象 | Runtime provider 暂保持 `custom`；pilot 只认证 DD-C1，不提前声明 OTLP/durable replay | Product/Architecture Owner + Integration Architecture | 已实施 | Owner 指令“按照你的建议开整”；conformance v0.3；revision 0039；真实 Hermes cron |
| D-020 | 2026-07-31 | 正式批准 M2/G2 Runtime Beta 与 M3/G3 Eval Beta，并继续 Trace2Dataset 数据飞轮 | RT-08 与 EH-06 已有完整技术证据、真实 Provider/MySQL/HTTP/浏览器验证和全量回归 | 关闭 S4～S6、E02、E03；已验收加权进度由 46.0% 提升至 50.0%；下一交付保持 Langfuse 原始内容与 DuckDock 治理元数据解耦 | Product/Architecture Owner | 已批准并执行 | Owner 明确回复“批准，继续继续”；G2/G3 evidence packages |
| D-021 | 2026-08-04 | 按既定建议完成 E06～E09 技术范围，再进入人工验证与合理性讨论 | 用户要求先完成总目标并进行完整本地 dev 验证；Langfuse 必须继续保持 adapter 解耦 | 关闭 S9～S11/E06～E09 技术范围；冻结 v2 contract，v1 在 2.x 保持兼容；100% 仅表示技术范围完成，不替代人工产品验收或生产发布审批 | Product/Architecture Owner + Codex | 已执行，待人工产品复核 | Owner 连续批准继续并明确要求“先按照既定计划完成这个总目标然后我们再人工验证”；E06～E09 evidence |

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
| 2026-07-28 | 1.0 | 执行 S1-D 安全边界：建立逐 ID tenant remediation manifest、原子审计 apply/replay、完整正反向关系校验与只读 contract preflight；修复 backfill CLI 连接池退出；保留 275 blocker，未提前增加 contract DDL | Codex | backend 729 passed、11 skipped，目标覆盖率 77.64%；MySQL 11 passed；frontend 34 passed + lint/build；Ruff/compile/import clean；mypy 82≤82；Alembic 0028/check clean；Compose healthy |
| 2026-07-28 | 1.1 | 完成 EF-04/S1-D：备份后精确清理 275 条旧开发测试数据，保留零 blocker 证据；新增并应用 revision 0029 tenant contract，五表 non-null、四个 tenant-scoped indexes 与 Binding tenant-key unique 生效 | Codex | backend 733 passed、12 skipped，目标覆盖率 77.64%；MySQL 12 passed；Foundation 95 passed；frontend 34 passed + lint/build；Ruff/compile/import clean；mypy 82≤82；Alembic 0029/check clean；Compose healthy |
| 2026-07-28 | 1.2 | 完成 EF-05：新增不可变 AgentDeployment/DeploymentComponent inventory、生命周期服务、Namespace RBAC 管理 API、严格 metadata-only schema 与 revision 0030 | Codex | backend 747 passed、13 skipped，目标覆盖率 77.64%；MySQL 13 passed；Foundation 109 passed、9 skipped；frontend 34 passed + lint/build；Ruff/compile/import clean；mypy 82≤82；空库/开发库 Alembic 0030/check clean；Compose healthy |
| 2026-07-28 | 1.3 | 完成 EF-06：新增 metadata-only AgentSession/AgentRun、strict envelope、canonical JSON/SHA-256、幂等 start/complete 与终态竞争保护，应用 revision 0031 | Codex | backend 769 passed、14 skipped，目标覆盖率 77.64%；MySQL 14 passed；Foundation 131 passed、10 skipped；Ruff/compile/import clean；mypy 82≤82；空库/开发库 Alembic 0031/check clean；Compose healthy |
| 2026-07-28 | 1.4 | 完成 EF-07：开放 Reporter/management API v2，以 `execution.write` ReporterCredential 派生 Namespace/Runtime/Trust；补齐 credential-scoped complete、强制时间窗、签名 cursor、跨租户非枚举读取与拒绝安全审计 | Codex | backend 776 passed、14 skipped，核心覆盖率 80.10%；MySQL 14 passed；Foundation 136 passed、10 skipped；frontend 34 passed + lint/build；Ruff/compile/import clean；mypy 82≤82；Alembic 0031/check clean；Compose healthy |
| 2026-07-28 | 1.5 | 完成 EF-08：新增 immutable AgentRunArtifact、TelemetrySink、TraceBackendRef、provider-neutral ports/null adapters、Namespace RBAC 管理 API 与 provider failure isolation，应用 revision 0032 | Codex | backend 794 passed、14 skipped，核心覆盖率 80.10%；MySQL 14 passed；Foundation 153 passed、10 skipped；frontend 34 passed + lint/build；Ruff/compile/import clean；mypy 82≤82；Alembic 0032/check clean；Compose ready |
| 2026-07-28 | 1.6 | 完成 EF-09：Session/Run/Artifact 原子写入 Transactional Outbox；新增安全事件 builder、MySQL lease dispatcher、稳定 event ID、退避/FAILED、管理员 health/retry 与 Beat/Worker 调度，应用 revision 0033 | Codex | backend 806 passed、15 skipped，核心覆盖率 80.10%；MySQL 15 passed；Foundation 164 passed、11 skipped；frontend 34 passed + lint/build；Ruff/compile/import clean；mypy 82≤82；Alembic 0033/check clean；Compose ready，12 条 v2 paths |
| 2026-07-28 | 1.7 | 完成 EF-10/Foundation：新增 candidate + Deployment revision 精确 Release evidence seam，保持当前 Clinic Gate/WorkTrace 兼容；增加 ingestion switch/Runtime allowlist，完成 dispatcher 与应用 backout drill并形成 G1 Evidence Alpha 候选包 | Codex | backend 815 passed、15 skipped，核心覆盖率 80.10%；MySQL 15 passed；Foundation 172 passed、11 skipped；frontend 34 passed + lint/build；Ruff/compile/import clean；mypy 82≤82；Alembic 0033/check clean；Compose ready |
| 2026-07-28 | 1.8 | 完成 G1 技术验收：新增安全可重复的 Run-control load gate、Reporter 热路径优化、v1 Structured Report→v2 typed Run 对账 projection/receipt、管理员 reconciliation API 与 live Worker smoke；正式 Gate 批准保持待 Owner 记录 | Codex | backend 829 passed、16 skipped，核心覆盖率 80.58%；MySQL 16 passed；Foundation 184 passed、12 skipped；三次 100/s 持续 + 500-request 短突发通过；frontend 34 passed + lint/build；Ruff/compile/import clean；mypy 82≤82；Alembic 0034/check clean；Compose ready，14 条 v2 paths |
| 2026-07-30 | 1.9 | Product/Architecture Owner 正式批准 M1/G1；关闭 S2，启动 S3/NEXT-001 OpenClaw Shadow Telemetry | Product/Architecture Owner + Codex | Owner 回复“确认，继续。”；G1 Evidence Alpha 技术验收包；S3 状态切换为进行中 |
| 2026-07-30 | 2.0 | 完成 S3/RT-01/NEXT-001：新增 pinned OpenClaw edge Collector，OTLP HTTP/gRPC Basic Auth、服务端可信身份覆盖、最小 normalizer、metadata-only fail-closed allowlist、Secret Canary fixture/smoke 与可选 telemetry profile | Codex | backend 834 passed、16 skipped；Collector config validate PASS；29 focused tests；unauthenticated=401、authenticated=200；Secret Canary/forged identity absent；默认 7 服务不变，telemetry profile live |
| 2026-07-30 | 2.1 | 完成 S3/RT-02～04/NEXT-002：Runtime MCP 显式 Session/Run lifecycle correlation；Collector 0.157.0 GenAI normalizer 对齐 OTel schema 1.40.0；OpenInference/直接 OTel GenAI golden 等价；新增可选 Langfuse v4 exporter overlay 与 bounded confirmation adapter | Codex | backend 845 passed、16 skipped；Runtime MCP 10 passed；32 focused tests；两套 live smoke PASS；401/200；Langfuse Basic Auth/v4 header/path/safe payload PASS；默认 7 服务不变，debug telemetry profile live |
| 2026-07-30 | 2.2 | 完成 S3/RT-05：Runtime MCP v0.3.0 metadata-only SQLite WAL、稳定序号/幂等键、依赖 public ID 解析、持久 ack cursor、容量信号与显式 loss marker；Langfuse exporter 改为 bounded file-storage WAL，并通过强制 KILL 后无第二次 producer 请求重放 | Codex | backend 845 passed、16 skipped；Runtime MCP 12 passed；18 focused tests；Collector overlay validate PASS；WAL persisted/restart replay/credential absent PASS；Langfuse protocol smoke PASS；默认 7 服务不变 |
| 2026-07-30 | 2.3 | 完成 S4/RT-06：Generic OTLP per-Runtime 可信 bridge、ReporterCredential-derived metadata projection、revision 0035 mapping/quarantine、独立 durable provider/DuckDock 双出口与 DD-C2 conformance | Codex | backend 859 passed、16 skipped；40 focused tests；Collector validate/live smoke PASS；401/200；OTL-001～009/CONF-C2-001～009；provider outage queue/recovery 无第二次 producer request；默认 7 服务不变 |
| 2026-07-30 | 2.4 | 完成 S4/RT-07：ReporterCredential-derived Pack/ATIF import、revision 0036 六态 FSM、严格归档/manifest/payload preflight、existing-Run mapping、content-addressed MinIO 与 PAT-001～012 | Codex | backend 877 passed、17 skipped；Foundation 212 passed、13 skipped；40 focused + live MinIO PASS；MySQL 0036 downgrade/upgrade/check clean；明确不提前声明 multipart/full DD-C3 |
| 2026-07-30 | 2.5 | 完成 RT-08 技术实现并提交 G2 待验收包：三 profile 共享 DD-C0、动态 capability snapshot、heartbeat/history/Collector/config drift、Fleet API/UI 与 revision 0037 | Codex | backend 890 passed、18 skipped；Foundation 224 passed、14 skipped；frontend 35 passed；focused 61 + real MySQL 1 + live MinIO 1 PASS；0037 downgrade/upgrade/check clean；G2 Owner approval pending |
| 2026-07-30 | 2.6 | 完成 NEXT-003：ATIF Pack export、MinIO resumable multipart、credential-scoped durable batch receipt/contiguous cursor、size/SHA-verified evaluation Outbox replay 与 Pack DD-C3 动态协商 | Codex | backend 895 passed、19 skipped；Foundation 229 passed、15 skipped；frontend 35 passed；真实 MinIO multipart PASS；0038 downgrade/upgrade/check clean；mypy 82=baseline；G2 Owner approval仍待明确记录 |
| 2026-07-30 | 2.7 | 完成 Hermes 原生 Adapter/Fleet 与真实 agent loop：`hermes-reporter`、0.2.0 handshake/heartbeat、metadata-only Session/Run、structured/pack 自动生命周期；修复结构化报告 MySQL 幂等 replay 的 Outbox timestamp conflict；应用 revision 0039 | Codex | backend 901 passed、19 skipped；frontend 35 passed + lint/build；Ruff clean；mypy 82=baseline；0039 upgrade/downgrade/upgrade/check clean；本地真实 Hermes cron SUCCEEDED，DD-C1、服务端 duration、失败终态与资产不重复均验证 |
| 2026-07-31 | 2.8 | 升级自托管 Langfuse v4/ClickHouse 25.12，完成 EH-01/NEXT-004：Langfuse-first Dataset/Evaluator/Experiment/Evaluation 治理索引、不可变版本、真实 provider sync、DeepEval 可选端口与 API v2；应用 revision 0040 | Codex | backend 909 passed、19 skipped；53 focused tests；Ruff/compile/OpenAPI clean；0040 downgrade/upgrade/check clean；真实 Clinic SDK、OTLP、Observations v2、Dataset sync PASS；Langfuse Web/Worker healthy |
| 2026-07-31 | 2.9 | 完成 EH-02：Langfuse-backed Evaluation 通过 Outbox/Beat 调度、数据库 lease/heartbeat/过期接管、稳定 execution key、指数退避、取消和人工 retry 可恢复执行；高层 Experiment runner 固定 Dataset version 并仅回写有界摘要；应用 revision 0041 | Codex | backend 913 passed、19 skipped；EH-02 focused 32 passed；Ruff/compile/OpenAPI clean；隔离真实 MySQL 0041→0040→0041/check clean；真实 Langfuse 2-item run=`COMPLETED/0.5/1 passed/1 failed/attempts 1`；backend/frontend/Langfuse healthy |
| 2026-07-31 | 3.0 | 完成 EH-03：新增不可变 provider-hosted `EvaluationResultManifest`、显式 `COMPLETE/PARTIAL` 与 expected/processed/scored/pass/fail/error 计数，部分结果可重试且保留历史清单；结果 API/Outbox 均为低敏字段白名单；应用 revision 0042 | Codex | backend 914 passed、19 skipped；EH-03 focused 33 passed；Ruff/compile/OpenAPI clean；隔离真实 MySQL 0041→0042→0041→0042 与旧 FAILED 计数回填通过；真实 Langfuse Experiment `94d84315e03526ac` 的 Public API 2-item `[0,1]` 重算 digest 与 manifest 一致；开发库 head 0042/check clean；backend/Langfuse healthy |
| 2026-07-31 | 3.1 | 完成 EH-04：新增不可变 RegressionPolicyVersion 与 EvaluationComparison，显式绑定 baseline/candidate Evaluation+manifest 和 policy version，校验同 DatasetVersion/EvaluatorVersion/provider Dataset/result schema，输出 PASS/REGRESSION/INCONCLUSIVE 与复现 digest；应用 revision 0043 | Codex | backend 919 passed、19 skipped；focused 47 passed、1 skipped；Ruff/compile/OpenAPI clean；隔离真实 MySQL 0042→0043→0042→0043、score backfill 与 populated downgrade guard 通过；真实 Langfuse 两个 run 的 2 个 item/`[0,1]` 完全匹配，comparison=`PASS` 且独立 digest 复算一致；开发库 head 0043/check clean |
| 2026-07-31 | 3.2 | 完成 EH-05/NEXT-005：新增不可变 ReleaseCandidateEvaluationBinding，将精确 candidate/Deployment revision/config snapshot 绑定 EvaluationComparison；绑定后冻结 REGISTERED Deployment；新增 exact-selector Candidate Release Gate，缺失、REGRESSION、INCONCLUSIVE 均 fail closed；应用 revision 0044 | Codex | backend 924 passed、19 skipped；EH-04/EH-05 focused 20 passed；Ruff/compile/OpenAPI clean；隔离真实 MySQL full-chain、0043→0044→0043→0044 与 populated downgrade guard 通过；真实 Langfuse comparison 绑定后 Gate=`PASS`，未绑定 candidate=`BLOCKED`，decision digest 独立复算一致；Outbox PUBLISHED；开发库 head 0044/check clean |
| 2026-07-31 | 3.3 | 完成 EH-06/NEXT-006 技术实现：新增每个精确 ReleaseCandidateEvaluationBinding 一次性的不可变人工评审、拒绝 fail-closed Gate 语义、评审 API/OpenAPI、Eval Hub 总览/基线对比/候选门禁 UI；应用 revision 0045，提交 G3 技术验收包 | Codex | backend 927 passed、19 skipped；frontend 37 passed、build/lint 0 error；Ruff/compile clean；隔离真实 MySQL full-chain、0044→0045→0044→0045、populated downgrade guard 与 check clean；真实 HTTP APPROVED review 后 Gate=`PASS`/2 evidence，Outbox PUBLISHED；本地真实浏览器三页签与 Gate 复算通过；正式 G3 Owner approval pending |
| 2026-07-31 | 3.4 | Product/Architecture Owner 正式批准 M2/G2 与 M3/G3；关闭 S4～S6、E02、E03并启动 Trace2Dataset 数据飞轮 | Product/Architecture Owner + Codex | Owner 回复“批准，继续继续”；G2/G3 技术证据包；已验收加权进度更新为 50.0% |
| 2026-07-31 | 3.5 | 完成 NEXT-007 Trace2Dataset 首个数据飞轮闭环：Langfuse Observation 原始 IO 在 provider adapter 内直写 Dataset，DuckDock 只记录不可变 materialization/version/manifest 治理元数据；应用 revision 0046并扩展 Langfuse 升级门禁 | Codex | backend 937 passed、19 skipped；frontend 38 passed、lint 0 error、build PASS；mypy 82=baseline；隔离 MySQL round trip/populated guard/check PASS；真实 Langfuse item/source/pin、幂等重放、Outbox PUBLISHED 与本地浏览器验证 PASS；[evidence](../specs/009-evaluation-hub/evidence/eval-hub-trace2dataset-20260731.md) |
| 2026-07-31 | 3.6 | 完成 NEXT-008 受治理 Trace2Dataset 策展队列：metadata-only 候选过滤、最多 20 项不可变选择批次、独立 APPROVED/REJECTED 评审、批准批次一次生成一个 DatasetVersion；应用 revision 0047并扩展 Langfuse 升级门禁 | Codex | backend 943 passed、19 skipped；frontend 40 passed、lint 0 error、build PASS；mypy 82=baseline；隔离 MySQL empty round trip/populated guard/check PASS；真实 Langfuse 三条 Observation、浏览器批量审批/同键重试、确定性 items、Outbox/Audit content-free PASS；[evidence](../specs/009-evaluation-hub/evidence/eval-hub-curation-queue-20260731.md) |
| 2026-07-31 | 3.7 | 完成 NEXT-009 可复现生产采样：Namespace 策略与不可变版本、显式 Dataset/时间窗口、stable-hash 排序、最低样本 fail-closed、已治理来源强制排除，并自动生成现有待审批策展批次；应用 revision 0048 | Codex | backend 947 passed、19 skipped；frontend 42 passed、lint 0 error、build PASS；mypy 82=baseline；隔离 MySQL empty round trip/populated guard/check PASS；真实 Langfuse 五条 Observation、浏览器 5 选 3、三条后续 `已治理`、Outbox PUBLISHED/Audit content-free PASS；[evidence](../specs/009-evaluation-hub/evidence/eval-hub-sampling-policy-20260731.md) |
| 2026-08-03 | 3.8 | 完成 NEXT-010 解耦 Langfuse Annotation Queue：绑定现有 Queue/Score Config 快照、持久 dispatch intent、lease/backoff Worker、先对账后创建、人工 retry/reconcile 与 Eval Hub UI；应用 revision 0049并把队列协议加入 Langfuse 升级门禁 | Codex | backend 951 passed、19 skipped；frontend 43 passed、lint 0 error、build PASS；mypy 82=baseline；隔离 MySQL empty round trip/populated guard/check PASS；真实 Hermes 三条 Observation 的 UI 派发、重复重放、SYNCED/COMPLETED 3/3、DuckDock 批次仍 PENDING_REVIEW、Outbox PUBLISHED/Audit content-free PASS；[evidence](../specs/009-evaluation-hub/evidence/eval-hub-annotation-queue-20260803.md) |
| 2026-08-03 | 3.9 | 完成 NEXT-011 标注驱动 Promotion 推荐：Namespace 策略身份、不可变规则版本、精确 Queue/Score Config/type pin、Scores API v3 质量证据、metadata-only 多样性和 fail-closed `RECOMMENDED/BLOCKED`；应用 revision 0050并把 Promotion lane 加入 Langfuse 升级门禁 | Codex | backend 955 passed、19 skipped；frontend 44 passed、lint 0 error、build PASS；mypy 82=baseline；隔离 MySQL empty/populated downgrade guard/check PASS；真实 Hermes 三条 Observation 在浏览器验证 `BLOCKED/insufficient_diversity` 与 `RECOMMENDED/promotion_criteria_met`，批次仍 PENDING_REVIEW/未物化，Outbox/Audit content-free PASS；[evidence](../specs/009-evaluation-hub/evidence/eval-hub-promotion-policy-20260803.md) |
| 2026-08-03 | 3.10 | 完成 NEXT-012 跨批次 Golden/Bad Case 路由：不可变策略版本精确 pin 一个带桶 Promotion 版本，跨 1～20 个不重叠运行以稳定 cluster round-robin 选择，minimum 不足原子 BLOCKED，ROUTED 只生成两个独立 PENDING_REVIEW 批次；应用 revision 0051 | Codex | backend 959 passed、19 skipped；frontend 45 passed、lint 0 error/9 existing warnings、build PASS；mypy 82=baseline；隔离 MySQL 0050→0051→0050→0051、check 与 populated guard PASS；真实 Hermes 两批六条路由 Golden 4/2 clusters、Bad Case 2/1 cluster，两个批次均无 review/materialization；Langfuse v4 real gate 54 passed + provider lanes PASS；[evidence](../specs/009-evaluation-hub/evidence/eval-hub-case-routing-20260803.md) |
| 2026-08-03 | 3.11 | 完成 NEXT-013 版本化 failure taxonomy/Experience candidate：不可变规则版本精确 pin Case Routing 版本，只消费 selected Bad Case lane/digest/ref，按显式阈值分类 CROSS_RUN_RECURRING/SINGLE_RUN_RECURRING/ISOLATED，isolated 默认排除；提取只生成 PENDING_REVIEW 候选，单次不可变评审不触发任何生产 Agent 变更；应用 revision 0052 | Codex | backend 963 passed、19 skipped；frontend 46 passed、lint 0 error/9 existing warnings、build PASS；新表面定向 mypy 0；隔离 MySQL full-chain、0051→0052→0051→0052、check 与 populated guard PASS；真实浏览器从 Hermes 路由的 2 个 Bad Cases/1 cluster 提取 1 个 SINGLE_RUN_RECURRING 并批准，2 条精确 lineage、Outbox/Audit content-free；Langfuse v4 real gate 57 passed；[evidence](../specs/009-evaluation-hub/evidence/eval-hub-failure-taxonomy-experience-20260803.md) |
| 2026-08-03 | 3.12 | 完成 NEXT-014 Experience 资产与独立激活：已批准候选单次认领、人工正文不可变版本、一次激活申请、作者/请求人隔离的最终审批、新 ACTIVE 原子退役旧版本；激活仅为 DuckDock 控制面状态，不写 Langfuse/Hermes/Prompt/Skill/Memory/Deployment；应用 revision 0053 | Codex | backend 966 passed、19 skipped；frontend 47 passed、lint 0 error/9 existing warnings、build PASS；Ruff clean、mypy 82=baseline；开发 MySQL 0052→0053/head/check clean；真实 HTTP 同人审批 409、第二人批准 ACTIVE，本地浏览器面板/资产/ACTIVE 可见；三类 Outbox 与四类 Audit 齐全且正文/申请说明泄露计数 0；Langfuse v4 real gate 57 passed；[evidence](../specs/009-evaluation-hub/evidence/eval-hub-experience-asset-activation-20260803.md) |
| 2026-08-03 | 3.13 | 完成 NEXT-015 真实语义失败聚类：不可变版本精确 pin Case Routing 与 embedding profile/model/dimensions/threshold，Langfuse IO 和向量只在 adapter 内瞬时使用，确定性 cosine/centroid 只落引用、相似度与 digest；Failure Taxonomy/Experience 精确 pin 语义版本/运行并保留 metadata 兼容路径；应用 revision 0054 | Codex | backend 969 passed、19 skipped；frontend 47 passed、lint 0 error/9 existing warnings、build PASS；定向 Ruff/mypy clean；开发 MySQL head=0054/check clean；真实 Langfuse + 本地 Hermes BGE 把 2 个 Bad Cases 聚为 1 个合格簇并提取 1 个语义候选；真实浏览器链路与 console 0 error；Langfuse v4 real gate 59 passed；[evidence](../specs/009-evaluation-hub/evidence/eval-hub-semantic-failure-clustering-20260803.md) |
| 2026-08-03 | 3.14 | 完成 NEXT-016 语义簇质量/漂移门禁：不可变策略版本冻结四项阈值，精确比较同一 Case Routing 与同一 source/content digest 集的 baseline/candidate 聚类，输出 PASS/DRIFTED/INCONCLUSIVE、原因码和复现摘要；门禁阶段不调用 Langfuse/Hermes/embedding；应用 revision 0055 | Codex | backend 971 passed、19 skipped；frontend 48 passed、lint 0 error/9 existing warnings、build PASS；Ruff/compile/mypy clean；开发 MySQL head=0055/check clean；真实 Hermes 稳定候选 agreement=1.0/PASS，漂移候选 agreement=0.0、cluster change=0.5、eligible drop=1.0/DRIFTED；Langfuse v4 real gate 60 passed；[evidence](../specs/009-evaluation-hub/evidence/eval-hub-semantic-regression-20260803.md) |
| 2026-08-04 | 3.15 | 完成 NEXT-017 定时语义漂移监控：监控精确冻结 baseline、候选聚类版本、回归阈值和周期；Beat due discovery、Worker lease/retry 重跑同源证据并生成不可变 comparison；PASS 静默，DRIFTED/INCONCLUSIVE/终态失败形成站内告警，支持一次性确认及 pause/resume/run-now；应用 revision 0056 | Codex | backend 973 passed、19 skipped；frontend 49 passed、lint 0 error/9 existing warnings、build PASS；Ruff/compile/import/mypy clean；开发 MySQL head=0056/check clean；真实 Beat+Worker+本地 Hermes 稳定监控 COMPLETED/PASS 且无告警，漂移监控 COMPLETED/DRIFTED 并形成 CRITICAL→ACKNOWLEDGED；Outbox 全部 PUBLISHED、确认备注 Audit/Outbox 泄露 0；真实浏览器页面闭环通过；Langfuse v4 real gate 60 passed；[evidence](../specs/009-evaluation-hub/evidence/eval-hub-semantic-monitor-20260804.md) |
| 2026-08-04 | 3.16 | 完成 E04 Agent Package Registry v2：Namespace Package/不可变版本、严格 Manifest 与组件 DAG、CycloneDX/SPDX MinIO SBOM、Ed25519 公钥信任/吊销、provenance、全证据复验、内容安全 Audit/Outbox、Deployment 精确 PackageVersion pin 与 `/packages` UI；应用 revision 0057 | Codex | backend 980 passed、19 skipped；frontend 50 passed、lint 0 error/9 existing warnings、build PASS；mypy 82/82；Langfuse v4 real gate 60 passed；开发 MySQL head=0057/check clean；真实 HTTP 外部签名、201/201 幂等 replay、MinIO 下载 digest、ACTIVE Hermes Deployment pin 与浏览器证据复验通过；修复 MySQL commit-visibility 幂等竞态和 SBOM 误删风险；[E04 evidence](../specs/010-package-registry-v2/evidence/package-registry-v2-20260804.md) |
| 2026-08-04 | 3.17 | 完成 E05 Release Control：Environment、不可变 Candidate、版本化 Policy/决策、四眼审批/限时例外、Promotion、最小权限 Runtime receipt credential、Canary 自动回滚、激活历史与 `/release-control` UI；应用 revisions 0058/0059 | Codex | backend 986 passed、19 skipped；frontend 52 passed、lint 0 error/9 existing warnings、build PASS；Ruff clean；开发 MySQL head=0059/check clean；隔离 MySQL full upgrade 与 populated downgrade guard PASS；真实本地 Hermes baseline Promotion/APPLIED receipt，以及失败 Canary→自动 Rollback→Runtime APPLIED→环境恢复闭环通过；浏览器 console 0 error；[G4 evidence](../specs/011-release-control/evidence/release-control-g4-20260804.md) |
| 2026-08-04 | 3.18 | 完成 E06 Handover 2.0：append-only evidence snapshot、确定性依赖图/readiness、义务回执、接收人 acceptance 与外部 Ed25519 signed package；应用 revision 0060 | Codex | 真实 Hermes release evidence、MySQL/MinIO readback、blocked obligation、签名独立复算、幂等 replay 和浏览器 Handover detail 通过；[G5 evidence](../specs/012-handover-2/evidence/handover-2-g5-20260804.md) |
| 2026-08-04 | 3.19 | 完成 E07 Identity/Security：provider-bound SCIM credential、目录 disable 原子 offboarding、DEVICE/SERVICE workload identity、key rotation 与 content-safe Audit/Outbox；应用 revision 0061 | Codex | 真实 Hermes Runtime #9：停用前 heartbeat 200，停用后登录/旧 credential 401；自动 Handover case、rotation sequence 2；59 focused tests；[E07 evidence](../specs/013-identity-security/evidence/identity-security-e07-20260804.md) |
| 2026-08-04 | 3.20 | 完成 E08 Observability/Ops：content-safe route metrics、GA SLO、incident lifecycle、Prometheus profile/rules 与真实 MySQL+MinIO backup→delete→restore；应用 revision 0062 | Codex | RecoveryDrill PASSED，RPO=0s/RTO=1s；Prometheus target/rules healthy；`/operations` 浏览器通过；精确修复 55 个旧无效 E2E 邮箱且保留业务关系；[E08 evidence](../specs/014-observability-ops/evidence/observability-ops-e08-20260804.md) |
| 2026-08-04 | 3.21 | 完成 E09/G6 技术候选：冻结 OpenAPI v2 `2.0.0-ga`、v1 2.x compatibility headers、14 项实时 GA Readiness 与 Operations UI；用真实 Hermes/credential/heartbeat/timeline/policy traffic 完成最终门禁 | Codex | GA=`READY`（14/14 PASS），SLO=`HEALTHY`（81 samples/0 errors），contract SHA-256 `60146a…6357`；backend 1005 passed/19 skipped，frontend 53 passed、lint 0 error、build PASS，Ruff/Alembic/OpenAPI drift clean；[G6 evidence](../specs/015-ga-candidate/evidence/ga-candidate-e09-20260804.md) |
| 2026-08-06 | 3.22 | 完成 PGA-09 最终发布供应链 FOUNDATION：签名 tag/full-gate GHCR 候选→全扫描→原始 SARIF 留存→拒绝覆盖 final tag 路径、独立 builder 信任策略和签名 report、tag/source archive、SLSA v1、SPDX 2.3、SARIF/漏洞统计复验，并把 provenance 引用绑定四方审批摘要 | Codex | backend 1182 passed/19 skipped；生产授权专项 82 passed；生产基线 21/21 PASS；Ruff/compile/YAML/JSON clean；真实 `v2.0.0` tag/registry bundle 仍待发布机构执行；[evidence](../specs/016-ga-production-authorization/evidence/release-provenance-gate-20260806.md) |
| 2026-08-06 | 3.23 | 完成 PGA-08 四方签字活动闭环：审批人不再签人工复制 digest，而是各自对同一空 approvals base 重跑权威 preflight、核对 policy role/key、签署并当场反向验签；finalizer 只在四份 entry 使持久化授权文件达到 `GA_AUTHORIZED` 时输出不可覆盖授权与 receipt | Codex | backend 1188 passed/19 skipped；生产授权专项 87 passed；生产运维专项 40 passed；生产基线 22/22 PASS；Ruff/compile/CLI/diff clean；真实人员决策未执行；[evidence](../specs/016-ga-production-authorization/evidence/approval-campaign-gate-20260806.md) |
| 2026-08-06 | 3.24 | 完成 `GA_AUTHORIZED` 归档闭环：只沿允许根内显式内容摘要/签名引用和 supplemental 清单收集，前后两次重验权威门禁，拒绝符号链接、私钥材料、越界/变更/超限文件和覆盖输出；确定性 tar.gz、外部 manifest、SHA-256 sidecar 写盘后逐成员复验且不扫描/收集邻近私钥 | Codex | backend 1192 passed/19 skipped；生产授权专项 91 passed；生产运维专项 40 passed；生产基线 23/23 PASS；mypy 81≤82；Ruff/compile/CLI/diff clean；真实授权 bundle 仍待发布活动产生；[evidence](../specs/016-ga-production-authorization/evidence/authorized-archive-gate-20260806.md) |
| 2026-08-06 | 3.25 | 完成 GA 授权归档可搬运复验：manifest v2 固定每条原始引用到内容寻址成员，归档前 strict closure preflight；接收方先验证独立摘要、detached/embedded manifest、成员/index，再在删除全部原主机证据后以临时隔离目录和 no-host-fallback 路径重映射重跑完整 evaluator；同时把 backup trust store 纳入内容摘要 | Codex | backend 1199 passed/19 skipped；生产授权专项 98 passed；恢复 collector 6 passed；生产运维专项 40 passed；生产基线 24/24 PASS；mypy 81≤82；真实 bundle/digest 仍待发布机构产生与外部发布；[evidence](../specs/016-ga-production-authorization/evidence/portable-authorized-archive-verification-20260806.md) |
| 2026-08-06 | 3.26 | 完成 GA 审批活动冻结：不可覆盖、限时 campaign freeze 绑定空 approvals base、发布机构 policy、release digest 和窗口；四方签名 statement/entry/preflight 与 finalizer receipt 全部绑定同一 campaign/freeze 摘要，最终授权内容寻址 freeze/base，权威 evaluator 和 finalizer 双重拒绝跨轮混签、绕过组装、过期与窗口外签字 | Codex | backend 1205 passed/19 skipped；生产授权专项 104 passed；生产运维专项 40 passed；生产基线 24/24 PASS；mypy 81≤82；Ruff/compile/CLI/YAML/JSON/diff clean；真实四方决策仍待外部执行；[evidence](../specs/016-ga-production-authorization/evidence/approval-campaign-freeze-20260806.md) |
| 2026-08-06 | 3.27 | 完成 GA 预审批证据自动组装：版本化 request 只声明最终 release/target 与九份 evidence 路径，approval policy 必须独立传入；组装器从 wrapper/签名容量原始回执自动投影完整 control 和摘要，内存与落盘后两次要求 `APPROVAL_COLLECTION`，拒绝缺项、跨目标、输入变化与覆盖 | Codex | backend 1208 passed/19 skipped；生产授权专项 107 passed；生产运维专项 40 passed；生产基线 25/25 PASS；mypy 81≤82；Ruff/compile/CLI/YAML/JSON/diff clean；真实目标输入仍待外部执行；[evidence](../specs/016-ga-production-authorization/evidence/preapproval-assembly-gate-20260806.md) |
| 2026-08-06 | 3.28 | 关闭九份 GA trust policy 之间的全局职责分离缺口：新增内容寻址 manifest 和不可覆盖预检回执，在真实演练前拒绝 identity/公钥跨职责复用；最终授权器从证据策略独立重算同一不变量并作为 `FOUNDATION` 门禁，缺失证据仍保持在 `EVIDENCE_COLLECTION` | Codex | backend 1211 passed/19 skipped；生产授权专项 110 passed；生产运维专项 40 passed；frontend 56 passed/lint/build clean；生产基线 26/26 PASS；Ruff/compile/CLI/YAML/JSON/diff clean；真实组织身份、公钥与目标执行仍待发布机构提供；[evidence](../specs/016-ga-production-authorization/evidence/trust-topology-gate-20260806.md) |
| 2026-08-06 | 3.29 | 完成真实 GA 验收执行 campaign：不可覆盖计划绑定最终 release/target、独立复验的九策略 topology、最长 14 天窗口和全新 evidence root；验证 12-phase DAG、破坏性恢复边界、精确安全 acknowledgement 和当前 67 个唯一预期 artifact（含 tag/source/SLSA/SPDX/SARIF/scan/closure），全部保持 `PENDING_EXTERNAL_EVIDENCE`，并自动生成后续 preapproval request | Codex | backend 1215 passed/19 skipped；生产授权专项 114 passed；生产运维专项 40 passed；frontend 56 passed/lint/build clean；生产基线 27/27 PASS；mypy 81≤82；Ruff/compile/CLI/YAML/JSON/diff clean；真实 release authority 审阅和外部执行仍待完成；[evidence](../specs/016-ga-production-authorization/evidence/execution-campaign-gate-20260806.md) |
| 2026-08-06 | 3.30 | 完成 GA execution campaign 实际证据闭环：计划预先内容寻址 backup trust 并分配 67 个产物；closure 只在窗口内接受 64 个精确外部路径，递归引用只能落到计划产物或 topology/campaign 的内容寻址输入；随后同一事务组装空审批底稿、assembly receipt 和非授权 closure，失败回滚且拒绝覆盖 | Codex | backend 1221 passed/19 skipped；生产授权专项 120 passed；生产运维专项 40 passed；frontend 56 passed/lint/build clean；生产基线 28/28 PASS；mypy 81≤82；Ruff/compile/CLI/YAML/JSON/diff clean；签名 fixture 端到端捕获 64 external/88 total/133 references 并到 `APPROVAL_COLLECTION`；真实外部证据仍待发布机构执行；[evidence](../specs/016-ga-production-authorization/evidence/execution-campaign-closure-gate-20260806.md) |
| 2026-08-07 | 3.31 | 完成 PGA-07 测试前/后双签名安全评估协议：campaign 创建后由 Security 角色签署精确 release/target/window/source/account、禁止动作、急停与数据删除委托；独立 assessor 的 v2 报告必须绑定委托、实际执行身份、数据处置、逐条 finding 和 PDF；v3 collector/最终门禁/closure 独立重验双方签名与 campaign→engagement→report 时序 | Codex | backend 1232 passed/19 skipped，核心 coverage 81.04%；生产授权专项 130 passed；安全采集器 7 passed；生产运维专项 41 passed；frontend 56 passed/lint/build；生产基线 30/30；69 planned/66 external/90 captured/135 references；Ruff/compile/OpenAPI/YAML/JSON/Compose/audit clean，mypy 81≤82；真实 Security 委托和第三方执行仍待外部完成；[evidence](../specs/016-ga-production-authorization/evidence/security-assessment-engagement-gate-20260807.md) |
| 2026-08-07 | 3.32 | 完成 PGA-15 外部执行双人授权：campaign 落盘后、窗口开始前，由 topology 中互斥的 Security/Operations 身份分别签署同一派生 manifest，精确绑定 release/target/执行控制和八个 acknowledged 风险阶段；progress、closure、持久化 closure 与离线 archive 均重验原始 statement/signature，授权边界不包含 GA、未列明 mutation 或第三方安全评估委托 | Codex | backend 1233 passed/19 skipped，核心 coverage 81.04%；生产授权专项 131 passed；生产运维专项 41 passed；frontend 56 passed/lint/build；生产基线 31/31；74 planned/71 external/95 captured/139 references；Ruff/compile/OpenAPI/JSON/YAML/四套 Compose/audit clean，mypy 81≤82；真实 Security/Operations 身份与最终 campaign 签字仍待外部执行；[evidence](../specs/016-ga-production-authorization/evidence/execution-dual-authorization-gate-20260807.md) |
| 2026-08-07 | 3.33 | 完成 PGA-16 风险阶段执行启动 interlock：TLS/network/secrets/capacity/alerting/recovery/state-services/HA 各自要求原 Operations 授权人在活动窗口、依赖完成后签署 campaign/phase/ack/无密动作说明；closure v2 将许可时间与 raw probe/provider/load/restore/alert/fault 的最早时间逐项比较，progress、持久化 closure 与离线 archive 均重验，旁路执行结果不能进入正式 GA 证据 | Codex | backend 1234 passed/19 skipped，核心 coverage 81.04%；生产授权 132 tests；生产运维 41 passed；frontend 56 passed/lint/build；生产基线 32/32；90 planned/87 external/111 captured/290 references；Python/npm audit、Ruff/compile/OpenAPI、63 JSON/33 YAML/四套 Compose clean，mypy 81≤82；真实目标 IAM/RBAC/change-management 与外部执行仍由发布机构完成；[evidence](../specs/016-ga-production-authorization/evidence/execution-phase-interlocks-20260807.md) |
| 2026-08-07 | 3.34 | 完成 PGA-17 官方生产入口运行时 interlock：八个目标 probe/collector/load/HA CLI 在任何目标 I/O 或 mutation 前重验活动 phase permit，精确绑定 campaign/action、commit、两个镜像、target 及适用 context/Namespace/recovery target；local-validation 仍明确不具授权效力 | Codex | backend 1234 passed/19 skipped，核心 coverage 81.04%；生产授权 132 tests；生产运维 41 passed；相关回归 237 passed；frontend 56 passed/lint/build；生产基线 33/33；Python/npm audit、Ruff/compile/OpenAPI、63 JSON/33 YAML/四套 Compose clean，mypy 81≤82；平台外高权限路径仍由真实 IAM/RBAC/change-management 控制；[evidence](../specs/016-ga-production-authorization/evidence/execution-runtime-entry-interlocks-20260807.md) |
| 2026-08-07 | 3.35 | 完成 PGA-18 真实 Kubernetes 目标身份绑定：campaign v3 固定 kube-system UID 与认证 principal，原 Operations 授权人在活动窗口签署实时观测；network phase 内容寻址该签名，progress/closure v3/离线 archive 重验；network/secrets/HA 在 permit 后、目标效果前再次实时核对，拒绝 context 重指向和凭据漂移 | Codex | backend 1236 passed/19 skipped，核心 coverage 81.04%；生产授权 134 tests；生产运维 41 passed；PGA-18 定向 5 passed；frontend 56 passed/lint/build；生产基线 34/34；92 planned/89 external/113 captured/292 references；Python/npm audit、Ruff/compile/OpenAPI、63 JSON/33 YAML/四套 Compose clean，mypy 81≤82；真实 IAM/RBAC 最小权限、云审计及平台外管理员仍由发布机构控制；[evidence](../specs/016-ga-production-authorization/evidence/target-cluster-identity-binding-20260807.md) |
| 2026-08-07 | 3.36 | 完成 PGA-19 Kubernetes 访问授权与变更范围绑定：campaign/closure v4 固定变更单号、Secret/CNI/三组探针 Pod/HA zone；Operations 在活动窗口签署固定 `kubectl auth can-i` allow/deny 回执；phase action 绑定变更单，network/secrets/HA 在 permit 后、效果前重查身份、完整权限矩阵和精确范围 | Codex | backend 1238 passed/19 skipped，核心 coverage 81.04%；生产授权 136 passed；生产运维 41 passed；frontend 56 passed/lint/build；生产基线 35/35；94 planned/91 external/115 captured/294 references；Python/npm audit、Ruff/compile/OpenAPI、72 JSON/48 YAML/四套 Compose clean，mypy 81≤82；外部工单真实性、全部有效权限、云 IAM/审计和平台外管理员仍由发布机构控制；[evidence](../specs/016-ga-production-authorization/evidence/target-cluster-access-binding-20260807.md) |
| 2026-08-07 | 3.37 | 清理旧开发数据后完成本地整栈 RC 复验：修复 Analysis Worker token 行缺失导致的 401 重启环和 E2E Namespace owner membership 缺失；真实 Langfuse v4、Hermes Reporter/Fleet、浏览器与全量门禁通过，并明确当前 Hermes 模型推理与正式 GA 仍阻塞 | Codex | backend 1245 passed/19 skipped；GA/生产运维聚焦 252 passed；frontend 56 passed、Playwright 3/3、lint/build；Langfuse 60 项；生产基线 35/35；Alembic/OpenAPI/Python/npm audit/Ruff/compile clean，mypy 81≤82；实时 dev readiness 7 PASS/7 BLOCK（清库后的预期 fail-closed）；[evidence](../specs/015-ga-candidate/evidence/local-integrated-candidate-rehearsal-20260807.md) |
| 2026-08-07 | 3.38 | 完成 PGA-20 最终 tag 前一键本地 GA 预检：干净源码上统一运行仓库与 integrated 门禁，逐项日志内容寻址并输出不可覆盖回执；六类真实外部输入始终 PENDING，PASS 不能进入审批或授权阶段；首轮发现并修复 Playwright 产物权限缺口，最终拒绝 symlink、全产物 manifest、0700/0600 和敏感模式复验通过 | Codex | commit `b50ea653` integrated 73/73 PASS、0 BLOCK、74 artifacts；backend 1253 passed/19 skipped、coverage 81.04%；frontend 56、Playwright 3/3、Langfuse 60；生产基线 36/36；receipt SHA-256 `df65b6…cd9c`；外部 target/security/signer evidence 6 项仍待执行；[evidence](../specs/016-ga-production-authorization/evidence/ga-local-preflight-20260807.md) |
| 2026-08-07 | 3.39 | 完成第三方送审前内部安全预审：清除 production `assert`、静默异常与不安全 XML 解析，新增 fail-closed 一键预审和 CI 高置信 SAST；干净提交绑定依赖审计、安全负向测试、tracked 源码凭据启发式、四个一方镜像重建及 Critical/High SARIF，并在回执中固定非独立/非 GA 授权边界 | Codex | commit `17039993` 13/13 PASS、0 BLOCK；backend 1260 passed/19 skipped；security 198 passed/2 skipped；frontend 56；Python/npm 0 已知漏洞；744 tracked 非测试文本 0 finding；四镜像 Critical/High 0/0；receipt SHA-256 `27f442…57db`；独立第三方测试、真实目标与四方授权仍待外部完成；[evidence](../specs/016-ga-production-authorization/evidence/internal-security-preaudit-20260807.md) |
| 2026-08-07 | 3.40 | 将凭据启发式升级为专用 Git 全历史门禁：固定 Gitleaks v8.30.1 官方 OCI 多架构摘要，以无网络、只读、无 capability、100% redact 容器扫描全部 refs；首轮 33 条候选逐条确认均为无外部权限的 fixture/digest/检测常量/环境变量引用，只登记精确 fingerprint，不做路径、commit 或规则级豁免；backend CI 改为 full-depth checkout 并留存 redact 报告 90 天 | Codex | commit `e1537117` 内部预审 14/14 PASS；218 commits/19.72 MB，基线外 0 finding；security 198 passed/2 skipped；Python/npm 0 已知漏洞；四镜像 Critical/High 0/0；37/37 生产基线；receipt SHA-256 `2c5e6a…9644`；独立第三方与目标 Secret Manager 验证仍待外部完成；[evidence](../specs/016-ga-production-authorization/evidence/git-history-secret-scan-20260807.md) |
| 2026-08-07 | 3.41 | 完成 PGA-21 正式 Kubernetes 目标部署纳入 GA campaign：request/plan/closure v5 新增 `target_deployment` 风险阶段和 11 个产物，强制三故障域、Secret/TLS/RWX/egress 范围；部署前重验双签 campaign、release provenance、集群身份/访问签名、实时 RBAC、phase permit、变更单和内容寻址确认；成功/不完整回执可在无原主机路径的授权归档中重组并独立复验 | Codex | backend 1279 passed/19 skipped；frontend 56 passed/lint/build；105 planned/102 external/至少 126 captured/294+ closure references；Kubernetes bundle/deployment 16 passed；生产授权 136 passed；生产基线 39/39；真实集群、托管状态服务、TLS/网络 enforcement、容量/恢复/值班/故障域、独立安全和四方审批仍为外部阻断；[evidence](../specs/016-ga-production-authorization/evidence/campaign-bound-target-deployment-gate-20260807.md) |
| 2026-08-07 | 3.42 | 在 PGA-21 落入候选后，对最新干净 commit 重新执行一键 integrated GA 本地预检；统一复验仓库质量、冻结契约、生产静态基线、全部 GA 模板/脚本以及在线 DuckDock/Langfuse/OTel/Prometheus/Analysis Worker/Alembic/Playwright，并在进程外复算回执和全部附属产物 | Codex | commit `522f5a1` integrated 73/73 PASS、0 BLOCK、74/74 artifacts；backend 1279 passed/19 skipped、coverage 81.04%；frontend 56、Playwright 3/3、Langfuse 60；生产基线 39/39；receipt SHA-256 `e627ba…85f85`；0700/0600、无 symlink、6 项外部 PENDING 边界复验通过；[evidence](../specs/016-ga-production-authorization/evidence/ga-local-preflight-522f5a1-20260807.md) |
| 2026-08-07 | 3.43 | 关闭 GA 权威规格与执行器的 campaign v5 漂移：将 PGA-08/11/12/14/16～19 更新为 closure v5、102 external/15 external phases/九个风险 interlock/四个 Kubernetes live guards，并正式补入 PGA-20 本地预检与 PGA-21 目标部署验收标准；生产基线和测试同时拒绝旧 v4/91/14/八阶段口径回退 | Codex | 生产授权 136 passed；`test_prod_ops_config.py` 43 passed；生产基线 40/40 PASS（新增 `ga_campaign_spec_alignment`）；Ruff/compile/diff/stale-marker scan clean；真实目标执行、独立安全和四方授权状态不变；[evidence](../specs/016-ga-production-authorization/evidence/ga-campaign-v5-spec-alignment-20260807.md) |

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

截至 2026-08-07：

1. S0～S11、E00～E09、M0～M6 的既定技术范围已完成，加权技术进度 `100.0%`。E06/E07 已关闭 Handover/Identity seam，E08 已关闭 metrics/SLO/recovery，E09 已冻结 API v2 contract 并得到 GA Readiness=`READY`。下一步是用户在当前 dev 环境进行人工产品验证与合理性讨论。Runtime Experience 下发仍需另行批准，并必须具备目标绑定、独立审批、回执和回滚；当前 ACTIVE 仍仅代表控制面状态。
2. S1-B/S1-C 的 resolver、backfill、Namespace 强制新写、跨租户关系拒绝和 typed Evidence link 已保持兼容。
3. S1-D/EF-04 已完成：逐 ID remediation、原子审计 apply/replay、完整 contract preflight 与 revision 0029 均已落地。
4. Owner 将目标开发库的 275 条 blocker 判定为可丢弃测试数据；系统在写静默窗口前完成全库备份校验，之后按精确表/count 清理并保留零 blocker 报告。生产或来源不明的数据不继承该例外。
5. revision 0029 已在开发库和隔离 MySQL 迁移夹具通过：五个 Foundation `namespace_id` 为 non-null，四个 tenant-scoped indexes 和 Binding tenant-key unique constraint 生效，`alembic check` clean。
6. 当前质量门：backend `1279 passed, 19 skipped, 3 warnings`，核心 coverage `81.04%`；GA/生产运维聚焦 `252 passed`；真实开发 MySQL head=`0062`、`alembic check` clean；frontend `56 passed`、Playwright 3/3、lint 0 warning 且 production build 通过；Python/npm audit 与 Ruff clean；mypy `81≤82`；OpenAPI v2 snapshot drift clean；Langfuse v4 compatibility gate 60 项通过并保持适配器解耦；生产静态基线 40/40 PASS，含 campaign v5 权威规格一致性门禁；最新候选实现 commit `522f5a1` 的一键 integrated preflight 73/73 PASS 且保留 6 项外部 PENDING。
7. 默认 Compose 的 backend、worker、beat、Analysis Worker、MySQL、Redis、MinIO 与 frontend 健康；可选 Langfuse Web/Worker、Postgres、ClickHouse、OTel Collector 与 Prometheus 均在本地运行。清理旧开发证据后，当前 `/operations` 为预期的 `BLOCKED`（7 PASS / 7 BLOCK）；历史隔离候选 14/14 `READY` 仍证明应用闭环，但二者都不替代目标环境 GA 授权。
8. EF-05/FND-020～028 已完成：revision 0030 提供不可变 `AgentDeployment` / `DeploymentComponent` inventory、生命周期、审计和 Namespace RBAC 管理入口。
9. EF-06 已完成 FND-030/032～034/037～039：revision 0031 提供 metadata-only `AgentSession` / `AgentRun`、canonical envelope hash、幂等 replay/conflict 和终态并发保护。
10. EF-07 已完成 FND-031/035/036/040～043：`/api/v2/reporter/*` 与 `/api/v2/agent-runs*` 已开放；写身份完全由 credential 派生，管理读具备时间界限、签名游标与跨租户非枚举语义。
11. EF-08/FND-050～058 已完成：revision 0032 提供 immutable Artifact metadata、Telemetry Sink credential reference、TraceBackendRef、provider-neutral ports/null adapters 与 Namespace RBAC API；原始 span/content/provider secret 不进入 MySQL。
12. EF-09/FND-060～069 已完成：revision 0033 提供 Session/Run/Artifact 同事务 Outbox、低敏事件 allowlist、MySQL `SKIP LOCKED` lease、稳定 event ID、退避/FAILED、health/retry 与实时 Worker 调度。
13. EF-10/FND-070～078 已完成：Release evidence 必须精确绑定 candidate + Deployment revision，当前 Gate 不读取未来运行证据；默认 Compose/可选依赖复验和 local staging-equivalent backout drill 通过，证据见 G1 Evidence Alpha 技术验收包。
14. G1 技术条件已完成：三次 100 req/s × 5 秒持续与 500-request/1 秒短突发均零错误通过；每轮精确物化 1000 组 Run/Audit/Outbox，replay=201、conflict=409。
15. revision 0034 已提供 v1 Structured Report 低敏同事务事件、幂等 consumer receipt、typed AgentRun projection 与 reconciliation health/get/recompute API；管理摘要不伪造执行事实，隔离与 live lane 未解释差异均为 0。
16. Product/Architecture Owner 已于 2026-07-30 明确回复“确认，继续。”，M1/G1 和 S2 正式关闭。
17. S3/RT-01/NEXT-001 已完成：Collector 0.157.0 配置验证、401/200 认证路径、可信 Namespace/Runtime/Trust 覆盖、Secret Canary 与 live smoke 均通过。
18. S3/RT-02～04/NEXT-002 已完成：Runtime MCP 显式生命周期绑定 stable external Session/Run 与 OTel trace/root span；OpenInference/直接 OTel GenAI 标准化等价；Langfuse v4 OTLP exporter 与 bounded confirmation/failure isolation 均通过。
19. S3/RT-05 已完成：Runtime MCP v0.3.0 在首次发送前写入 metadata-only SQLite WAL，支持稳定 sequence、跨重启依赖解析、当前 credential 重放、持久 ack cursor、hard-limit fail-closed 与显式 PARTIAL/DECLARED_LOSS；Collector 使用独立 file-storage WAL，强制 KILL 后无需第二次 producer 请求完成重放。
20. S4/RT-06 已完成：Generic OTLP bridge 以 ReporterCredential 派生 Runtime/Namespace，revision 0035 仅存 metadata-only `GenericTraceProjection`；单 root 映射/创建 canonical Run，无 root 保持 UNMATCHED，双 root/复用 trace/冲突信号进入 QUARANTINED；provider 与 DuckDock 独立持久队列、provider outage 恢复无需第二次 producer request，OTL-001～009/CONF-C2-001～009 通过。
21. S4/RT-07 已完成：revision 0036 的 `PackImport`/`PackImportArtifact` 六态资源以 ReporterCredential 派生租户，`duckdock-pack/1.0` 严格 manifest、全包/逐 payload SHA、path/duplicate/symlink/encryption/bomb 防护和 existing-Run mapping 均在最终对象/Artifact/Outbox 之前；ATIF v1.0–v1.7 metadata-only、PAT-001～012 和真实 MinIO PUT/finalize/readback 通过。基础 import 为 DD-C1 + artifact extension，完整 DD-C3 由 NEXT-003 的协商能力闭环提供。
22. S4/RT-08 已完成并通过 G2：revision 0037 增加 opaque Runtime ID、credential-bound nonce handshake、动态 capability snapshot、append-only heartbeat history 与 Fleet read model；OpenClaw/Generic OTLP/Pack-ATIF 分别只声明其已通过的 DD-C3/DD-C2/DD-C1+ARTIFACT，Generic 无 Sink 不再返回可用，Pack/Generic 数据面可绑定协商握手；真实 MySQL、MinIO、全量 backend/frontend 门禁通过；Owner 于 2026-07-31 明确批准。
23. NEXT-003 已完成：revision 0038 增加兼容单 PUT 的 MinIO multipart init/list/part/complete、`PackBatchStream`/不可变 receipt 与连续 cursor、可复核 ATIF `PackExport`、size/SHA/Canary 校验后的 `EvaluationResultReplayed` Outbox；真实 MySQL 与 MinIO 门禁通过。Pack 只有协商完整七项能力时返回 DD-C3，旧客户端仍为 `DD-C1+ARTIFACT`。
24. Hermes Reporter 0.2.0 已完成：revision 0039 增加原生 `hermes-reporter` profile；真实本地 Hermes no-agent cron 自动执行 v2 handshake/heartbeat、metadata-only Session/Run 和 v1 structured report；成功/失败终态、服务端 duration、幂等 replay、Fleet DD-C1 与资产不重复均已验证。
25. G2/G3 已正式批准；E04～E09 技术 Gate 也已完成并登记证据。第三方 Pack producer 的生产部署认证仍需逐配置运行客户端本地持久化、pressure 与 loss-marker lanes；Runtime delivery adapter 仍需另行批准，不能由 GA 技术候选状态推定为已开放。
26. EH-01/NEXT-004 已完成：revision 0040 提供 Namespace-scoped Dataset/Evaluator、不可变版本、target-pinned Experiment/Evaluation 与有界摘要；Dataset item、prompt/output、trace 与 score detail 留在 Langfuse，DeepEval 仅作为可选执行端口；真实 Langfuse v4 Dataset sync、Clinic SDK、OTLP/Observations v2、MySQL migration 与全量回归均通过。
27. EH-02 已完成：revision 0041 提供 Evaluation execution key、lease/heartbeat/reclaim、attempt/backoff、取消与调度索引；同事务 `EvaluationQueued` Outbox 和恢复 Beat 复用同一 Worker；真实 Langfuse v4 Experiment 固定 DatasetVersion，重复投递只执行一次，2-item 混合结果以 `0.5 / 1 passed / 1 failed` 有界回写且 item 内容仍只在 Langfuse。
28. EH-03 已完成：revision 0042 提供不可变、版本化 `EvaluationResultManifest`，区分 expected/processed/scored/pass/fail/error 与 `COMPLETE/PARTIAL`，部分结果可重试且保留旧清单；真实 Langfuse Public API 以 `core,scores` 反查 2 个 item 并重算 SHA-256 与 MySQL manifest 一致，DuckDock HTTP API/Outbox 均未暴露 input/output/expected output/item array。
29. EH-04 已完成：revision 0043 提供不可变 RegressionPolicyVersion 与 EvaluationComparison，只接受显式 baseline/candidate manifest 和 policy version；同 DatasetVersion/EvaluatorVersion/provider Dataset/result schema 才可比较，部分结果为 INCONCLUSIVE，未知 `latest` 不回退；真实 Langfuse 两个 run 的稳定 item IDs 与 `[0,1]` 分数一致，DuckDock 比较 `PASS` 且独立重算 reproducibility digest 一致。
30. EH-05/NEXT-005 已完成：revision 0044 提供不可变 ReleaseCandidateEvaluationBinding，以精确 candidate ref、Deployment public ID/revision/config digest 与 EvaluationComparison 建立正式证据链，绑定后冻结 REGISTERED Deployment；新 Candidate Release Gate 只读 exact selector，缺失/REGRESSION/INCONCLUSIVE 均 `BLOCKED`；真实 Langfuse comparison 绑定后 `PASS`，未绑定 candidate fail closed，Gate digest 独立复算一致且 Outbox 已发布。
31. EH-06/NEXT-006 已完成并通过 G3：revision 0045 为每个精确 binding 提供一次性 APPROVED/REJECTED 人工决定，批准进入 Gate digest，拒绝以 `manual_review_rejected` fail closed；`/eval-hub` 提供治理总览、基线对比、候选证据评审与门禁复算；真实 MySQL、现有 Langfuse-backed binding、HTTP、Outbox/审计和本地浏览器均复验通过；Owner 于 2026-07-31 明确批准。
32. NEXT-007 Trace2Dataset 已完成：revision 0046 提供不可变 `EvaluationDatasetMaterialization`、严格幂等键、确定性 Provider item、Langfuse Observation v2 raw-IO adapter、Dataset manifest digest 与时间点 pin；原始 IO 只在 Langfuse 内复制，DuckDock API/MySQL/Audit/Outbox 仅含引用、版本、计数和摘要。真实 Langfuse `e448…481e` → item `df509…99ac` → DatasetVersion v3/3-items、UI 幂等重放、Outbox `PUBLISHED`、升级兼容门禁和隔离 MySQL guard 均通过；证据见 [NEXT-007](../specs/009-evaluation-hub/evidence/eval-hub-trace2dataset-20260731.md)。
33. NEXT-008 受治理策展队列已完成：revision 0047 以 `core,basic,time` 查询不含 IO 的 Langfuse Observation 候选，提供最多 20 项的不可变 selection digest、独立 APPROVED/REJECTED 评审和批准批次一次一个 DatasetVersion 的 provider-local 批量物化；真实浏览器三条候选从检索到批准/物化闭环通过，三条确定性 Dataset item/source ref、候选 `already_materialized`、Outbox/Audit content-free、隔离 MySQL populated downgrade guard 和 `44 passed` Langfuse 升级门禁均通过；证据见 [NEXT-008](../specs/009-evaluation-hub/evidence/eval-hub-curation-queue-20260731.md)。
34. NEXT-009 可复现生产采样已完成：revision 0048 提供 Namespace-scoped 策略与不可变版本，显式绑定版本/Dataset/时间窗，以 stable hash 在 metadata-only 候选中排序，最低样本不足 fail closed，并用数据库约束强制排除已治理来源；真实浏览器对五条 Langfuse Observation 完成 5 选 3，自动批次保持 `PENDING_REVIEW`，三条来源再次检索显示 `已治理`，Outbox/Audit content-free、隔离 MySQL populated downgrade guard 与全量门禁均通过；Langfuse Annotation Queue 被明确设计为独立可替换 adapter，不参与采样事务，并由 NEXT-010 落地；证据见 [NEXT-009](../specs/009-evaluation-hub/evidence/eval-hub-sampling-policy-20260731.md)。
35. NEXT-010 解耦 Annotation Queue 已完成：revision 0049 只绑定现有 Langfuse Queue 和不可变 Score Config 快照，DuckDock 先持久化 dispatch intent，再由独立 lease/backoff Worker 先对账后创建 Observation item；真实浏览器把 NEXT-009 的三条 Hermes 来源同步为三个唯一 provider item，Langfuse `COMPLETED` 对账为 3/3 后批次仍无 review/materialization；重复派发保持单 dispatch，Outbox/Audit content-free，升级门禁、隔离 MySQL populated guard 与全量回归均通过；证据见 [NEXT-010](../specs/009-evaluation-hub/evidence/eval-hub-annotation-queue-20260803.md)。
36. NEXT-011 标注驱动 Promotion 推荐已完成：revision 0050 提供 Namespace-scoped policy 和不可变版本，精确固定 Annotation Queue binding、Score Config/data type、质量规则与 metadata-only 多样性门槛；只有 100% 完成的 dispatch 可评估，缺分/质量/metadata/多样性均 fail closed。真实浏览器对三条 Hermes Observation 验证 v1 `BLOCKED/insufficient_diversity` 和 v2 `RECOMMENDED/promotion_criteria_met`，两次运行后批次仍无 review/materialization；Scores API v3 lane、Outbox/Audit content-free、隔离 MySQL populated guard 与全量回归均通过；证据见 [NEXT-011](../specs/009-evaluation-hub/evidence/eval-hub-promotion-policy-20260803.md)。
37. NEXT-012 跨批次 Case Routing 已完成：revision 0051 提供 Namespace-scoped policy 与不可变版本，精确绑定一个非 NONE Promotion 版本，在最多二十个互不重叠运行上只消费已持久化 quality/diversity 布尔与 digest，以稳定 cluster round-robin 生成 Golden/Bad Case 候选；minimum 不足整次 BLOCKED 且无半批次。真实浏览器把两个 v1 Promotion 运行的六条 Hermes Observation 路由为 Golden 4 条/2 clusters 与 Bad Case 2 条/1 cluster，两个目标批次仍 PENDING_REVIEW 且无 review/materialization；Outbox/Audit content-free、隔离 MySQL guard、959/45 全量回归和 Langfuse v4 real gate 均通过；证据见 [NEXT-012](../specs/009-evaluation-hub/evidence/eval-hub-case-routing-20260803.md)。
38. NEXT-013 Failure Taxonomy/Experience Candidate 已完成：revision 0052 提供 Namespace-scoped policy 与不可变版本，精确绑定一个 Case Routing 版本，只消费 selected Bad Case lane/digest/ref，以显式条数/运行阈值确定性分类跨运行重复、单运行重复和孤立失败；isolated 默认排除，无合格 cluster 时 BLOCKED。真实浏览器从既有 Hermes 路由的 2 个 Bad Cases/1 cluster 提取 1 个 SINGLE_RUN_RECURRING 候选并完成独立不可变批准，2 条 lineage 精确指向同一 Promotion run；批准不创建 Prompt/Skill/Memory/Agent 变更。Outbox/Audit content-free、隔离 MySQL guard、963/46 全量回归与 Langfuse v4 57 项 real gate 均通过；证据见 [NEXT-013](../specs/009-evaluation-hub/evidence/eval-hub-failure-taxonomy-experience-20260803.md)。
39. NEXT-014 Experience Asset/Activation 已完成：revision 0053 提供 approved candidate 单次认领、Namespace-scoped asset、人工正文不可变版本与一次 activation request；作者或请求人不能审批，第二人批准把精确版本标记 ACTIVE 并原子退役旧版本。真实 HTTP 验证同人 409/第二人 ACTIVE，本地浏览器看到新面板、资产与唯一 ACTIVE；三类 Outbox、四类 Audit 齐全且正文/申请说明泄露计数均为 0。激活不调用 Langfuse/Hermes，不改变 Prompt/Skill/Memory/Deployment/Release Gate；966/47 全量回归与 Langfuse v4 57 项 real gate 通过；证据见 [NEXT-014](../specs/009-evaluation-hub/evidence/eval-hub-experience-asset-activation-20260803.md)。
40. NEXT-015 真实语义失败聚类已完成：revision 0054 以 provider-neutral port 隔离 Langfuse IO 与 OpenAI-compatible embedding，策略版本冻结模型/维度/cosine 阈值和 Case Routing pin，核心使用确定性 centroid 聚类；MySQL/API/Audit/Outbox 只保存引用、相似度和 digest。真实本地 Hermes BGE 对两个 Bad Cases 的 cosine=`0.985426665`，在阈值 `0.9754` 下形成一个合格簇，精确语义版本/运行被 Failure Taxonomy/Experience 提取消费并生成一个候选；浏览器 console 无错误，969/47 全量回归与 Langfuse v4 59 项 real gate 通过；证据见 [NEXT-015](../specs/009-evaluation-hub/evidence/eval-hub-semantic-failure-clustering-20260803.md)。
41. NEXT-016 语义簇质量/漂移门禁已完成：revision 0055 新增不可变质量策略版本和 comparison，要求 baseline/candidate 精确同源并校验 content digests，以 pairwise assignment agreement、归一化簇数量变化、合格簇比例下降和 centroid similarity 下降判定 PASS/DRIFTED/INCONCLUSIVE；门禁不调用 Langfuse/Hermes/embedding。真实稳定候选 `esrc_fb254656d45f4ad49ac2f21e753176b2` 为 PASS，漂移候选 `esrc_6a4490eebd284726bb2e3ab8d5966d02` 为 DRIFTED；971/48 全量回归与 Langfuse v4 60 项 real gate 通过；证据见 [NEXT-016](../specs/009-evaluation-hub/evidence/eval-hub-semantic-regression-20260803.md)。
42. NEXT-017 定时语义漂移监控已完成：revision 0056 新增精确 pin 的监控、不可变执行和站内告警；Beat/Worker 对同源 Case Routing 证据进行 lease/retry 重聚类和离线比较，PASS 静默，DRIFTED/INCONCLUSIVE/最终失败告警，一次性确认备注不进入 Audit/Outbox。真实 Beat+Worker+本地 Hermes 运行得到稳定 `esmr_53ac186baf814bb2ab16750e2596eef2` 的 PASS/无告警和漂移 `esmr_00e1d4e0c62f4138b983a97913ceae09` 的 DRIFTED/CRITICAL→ACKNOWLEDGED；两监控已暂停待人工查看；973/49 全量回归与 Langfuse v4 60 项 real gate 通过；证据见 [NEXT-017](../specs/009-evaluation-hub/evidence/eval-hub-semantic-monitor-20260804.md)。
43. E04 Package Registry v2 已完成：revision 0057 新增 AgentPackage/PackageVersion、组件 DAG、CycloneDX/SPDX SBOM、Ed25519 公钥信任、provenance 与 nullable Deployment pin；真实外部私钥只在验证进程中签名，PackageVersion `pkgv_35c6af5165f54f69aad2c6d8acb59175` 完整复验，MinIO readback digest 一致，Deployment `dep_e9e60bbea96c499999874579c2862461` ACTIVE 且精确 pin。即时 HTTP replay 捕获并修复 MySQL commit-visibility 竞态及 content-addressed SBOM 误删风险；980/50 全量回归、mypy 82/82 与 Langfuse v4 60 项 gate 通过；证据见 [E04](../specs/010-package-registry-v2/evidence/package-registry-v2-20260804.md)。
44. E05 Release Control 已完成：revisions 0058/0059 新增 Environment、不可变 ReleaseCandidate、版本化 Policy/Rule decision、四眼审批/限时例外、Promotion、独立 `release.receipt` Runtime credential、Canary evaluation、精确 Rollback 与激活历史。真实本地 Hermes baseline `rprom_dcf78d2fb3fb47169360055232894cd4` 完成 SUCCEEDED/APPLIED；失败 canary `rprom_2dca580133fa464a9a9cf84206a08424` 产生 FAIL、自动 rollback `rrbk_25ec1a2926584fb981e4af86379b0ce0` 和 Runtime APPLIED 回执，环境恢复 baseline；986/52 全量回归、隔离 MySQL downgrade guard 与浏览器 console 0 error 通过；证据见 [E05/G4](../specs/011-release-control/evidence/release-control-g4-20260804.md)。
45. E06 Handover 2.0 已完成：revision 0060 提供不可变 snapshot/graph/readiness、义务回执、接受与 signed package；真实 Hermes release evidence、MinIO readback 和外部 Ed25519 独立验证通过；证据见 [E06/G5](../specs/012-handover-2/evidence/handover-2-g5-20260804.md)。
46. E07 Identity/Security 已完成：revision 0061 提供 SCIM lifecycle、DEVICE/SERVICE identity、原子 access revoke/handover 和 key rotation；真实目录等价流程验证停用后登录与旧 credential 均为 401；证据见 [E07](../specs/013-identity-security/evidence/identity-security-e07-20260804.md)。
47. E08 Observability/Ops 已完成：revision 0062 提供 content-safe Prometheus metrics、SLO/incident 和不可变 recovery receipt；真实 MySQL/MinIO restore 得到 RPO=0s/RTO=1s，Prometheus target/rules healthy；证据见 [E08](../specs/014-observability-ops/evidence/observability-ops-e08-20260804.md)。
48. E09/G6 技术候选已完成：contract `2.0.0-ga` drift clean，v1 在 2.x 兼容；真实 Hermes traffic 得到 SLO HEALTHY，14 项 GA checks 全部 PASS；全量 backend 1005 passed、frontend 53 passed；证据见 [E09/G6](../specs/015-ga-candidate/evidence/ga-candidate-e09-20260804.md)。
49. PGA-09 最终发布供应链 FOUNDATION 已完成仓库侧实现：受控 `v2.0.0` tag 只有在 backend/frontend/E2E/Compose 全通过后才推送带 provenance/SBOM 的 commit 候选，四镜像 Critical/High 扫描全通过、原始 SARIF 已留存且最终 tag 尚不存在后才晋升；独立 builder policy/report 绑定 tag/source/SLSA/SPDX registry predicate/Statement、原始 SARIF、scan 和精确镜像，最终授权器重验全部文件并禁止 builder 与审批/评估角色复用 identity/key。1182 项后端回归、82 项授权专项与 21 项生产基线通过；真实最终 tag、registry bundle、目标证据和组织签字仍待外部执行；证据见 [PGA-09](../specs/016-ga-production-authorization/evidence/release-provenance-gate-20260806.md)。
50. PGA-08 四方签字活动已完成仓库侧 fail-closed 编排：每个 signer 必须使用 approvals 为空的同一 base 和 out-of-band policy 重跑完整证据门禁，工具自动提取 release digest、验证唯一 role identity、签名后立即用共享 trust store 反向验证，分别保留 approval/preflight/signature；finalizer 拒绝手工混合、重复角色/identity、错误 digest/key、证据变化和覆盖旧输出，只有持久化文件重新得到 `GA_AUTHORIZED` 才生成 finalization receipt。1188 项后端回归、87 项授权专项、40 项生产运维专项和 22 项生产基线通过；真实四方人员仍须亲自审阅和签署；证据见 [PGA-08 campaign](../specs/016-ga-production-authorization/evidence/approval-campaign-gate-20260806.md)。
51. `GA_AUTHORIZED` 授权归档已完成仓库侧闭环：archiver 在当前时间前后两次要求无阻断 `GA_AUTHORIZED`，只沿授权/策略 JSON 的 `path+sha256`、`allowed_signers_path+sha256`、`signature_path` 引用和明确 supplemental 文件收集，限制允许根、单文件/总大小并拒绝 symlink、私钥材料、越界、摘要冲突、TOCTOU 和覆盖。归档路径按内容寻址，tar/gzip metadata 固定；外部 manifest 和 SHA-256 sidecar 不可覆盖，落盘后重开逐成员验证，不扫描目录或带入邻近私钥。1192 项后端回归、91 项授权专项、40 项生产运维专项和 23 项生产基线通过；真实 `v2.0.0` 授权 bundle/digest 仍待发布机构执行和外部发布；证据见 [authorized archive](../specs/016-ga-production-authorization/evidence/authorized-archive-gate-20260806.md)。
52. GA 授权归档可搬运复验已完成：manifest v2 为 `path+sha256`、`path+manifest_sha256`、trust-store digest 和 signature 建立精确 source→target index，归档器以 bounded canonical time 和当前时间重验，并在写包前用 strict reference closure 预演；独立 verifier 必须取得外部 digest，拒绝摘要/manifest/member/index/signature substitution，随后仅从临时内容寻址成员重跑完整 evaluator，未索引原主机路径一律 missing。测试在删除原 authorization/policy/全部证据和签名后仍得到同一 `GA_AUTHORIZED`；同时修复 backup allowed-signers 未内容寻址的缺口。1199 项后端回归、98 项授权专项、6 项恢复 collector、40 项生产运维专项和 24 项生产基线通过；真实 bundle 与外部 digest 仍待发布活动；证据见 [portable archive verification](../specs/016-ga-production-authorization/evidence/portable-authorized-archive-verification-20260806.md)。
53. GA 审批活动冻结已完成：freezer 只对空 approvals 且证据完整的 base 生成不可覆盖、默认 24 小时/最大 72 小时的 campaign receipt；signer 的 v2 statement 与 preflight 同时签署 campaign ID 和 freeze 摘要，最终授权内容寻址 freeze 与空 base，finalizer/权威 evaluator/离线归档复验共同拒绝跨轮混签、绕过组装、过期、窗口外时间和冻结后证据变化。1205 项后端回归、104 项授权专项、40 项生产运维专项和 24 项生产基线通过；真实发布机构仍须冻结最终 bundle，并由四个不同真人在窗口内独立签署；证据见 [approval campaign freeze](../specs/016-ga-production-authorization/evidence/approval-campaign-freeze-20260806.md)。
54. GA 预审批证据自动组装已完成：`preapproval-assembly-request-v1` 只保存最终 release/target 与九份 evidence 路径，组织 approval policy 不能由 request 自选；组装器重开全部 wrapper、重算顶层摘要和签名容量 load/growth/cleanup 投影，只有内存及持久化两次权威评估都到 `APPROVAL_COLLECTION` 才保留空 approvals 底稿和 receipt。1208 项后端回归、107 项授权专项、40 项生产运维专项和 25 项生产基线通过；真实证据、策略与审批仍由外部发布活动提供；证据见 [preapproval assembly gate](../specs/016-ga-production-authorization/evidence/preapproval-assembly-gate-20260806.md)。
55. GA 全局组织信任拓扑门禁已完成：发布机构可在目标演练前用内容寻址 manifest 一次性验证九份 policy/trust store，identity 与公钥必须在全部审批、评估、构建、探测、provider、on-call、执行和 verifier 职责中全局唯一；不可覆盖 receipt 落盘后会再次重开全部输入。最终授权器不信任该回执，而是从九份证据策略独立重算 `organizational_trust_separation`，复用即停在 `FOUNDATION`；缺证据时仍保持原有 `EVIDENCE_COLLECTION` 语义。1211 项后端回归、110 项授权专项、40 项生产运维专项、56 项前端测试和 26 项生产基线通过；真实组织身份、公钥和目标演练仍待发布机构执行；证据见 [trust topology gate](../specs/016-ga-production-authorization/evidence/trust-topology-gate-20260806.md)。
56. GA 真实验收执行 campaign 已完成仓库侧编排：版本化 request 只声明最终 release/target 和执行边界，topology receipt 必须通过独立 CLI 参数提供并被重新验证；campaign ID 内容寻址绑定请求、topology、窗口和生成的 assembly request。12 个 phase 的依赖、风险、精确 acknowledgement、授权 policy role 和当前 67 个 artifact（含 tag/source/SLSA/SPDX/SARIF/scan/closure）必须完整且唯一，恢复 target 必须与 production 不同，evidence root 必须尚不存在；全部 phase 只能为 `PENDING_EXTERNAL_EVIDENCE`，计划明确不能授权 mutation/GA。1215 项后端回归、114 项授权专项、40 项生产运维专项、56 项前端测试和 27 项生产基线通过；真实发布机构仍须审阅计划并逐阶段授权外部执行；证据见 [execution campaign gate](../specs/016-ga-production-authorization/evidence/execution-campaign-gate-20260806.md)。
57. GA execution campaign closure 已完成：request 在执行前内容寻址 backup allowed-signers，计划现包含 64 个外部产物和 3 个预审批闭环输出；关闭工具重验 campaign/request/topology，限定执行窗口与十份最终 `observed_at`，并要求全部显式引用精确闭合到计划路径或 24 份内容寻址发布机构输入。签名 fixture 端到端捕获 64 external/88 total/133 references，权威评估到 `APPROVAL_COLLECTION` 后原子写 authorization/assembly/closure，重跑拒绝覆盖；缺失、替换、摘要冲突和窗口外证据均 fail closed。1221 项后端回归、120 项授权专项、40 项生产运维专项、56 项前端测试和 28 项生产基线通过；真实发布机构仍须提供全部外部证据并执行后续四方审批；证据见 [execution campaign closure](../specs/016-ga-production-authorization/evidence/execution-campaign-closure-gate-20260806.md)。
58. GA execution closure → approval → archive 摘要链已闭合：正式 `approval-campaign-freeze-v2` 在冻结前独立复验 persisted closure，并把 closure、空 base、out-of-band policy、release digest 与窗口共同纳入 campaign ID；四份 statement 通过 freeze digest 传递绑定，signer/finalizer/最终授权 CLI/archiver/archive verifier 默认拒绝 unbound v1。v2 freeze 使 closure 及其 64 external/88 captured/133 references 自动进入 deterministic archive，签名 fixture 已端到端通过四方签字、`GA_AUTHORIZED`、无 supplemental closure 归档和隔离临时目录 `GA_AUTHORIZED_ARCHIVE_VERIFIED`；v1 只保留显式历史审计开关。1221 项后端回归、120 项授权专项、40 项生产运维专项、56 项前端测试和 28 项生产基线通过；真实身份、目标证据、真人审批与独立摘要发布仍待发布机构执行；证据见 [approval campaign closure binding](../specs/016-ga-production-authorization/evidence/approval-campaign-closure-binding-20260806.md)。
59. GA 正式公开发布边界已闭合：新增 `authorize_ga_publication.py`，从同一次隔离 archive 复验中取得 release/target/final-tag CI context，默认拒绝 unbound v1，绑定独立 archive digest、2.0.0 commit/镜像、production kubernetes-ha 目标、draft Release 与受保护 workflow，并限制 canonical 授权后 24 小时内发布。新增 `publish-ga.yml`，仅允许 `BaiKudan/DuckDock` 的 `ga-production-publication` Environment 执行，重验 signed tag、精确 draft assets、完整 portable archive 和归档绑定的成功 CI run；先上传/回读不可覆盖 publication receipt+sidecar，再公开同一 draft，且支持上传后失败的安全重跑。formal fixture 已端到端到 `GA_PUBLICATION_AUTHORIZED`；1223 项后端回归/19 skipped、121 项生产授权专项、41 项生产运维专项、56 项前端测试和 29 项生产基线通过，Ruff/compile/YAML/JSON/Compose/OpenAPI/依赖漏洞门禁通过，mypy 81≤82；真实 Environment reviewers、授权 archive/digest、draft 和实际公开动作仍待发布机构执行；证据见 [GA publication gate](../specs/016-ga-production-authorization/evidence/ga-publication-gate-20260807.md)。
60. GA 外部执行增量可观测门禁已完成：新增 `inspect_ga_execution_campaign.py` 与 `duckdock-ga-execution-campaign-progress-v1`，每次从 campaign/request/topology/policies 重建计划，按 lexical path 检查 64 external artifacts 的 missing/symlink/type/size/JSON/private-key/unplanned-reference/digest，并把 11 个外部 phase 分类为 pending/partial/dependency-blocked/invalid/artifacts-ready；识别未开始、活动、过期和已 closure，输出精确工具/role/ack/missing 的下一动作。checkpoint 不可覆盖且禁止进入 evidence root，`ARTIFACTS_READY` 明确不代表 PASS；只有 `--require-ready-for-closure` 后原 closure/full evaluator 才能推进。同时修复 execution root 或计划 artifact 在准备后被替换为 symlink 时 `resolve()` 过早吞掉链接的底层问题，progress 与 direct closure 均 fail closed。1224 项后端回归/19 skipped、122 项生产授权专项、41 项生产运维专项、56 项前端测试和 30 项生产基线通过；Ruff/compile/YAML/JSON/Compose/OpenAPI/依赖漏洞门禁通过，mypy 81≤82。真实 64 份目标/第三方产物仍待发布机构执行；证据见 [execution campaign progress](../specs/016-ga-production-authorization/evidence/execution-campaign-progress-gate-20260807.md)。
61. PGA-07 独立安全评估已从“只验最终报告”升级为测试前/后双签名协议：正式 campaign 创建后，Security 角色先签署最长 30 天、精确来源/测试账号/禁止动作/急停/数据删除的委托；外部 assessor 的 v2 报告必须绑定其 ID/SHA-256、实际执行身份、数据处置、逐条 finding 和 PDF，v3 collector、最终 evaluator 与 closure 分别重验双方 key/role、内容和时序。campaign 现为 69 planned/66 external，签名 fixture 捕获 90 inputs/135 references 并走通 `GA_AUTHORIZED`/archive/publication authorization。1232 项后端回归、130 项授权专项、7 项采集器专项、41 项生产运维专项、56 项前端测试和 30 项生产基线通过；真实 Security 委托、第三方测试、整改复测和删除证明仍待外部完成；证据见 [security assessment engagement gate](../specs/016-ga-production-authorization/evidence/security-assessment-engagement-gate-20260807.md)。
62. PGA-15 外部执行双人授权已完成仓库侧闭环：campaign 创建后、窗口开始前，由 release-authority approval policy 中互斥的 Security 与 Operations 身份分别签署工具派生的同一 manifest；manifest 内容寻址 campaign，并固定 release/target、Kubernetes/恢复/窗口控制及 TLS/network/secrets/capacity/alerting/recovery/state-services/HA 八个阶段的 acknowledgement、工具与输出。progress、closure、持久化 closure 和 no-host-fallback archive verifier 均重验原始两份 statement/signature，签字只授权列明执行，不授权 GA、未列明 mutation、evidence PASS 或第三方评估委托。签名 fixture 现为 74 planned/71 external/95 captured/139 references；1233 项后端回归、131 项授权专项、41 项生产运维专项、56 项前端测试和 31 项生产基线通过；真实 Security/Operations 身份和最终 campaign 签字仍待发布机构执行；证据见 [execution dual authorization gate](../specs/016-ga-production-authorization/evidence/execution-dual-authorization-gate-20260807.md)。
63. PGA-16 风险阶段执行启动 interlock 已完成仓库侧闭环：八个 acknowledged phase 各自新增不可覆盖的 Operations 签名 start statement/signature，启动工具重建 campaign/topology、重验双签和全部依赖产物，只接受原 Operations authorizer，并由工具派生当前 UTC 与单行无密动作说明摘要；不暴露 backdate 或 caller-controlled digest。closure v2 把八份许可与 raw TLS/network、secret rotation、capacity load、alert exercise、restore、state provider 和 HA fault-injection 的最早时间比较，progress 与 no-host-fallback archive 复验全部签名和 dependency digests。错误 identity、越窗、篡改动作、跨 phase 复用签名、许可后改变依赖、动作早于许可和过期 campaign 均 fail closed。fixture 现为 90 planned/87 external/111 captured/290 references；1234 项后端/19 skipped、132 项生产授权、41 项生产运维、56 项前端和 32 项生产基线通过。仓库保证旁路结果不能成为 GA 证据，但目标 IAM/RBAC/change-management 仍须发布机构独立阻止平台外操作；证据见 [execution phase interlocks](../specs/016-ga-production-authorization/evidence/execution-phase-interlocks-20260807.md)。
64. PGA-17 官方生产入口运行时 interlock 已完成：`verify_runtime_entry` 在重验 PGA-16 全部签名、窗口和依赖后，再精确绑定 campaign digest/ID、phase action、target-production scope、commit、backend/frontend 镜像，以及适用的 Kubernetes context/Namespace 或 recovery target。TLS/network/secrets/capacity/alerting/recovery/state-services/HA 八个正式 CLI 均在 probe/collect/load/fault effect 前调用它；静态基线固定精确 phase 映射、必需参数和调用顺序。正确许可得到非授权 `RUNTIME_ENTRY_AUTHORIZED`，错误 action/target/commit/context/recovery target 均 fail closed；local-validation 不升级为 GA 证据。1234 项后端/19 skipped、132 项生产授权、41 项生产运维、237 项相关回归、56 项前端和 33 项生产基线通过；真实云控制台、直接集群管理员或仓库外工具仍必须由 IAM/RBAC/change-management 独立约束；证据见 [execution runtime entry interlocks](../specs/016-ga-production-authorization/evidence/execution-runtime-entry-interlocks-20260807.md)。
65. PGA-18 真实 Kubernetes 目标身份绑定已完成：execution campaign request/plan v3 除 context/Namespace 外必须固定 `kube-system.metadata.uid` 和 `kubectl auth whoami` 的无通配精确 principal；活动窗口且 release provenance 齐备后，原 Operations 双签人运行只读 collector，工具自行实时查询并签署不可覆盖的 identity report/signature。network phase-start 依赖并内容寻址该签名；progress、closure v3 和 no-host-fallback archive 均重验。network/secrets/HA 正式 CLI 固定 `phase permit → live UID/principal → effect` 顺序，UID/principal 漂移、错误 signer、模糊请求、报告篡改全部 fail closed。fixture 现为 92 planned/89 external/113 captured/292 references；1236 项后端/19 skipped、134 项生产授权、41 项生产运维、5 项定向、56 项前端和 34 项生产基线通过。该控制只证明进程所见身份，真实 IAM/RBAC least privilege、云审计、change-management 与平台外管理员路径仍须发布机构独立控制；证据见 [target cluster identity binding](../specs/016-ga-production-authorization/evidence/target-cluster-identity-binding-20260807.md)。
66. PGA-19 Kubernetes 访问授权与变更范围绑定已完成：execution campaign request/plan v4 固定外部 `change_request_id`，并逐项固定目标 Secret、CNI DaemonSet、trusted/monitoring/untrusted probe Namespace/Pod 和 HA drain zone。原 Operations 双签人在活动窗口、release provenance 与目标 identity 齐备后运行只读 access collector；工具重验身份、执行固定 `kubectl auth can-i` allow/deny 矩阵，并签署不可覆盖的 report/signature。phase action ID 必须以变更单号加 `/` 开头；progress、closure v4 与 no-host-fallback archive 重验回执。network/secrets/HA 正式 CLI 固定 `phase permit → exact scope → live UID/principal → full permission profile → effect` 顺序，权限、身份或范围漂移全部 fail closed。fixture 现为 94 planned/91 external/115 captured/294 references；1238 项后端/19 skipped、136 项生产授权、41 项生产运维、56 项前端和 35 项生产基线通过。该控制不验证外部工单批准、不穷举 Kubernetes 全部有效权限，也不能约束云/集群管理员或仓库外工具；真实 IAM/RBAC review、审计和 change-management 仍由发布机构独立完成；证据见 [target cluster access binding](../specs/016-ga-production-authorization/evidence/target-cluster-access-binding-20260807.md)。
67. PGA-21 正式 Kubernetes 目标部署已纳入 GA campaign：campaign v5 新增 `target_deployment` phase，将三阶段 bundle、Operations phase-start、fresh preflight 和最终 deployment receipt 共 11 个产物固定到同一 release/target/change；正式 deploy 只有在双签执行授权、签名 provenance、集群 UID/principal/access、三故障域、Secret/TLS/RWX/egress 范围、实时权限矩阵和确认串全部一致时才可写入。closure v5 与 no-host-fallback archive 会从内容寻址成员重组 bundle/sidecar 并重新验证，路径迁移不会改变安全身份；预检替换、跨工单 action、时间倒置和 rollout 投影篡改均 fail closed。fixture 现为 105 planned/102 external/至少 126 captured/294+ closure references；backend 1279 passed/19 skipped、frontend 56 passed/lint/build、Kubernetes bundle/deployment 16 passed、生产授权 136 passed、生产基线 39/39。该状态仍不证明真实 TLS/NetworkPolicy enforcement、托管状态/RWX 冗余、容量增长、恢复/值班、故障域存活、独立安全或四方签字；证据见 [campaign-bound target deployment](../specs/016-ga-production-authorization/evidence/campaign-bound-target-deployment-gate-20260807.md)。
68. 最新候选提交已重跑完整一键 GA 本地预检：干净 commit `522f5a1` 的 repository 与 integrated 共 73 项全部通过，在线 DuckDock、Langfuse 4.1.0、OTel、Prometheus、Analysis Worker、Alembic、Playwright 和 Langfuse 60 项兼容门禁均真实执行；进程外复验 receipt sidecar、源码 commit/tree、74/74 产物摘要、0700/0600 权限和无 symlink。回执 SHA-256 为 `e627babb100c122dd766ca8329152de2003ec09eefae5a5a997ba3e5e3185f85`，并继续把最终供应链、目标部署控制、容量增长、HA/故障域、独立安全与四方授权六项标记为 `PENDING_EXTERNAL`；证据见 [latest GA local preflight](../specs/016-ga-production-authorization/evidence/ga-local-preflight-522f5a1-20260807.md)。
69. GA 权威验收规格已与 campaign v5 执行事实重新对齐：PGA-08/11/12/14/16～19 不再残留 closure v4、91 external、14 external phases、八个 interlock 或三个 Kubernetes guard 的旧合同，PGA-20/PGA-21 正式定义非授权本地预检和 campaign-bound 目标部署。生产基线新增 `ga_campaign_spec_alignment`，与 `test_prod_ops_config.py` 双重拒绝旧口径回退；136 项生产授权、43 项定向测试、40/40 生产基线、Ruff、compileall、diff 和 stale-marker scan 通过。该变更只消除外部审计合同歧义，不改变真实目标证据仍缺失的事实；证据见 [campaign v5 spec alignment](../specs/016-ga-production-authorization/evidence/ga-campaign-v5-spec-alignment-20260807.md)。
