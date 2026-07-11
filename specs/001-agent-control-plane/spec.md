# Feature Specification: DuckDock 企业 AI Agent 资产与交接控制平面

**Feature Branch**: `001-agent-control-plane`

**Created**: 2026-06-08

**Status**: Implementing(2026-06-12 经 `/speckit-analyze` 与代码对账;后端 API 面约七成落地,缺口见 tasks.md)

**Input**: 把 DuckDock 从「私有 Skills 仓库」升级为「企业 AI Agent 资产与交接控制平面」:
统一资产目录 + 运行时连接 + 工作历程/证据 + 离职/项目交接闭环;采集 = **Push 上报**(Reporter + Analysis Worker;2026-06-12 决策,Pull Adapter 链退役)。

> 来源文档(权威细节仍以这些为准,本 spec 做结构化与可追溯收口):
> [01-product-prd](../../docs/agent-control-plane-prd/01-product-prd.zh-CN.md) ·
> [04-api-contract](../../docs/agent-control-plane-prd/04-api-contract.zh-CN.md) ·
> [07-roadmap-and-acceptance](../../docs/agent-control-plane-prd/07-roadmap-and-acceptance.zh-CN.md) ·
> [08-implementation-backlog](../../docs/agent-control-plane-prd/08-implementation-backlog.zh-CN.md)

## Clarifications

### Session 2026-06-12

- Q: FR-016 运行时凭证存储选型(KMS / Vault / 应用层加密)? → A: **应用层加密**——credential 表存 Fernet 密文(密钥独立 env 变量),`credential_ref` 指向密文记录;保留 Vault/KMS 升级路径。
- Q: FR-014 隔离模型? → A: 见 [specs/003](../003-tenant-isolation/spec.md) 同日决策——**单租户部署声明 + 激活闲置权限键**,不引入 tenant 列。
- Q: JVS Adapter 维持 P0 还是降级? → A: **降为 P1**(与 E10/E11 同批,等真实 JVS 环境需求)。依据:发布标准只要求"OpenClaw 和 JVS 至少一个真实环境同步通过",OpenClaw 已实现;PRD §7.1 P0 范围与发布标准的矛盾以发布标准为准。FR-015 仍有效,但实施排期后移。
- Q: 采集路径用 Pull(服务端 Adapter 外联)还是 Push(Reporter 上报)? → A: **仅 Push**(2026-06-12,本条 supersede 上一条 JVS 决策):现场探针打包 `duckdock-pack` 经 report token 上传,Analysis Worker(LLM)解析物化。**Pull 链整体退役**——FR-015/017/018 撤销;`runtime_instance` 语义收窄为「上报来源注册 + report token 管理」;「测试连接」改为上报链路自检(T080);openclaw-backup **上传式**导入保留(Push 兼容)。新开放问题:执行动作路径(FR-019)。
- Q: FR-019 执行动作走探针 lease 自动执行还是人工执行+回执? → A: **双模式,操作者按动作可选,`manual` 为默认**(2026-06-12)。决策理由(用户原话大意):"需要使用人员自己决定走哪种方式;人工执行可以加上一个自我二次审查,方便把不必要的东西**不**上报给公司平台。" → manual = 数据最小化通道;auto(探针 lease)留给信任度高/批量场景。实施拆 T084(manual 回执 MVP)/ T085(lease 协议,待探针支持)。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 创建运行时连接 (Priority: P1)

平台管理员配置 OpenClaw / JVS 运行时连接,使 DuckDock 可以采集企业 AI 资产。

**Why this priority**: 没有运行时连接就没有任何采集,是整个控制平面的入口与 MVP 基座。

**Independent Test**: 单独配置一个 OpenClaw/JVS 连接并「测试连接」,无需交接流程即可验证价值
(能看到 provider 版本与能力声明)。

**Acceptance Scenarios**:

1. **Given** 管理员在运行时中心, **When** 新增一个 OpenClaw 连接并填入凭证, **Then** 连接被保存且凭证不以明文返回前端。
2. **Given** 已保存的连接, **When** 点击「测试连接」, **Then** 返回 provider 版本与 Adapter 能力声明。
3. **Given** 一个不再使用的连接, **When** 管理员禁用它, **Then** 该连接不再参与同步且状态可见。

---

### User Story 2 - 同步资产 (Priority: P1)

平台管理员手动触发同步,查看进度与结果,成功后资产进入统一资产中心。

**Why this priority**: 「资产盘点」本身就是可独立交付的商业价值(无需交接闭环即可售卖)。

**Independent Test**: 对一个真实/mock 运行时触发同步,在资产中心看到统一映射后的资产即通过。

**Acceptance Scenarios**:

1. **Given** 一个可用运行时, **When** 触发同步, **Then** 产生一个 `collection_job`,状态在 `pending/running/succeeded/failed` 间流转。
2. **Given** 同步失败, **When** 查看任务, **Then** 能看到失败原因。
3. **Given** 同步成功, **When** 打开资产中心, **Then** Skill / OpenClaw 资产 / JVS 资产统一展示,详情含来源平台、运行时绑定、负责人。

---

### User Story 3 - 创建离职交接单 (Priority: P2)

HR 或管理员为离职员工创建交接单,圈定运行时范围与回溯窗口。

**Why this priority**: 交接闭环是产品差异化价值,但依赖 US-1/US-2 的资产底座先就位。

**Independent Test**: 创建一张交接单,系统进入 `collecting` 状态即可独立验证。

**Acceptance Scenarios**:

1. **Given** 一名离职员工, **When** 创建交接单并选择接收人、运行时范围、回溯天数、是否含产物, **Then** 交接单进入 `collecting` 状态。

---

### User Story 4 - 生成交接建议 (Priority: P2)

系统基于采集证据生成交接建议(资产、动作、接收人、风险、置信度)。

**Independent Test**: 对已采集的交接单生成建议列表,每条建议带证据与置信度即通过。

**Acceptance Scenarios**:

1. **Given** 采集完成的交接单, **When** 生成建议, **Then** 每条建议包含资产、动作、接收人、风险原因、证据引用、置信度。
2. **Given** 一条建议, **When** 主管修改/跳过/标记人工复核, **Then** 变更被记录,不丢失原始建议。

---

### User Story 5 - 审批交接 (Priority: P2)

主管或安全负责人审批交接动作;未审批不可执行。

**Acceptance Scenarios**:

1. **Given** 待审批的交接, **When** 审批人查看, **Then** 能看到影响范围。
2. **Given** 审批被拒绝, **When** 尝试执行, **Then** 执行被阻止,审批意见进入审计。

---

### User Story 6 - 执行交接 (Priority: P3)

管理员在审批通过后执行交接动作(归属转移、归档、备份包生成等),幂等且可重试。

**Acceptance Scenarios**:

1. **Given** 已审批的交接, **When** 执行, **Then** 每个 `execution_action` 有独立状态与结果。
2. **Given** 某个执行动作失败, **When** 重试, **Then** 幂等执行不产生重复副作用,结果进入证据链与审计。
3. **Given** 交接执行产生备份/交接包, **When** 上传 MinIO, **Then** 保存 manifest 与 sha256 且可下载校验。

### Edge Cases

- 厂商 API 不完整,部分资产无法自动采集 → Adapter 声明能力缺口 + 人工补录,**不**伪装为完整采集。
- 文件下载中途失败 → 整体任务进入 `partial-failed`,已采集部分保留,不静默丢失(见宪法原则 II)。
- 备份包内含 secrets → 加密存储 + 权限隔离 + 默认不展示(见宪法原则 V)。
- LLM 建议错误 → 必须有证据 + 置信度 + 人工审批兜底,LLM 不作为最终事实。
- 越权访问 → 所有查询强制 `tenant_id`,需有越权测试覆盖。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 系统 MUST 支持运行时连接的新增/编辑/禁用,provider ∈ {openclaw, jvs, arkclaw, workbuddy, custom}。
- **FR-002**: 系统 MUST 提供运行时「测试连接」。*(2026-06-12 Push-only 修订:语义重定义为「上报链路自检」——report token 有效性 / 最近上报时间,不再外联厂商;实施归 T080。)*
- **FR-003**: 系统 MUST 加密存储运行时凭证,且**绝不**明文返回前端。
- **FR-004**: 系统 MUST 以 `collection_job` 表达同步任务,状态机为 `pending → running → succeeded | failed | partial-failed`。
- **FR-005**: 采集动作 MUST 幂等且可重试;部分失败 MUST 进入显式 `partial-failed` 状态。
- **FR-006**: 系统 MUST 将 Skill、OpenClaw、JVS 等异构资产统一 upsert 为 `ai_asset`,并保存归属推断结果。
- **FR-007**: 资产详情 MUST 展示来源平台、`runtime_binding`、负责人。
- **FR-008**: 工作历程 MUST 默认仅展示摘要;查看完整内容 MUST 要求填写原因并记入审计。
- **FR-009**: 每条交接建议 MUST 可追溯到 `evidence_item`。
- **FR-010**: 交接 MUST 由状态机驱动:`draft → collecting → analyzing → pending_approval → approved → executing → verifying → completed`,旁路终态 `rejected | cancelled`。
  *(2026-06-12 按实现枚举 `HandoverStatus` 修订;原 `done|archived` 与代码不符,实现更完整,以实现为准。)*
- **FR-011**: 系统 MUST 在审批通过前阻止任何执行动作(审批闸门)。
- **FR-012**: 交接/备份包 MUST 上传 MinIO 并保存 manifest 与 sha256,可下载校验。
- **FR-013**: 所有写操作 MUST 进入审计日志;审计日志 MUST NOT 可被普通管理员删除。
- **FR-014**: 部署模型为单租户(一企业一实例);实例内 MUST 通过系统权限键(`runtime.* / asset.* / handover.* / worktrace.* / evidence.*`)+ ownership/org 过滤实施最小可见性 *(2026-06-12 clarify 修订,实施细节见 specs/003;SaaS 多租户属 P2,届时再引入 tenant 维度)*。
- **FR-015**: **已撤销**(2026-06-12 Push-only 决策):不做 JVS 服务端 Adapter;未来接入 JVS 走 Reporter 技能适配。
- **FR-016**: 系统 MUST 以应用层加密持久化运行时凭证:独立 credential 表存 **Fernet 密文**(`cryptography` 已在依赖树),加密密钥来自独立环境变量(不复用 `SECRET_KEY`),`RuntimeInstance.credential_ref` 指向密文记录;任何接口 MUST NOT 回传明文 *(2026-06-12 clarify 决策;升级路径:可换 Vault/KMS 后端而不改调用方)*。
- **FR-017**: **已撤销**(2026-06-12 Push-only):ArkClaw 不做服务端 Adapter;未来接入走 Reporter 技能适配。
- **FR-018**: **已撤销**(2026-06-12 Push-only):WorkBuddy 同上。
- **FR-019**: 交接执行 MUST 支持**双模式且由操作者按动作选择**(2026-06-12 决策):
  - `manual`(**默认**):执行人在厂商平台手动完成后提交**回执**(结果 + 说明)→ 动作置 SUCCEEDED/FAILED + 审计 + 证据。人工通道天然构成**自我二次审查**——不必要的内容不流经公司平台(原则 V 数据最小化,决策原话见 Clarifications)。
  - `auto`:现场探针以 lease 协议领取执行任务(复刻 analysis worker 的 lease/heartbeat/finalize 模式),执行后回传结果。
  - 治理扩展位:高风险动作(如凭证轮换)可由 namespace 策略强制 `manual`。

### Key Entities *(include if feature involves data)*

- **runtime_instance**: 一个厂商平台连接(provider、能力声明、加密凭证引用、状态)。
- **collection_job**: 一次采集任务及其状态机与错误信息。
- **ai_asset**: 统一资产(Skill/Agent/Prompt/Workflow/MCP/知识库/凭证引用…),含来源平台。
- **asset_ownership**: 资产归属(推断 + 人工确认)。
- **runtime_binding**: 资产 ↔ 运行时实例的使用绑定。
- **work_trace** / **work_artifact**: 工作历程摘要与产物索引(产物走签名 URL)。
- **evidence_item**: 证据条目,被建议/执行动作引用。
- **handover_case** / **handover_item**: 交接单与交接项。
- **approval_task**: 审批任务(影响范围、意见、结果)。
- **execution_action**: 执行动作(幂等、状态、结果、可重试)。

## Success Criteria *(mandatory)*

### Measurable Outcomes *(源自 PRD §9)*

- **SC-001**: OpenClaw/JVS 资产同步成功率 > 95%。
- **SC-002**: 离职交接包生成时间 < 30 分钟。
- **SC-003**: 关键资产遗漏率(人工抽检)< 5%。
- **SC-004**: 审批动作审计覆盖率 = 100%。
- **SC-005**: 接管动作可回放率 = 100%。
- **SC-006**: 员工可见资产确认率 > 80%。
- **SC-007**: 平均交接周期比人工方式降低 ≥ 50%。

## Assumptions

- 复用现有 IAM / Namespace / Skill / Scanner / Sandbox / ReleaseGate / Clinic / 审计能力,不回归。
- 技术底座 E00(PostgreSQL → MySQL 8.x)已完成(见迁移 `0009_agent_control_plane` 起)。
- 产物与交接包存储复用 MinIO;签名 URL 有过期约束。
- OpenClaw 探针在**授权范围**内连接厂商平台;默认只采集元数据/摘要/产物索引。
- 采集仅 Push:Reporter 技能覆盖各厂商现场(OpenClaw 已有;ArkClaw/WorkBuddy 未来做 Reporter 适配),服务端不做外联 Adapter(2026-06-12 决策)。

## Traceability *(本 spec 补齐的 SDD 缺口:需求 ↔ 故事 ↔ Epic ↔ 来源)*

| 需求 | 用户故事 | Epic(08-backlog) | 来源文档 |
|---|---|---|---|
| FR-001~003 | US-001 | E02 Runtime | 07 §6 US-001 / 08 §3 |
| FR-004~007 | US-002 | E02, E03, E06, E07 | 07 §6 US-002 / 08 §4,7,8 |
| FR-008 | US-002/US-004 | E04 工作历程 | 08 §5 |
| FR-009, FR-013 | US-004/US-005 | E05 证据/审计 | 08 §6 |
| FR-010~012 | US-003~006 | E08 交接工作流 | 07 §4 / 08 §9 |
| FR-014 | (全局) | E01 身份/租户 | 07 §7.3 安全 |
| FR-015 | US-002 | E07 JVS | 08 §8 |
| FR-016~018 | (P1) | E10/E11 | 07 §8 风险清单 / 09 vendor research |
