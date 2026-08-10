# DuckDock 项目审查（2026-08-10）

> 一次性快照式审查。产出两块：**(A) 健康度与逻辑缺陷盘点**，**(B) 产品方向转型决策**（已落成 [ADR-0213](../adr/0213-collection-core-to-knowledge-service.md) + [CONTEXT.md](../../CONTEXT.md)）。
> 审查方法：结构盘点 + 后端逻辑审计 + 前端/集成审计 + specs↔实现漂移审计 + 向量能力查证，五路交叉核实。

---

## 0. 结论先行

- **代码工程卫生高**：表级零迁移漂移、全仓仅 8 处 TODO 且均为正常协议定义、无假路由。问题几乎全在**领域逻辑与授权语义**，不在工程质量。
- **文档漂移是系统性的**：CLAUDE.md 与 architecture.md 的计数几乎全错（详见 §2），specs 治理已实质失效。
- **产品重心已迁移**：近 30 个提交 100% 投入 GA 授权基础设施，产品功能线静止。经本次 grilling，方向转向「**保留 agent harness 管理 + 新增成果物知识服务，替换 v1 采集内核**」（§3）。
- **逻辑缺陷按新方向分流**（§4）：只有落在**保留/改造**模块的缺陷需要动手；**退场**模块的缺陷记录存档、不修。

---

## 1. 现状快照

| 维度 | 宣称（CLAUDE.md） | 实际 |
|---|---|---|
| 迁移 | head `0034` / 34 个 | head `20260804_0062` / **62** 个 |
| 后端测试 | 861 tests / 93 文件 | ≥1107 test fn / **166** 文件 |
| 前端测试 | Vitest 5 文件 / 34 项 | **11** 文件 / **44** 项；Playwright 2 spec / 3 case |
| API router | architecture.md 写 17~18 | v1 **19** + v2 **14** |
| services | architecture.md 写 ~28 | **77** |
| 版本 | — | `backend/app/version.py` = `2.0.0-rc.1` |

`specs/` 17 个目录（**009 编号冲突**：`009-evaluation-hub` 与 `009-foundation-tenant-contract` 并存）。宪法要求每 spec 六件套（spec/plan/tasks/data-model/contracts/quickstart），**仅 2/17 满足**；010–016 七个 epic 只有 `spec.md` + `evidence/`。

---

## 2. 系统性文档漂移

| 问题 | 位置 | 处理建议 |
|---|---|---|
| CLAUDE.md 四项数字全错 | `CLAUDE.md` 测试现状段 | 改为**指向命令**（`alembic heads` / `pytest --collect-only`），不固化易变计数 |
| architecture.md 六项数字全错，且 §7 自称「不固化易漂移计数」却在 §9 违反 | `docs/architecture.md:46,91,328,329,339,342` | 同上；§9 目录树补 `deployments.py` 与 v2 全 14 模块 |
| 008 Status 与 101/101 全勾自相矛盾 | `specs/008-*/spec.md` Status 行 | 更正 Status |
| 002/003/004 Status 停在 2026-06 | 各 spec Status 行 | 更正或标注 |
| 009 编号冲突 | `specs/009-evaluation-hub/` | 重编号（建议 `017-evaluation-hub`，原目录留 README 指路） |
| ADR 自 2026-07-28 后断产，E04–E09 跨边界决策零 ADR | `docs/adr/` | 本次 0213 已补一笔；E04–E09 关键决策择要补记 |
| 004/005 tasks 纯散文无 checkbox，完成度不可机器判定 | `specs/004,005/tasks*.md` | 底线规则：tasks 必须机器可判（见 §5 开放项 Q11） |

> **根因**：architecture.md §7 已写对了原则（不固化易变计数），执行未贯彻。建议一次性采纳该原则并清理两份文档。

---

## 3. 方向转型（已决策，详见 ADR-0213）

本次 grilling 的收敛结论：

- **DuckDock = agent harness 管理平台（保留）+ 成果物知识服务（新核心）**。
- **被替换的是采集内核**：从「harness 定时上报过程对话/WorkTrace」→「用户**显式标记**满意成果物 → 事件触发投递 → MinIO 存原件(事实) + LanceDB 存向量(**派生索引**) → qwen 多模态 embedding + rerank → 检索(先人后 agent)」。
- **保留**：2.0 harness 管理（fleet/遥测/package-registry-v2/release-control/eval-hub）。
- **改造**：交接 → 「交接知识制品库」；ingestion 骨架、experience assets 治理骨架、对象存储层复用。
- **退场**：v1 采集线 + 其纯展示面（个人工作台/agent-overview/资产中心）。
- **冻结**：GA 生产授权（spec 016）。

术语已钉入 [CONTEXT.md](../../CONTEXT.md)。

### 3.1 开放项裁决（2026-08-10 已定）

§5 原为待拍板项，现全部有结论：

| 项 | 裁决 | 对分流的影响 |
|---|---|---|
| O1 | **Skills 注册表 v1 保留**（核心功能之一） | §4.3 的 v1 门禁绕过 **升级为要修（P0）** |
| O2 | 两份失败证据是 GA/RC 容量验证（300rps 探针、GA campaign 容量 harness），随 **GA 线冻结**：不补豁免、不投入修复；但 015 的 "RC validation completed" **降级标注**为「含未闭环 300rps 容量项」（诚实 > 好看，且不给冻结线投资源） | 无（冻结线，不进 backlog） |
| O3 | 交接 **改造**为「交接知识制品库」 | §4.2 交接 FSM 缺陷在改造时一并修 |
| O4 | specs 治理 **修宪**（轻量 evidence-driven）；底线：tasks 必须含 checkbox（机器可判） | P2 |
| O5 | **Namespace 是唯一隔离单元**，清理 001 spec 矛盾文本 | P2/P3 |
| O6 | 文档 **不固化易变计数**，改指向命令；本次一并清理 CLAUDE.md + architecture.md，顺修 008 Status 矛盾、009 编号冲突 | P2 |

---

## 4. 逻辑缺陷清单（按 退场 / 保留 / 改造 分流）

严重度：🔴 critical｜🟠 high｜🟡 medium｜⚪ low

### 4.1 🔧 保留模块 — 要修（这些模块继续用，缺陷是真债）

**RBAC / IAM（知识服务权限边界的地基，最高优先）**

| 严重度 | 缺陷 | 位置 |
|---|---|---|
| 🔴 | **一步提权到全租户**：SYSTEM 作用域绑定在所有 namespace 生效 + 改角色权限时不校验「权限 scope == 角色 scope」，可给 SYSTEM 角色塞 `namespace.admin` → 全 namespace 写 | [iam_service.py:255](../../backend/app/services/iam_service.py) + [iam.py:443](../../backend/app/api/v1/endpoints/iam.py) |
| 🟠 | 权限缓存永不刷新：`permissionsLoaded` 被 persist 进 localStorage，刷新后跳过权限拉取，改绑定后用旧快照直到登出 | [store/auth.ts](../../frontend/src/store/auth.ts) + [Layout.tsx:45](../../frontend/src/components/Layout.tsx) |
| 🟠 | RBAC UI 断头路：全局 403 提示让用户「去 IAM 绑定角色」，但绑定/建角色/配权限的后端端点前端零消费 | [client.ts:47](../../frontend/src/api/client.ts) + [iam.py:476](../../backend/app/api/v1/endpoints/iam.py) |
| 🟡 | 改 `is_system` 角色权限：端点返回 200 且写审计，下次鉴权被 `ensure_builtin_rbac` 静默还原（响应与行为矛盾） | [iam.py:443](../../backend/app/api/v1/endpoints/iam.py) vs [iam_service.py:190](../../backend/app/services/iam_service.py) |
| 🟡 | `require_namespace_permission` 默认 `allow_legacy_admin=True`，新细粒度权限点被 legacy ADMIN 自动绕过 | [deps.py:196](../../backend/app/core/deps.py) |

**发布门禁 v2 / Release Control（保留 — 完整的绕过链）**

| 严重度 | 缺陷 | 位置 |
|---|---|---|
| 🔴 | **可自选宽松策略版本**：PolicyVersion 无 active/superseded 概念，评估用请求传入的版本 → developer 挑历史 SHADOW/宽松版本拿 ALLOW 再晋升 | [release_control_service.py:1020](../../backend/app/services/release_control_service.py) |
| ⚪ | ~~SHADOW 模式下 FAIL 候选无声晋升~~ **经 adversarial 复核为误报**：仍须走 DEV→STAGING→CANARY + 四眼/最小审批，SHADOW 只记录不阻断（spec §4.5 设计）。**不修**，见 [handoff §3.7](./2026-08-10-codex-handoff.md) 护栏 | — |
| 🟠 | 晋升权限最低：建环境/策略/审批/回滚都要 `namespace_admin`，唯独往 PRODUCTION 推的晋升只要 `namespace_writer`（developer） | [release_control.py:564](../../backend/app/api/v2/endpoints/release_control.py) |
| 🟠 | `minimum_approvals` 默认 0；候选证据绑定与人工复核可同一人完成（无职责分离，自建自批自升） | [schemas/release_control.py:56](../../backend/app/schemas/release_control.py) + [release_candidate_evaluation_service.py:442](../../backend/app/services/release_candidate_evaluation_service.py) |
| 🟡 | `required=False` 规则证据缺失记 NOT_APPLICABLE（fail-open）；全 optional 策略永远 PASS | [release_control_service.py:645](../../backend/app/services/release_control_service.py) |

**发布门禁 v1 / Skills（O1 已确认保留 → 升级为要修）**

| 严重度 | 缺陷 | 位置 |
|---|---|---|
| 🔴 | ClawHub 导入**绕过公开分享审批四项**（license 认证 / 名称 / 公开许可校验 / 风险确认），且默认落 `APPROVED` 状态；publish 正路强制这四项 | [clawhub.py:666](../../backend/app/api/v1/endpoints/clawhub.py) + [public_release_service.py:60](../../backend/app/services/public_release_service.py) |
| 🟠 | `require_sandbox_success` 在 `sandbox_run is None` 时整条跳过（静默放行），而 ClawHub 路径不建 SandboxValidationRun | [release_gate_service.py:172](../../backend/app/services/release_gate_service.py) + [clawhub.py:708](../../backend/app/api/v1/endpoints/clawhub.py) |
| 🟠 | scan 门禁只拦 `FAILED`，`PENDING/RUNNING` 放行 → 扫描排队时管理员 approve 即可推 production | [release_gate_service.py:208](../../backend/app/services/release_gate_service.py) |
| 🟠 | clinic 门禁按 namespace 取最新评测、与 skill/version 无关（`evaluate()` 的 `skill` 参数根本没用） | [release_gate_service.py:244](../../backend/app/services/release_gate_service.py) |

**前端 / 集成（保留模块的 UI）**

| 严重度 | 缺陷 | 位置 |
|---|---|---|
| 🟠 | 幂等键用 `Date.now()` 拼接 → 双击「晋升」产生两次晋升（后端强制 Idempotency-Key 被前端废掉） | [ReleaseControlPage.tsx:48](../../frontend/src/pages/ReleaseControlPage.tsx)、[OperationsPage.tsx:25](../../frontend/src/pages/OperationsPage.tsx) |
| 🟡 | 门禁决策仅存 React state，刷新即失；后端 `/release-gates/evaluations` 只有 POST 无 GET（违反 ADR-0208 精神：门禁结论未被当作可查询事实） | [EvalHubPage.tsx:123](../../frontend/src/pages/EvalHubPage.tsx) + [release_evidence.py:316](../../backend/app/api/v2/endpoints/release_evidence.py) |
| 🟡 | EvalHub 一次 `Promise.all` 26 请求，仅 1 处 `.catch`，任一 403 全页白屏 | [EvalHubPage.tsx:218](../../frontend/src/pages/EvalHubPage.tsx) |
| ⚪ | `field-control` CSS 类全仓未定义，30 处表单裸浏览器样式 | `ReleaseControlPage.tsx`、`PackageRegistryPage.tsx` |

**数据一致性（跨保留模块）**

| 严重度 | 缺陷 | 位置 |
|---|---|---|
| 🟡 | handler 中途 `await db.commit()` 把业务操作拆两事务，commit 后才 audit/dispatch_event/replication（违反宪法 III「业务状态+领域事件同事务」） | [skills.py:888](../../backend/app/api/v1/endpoints/skills.py)、[clawhub.py:711](../../backend/app/api/v1/endpoints/clawhub.py) 等 |

### 4.2 🔄 改造模块 — 改造时一并解决（交接 / ingestion / 治理骨架）

交接将从「交接工作历程」改造为「交接知识制品库」，以下缺陷在改造中修复，不单独立项：

| 严重度 | 缺陷 | 位置 |
|---|---|---|
| 🟠 | v2 acceptance 的 `REJECTED` 分支**完全无守卫**：任何 namespace 成员可对任意快照（含已 ACCEPTED）反复写 REJECTED，终态后仍可追加 | [handover_evidence_service.py:1119](../../backend/app/services/handover_evidence_service.py) |
| 🟠 | acceptance（最终状态变更）只要 READONLY 权限，同文件 obligation/signed-package 都要 writer | [handovers.py:150](../../backend/app/api/v2/endpoints/handovers.py) |
| 🟡 | `CANCELLED` 是**不可达状态**：无任何端点赋值，交接单永远无法取消 | [models/control_plane.py:264](../../backend/app/models/control_plane.py) |
| 🟡 | `due_at` 定义了但**零超时/到期处理**，逾期交接不被标记/提醒/阻断 | [models/control_plane.py:934](../../backend/app/models/control_plane.py) |
| 🟡 | 状态变更端点大多不加行锁（read-then-write 竞态）；并发 analyze 造重复 HandoverItem（无唯一约束） | [control_plane.py:2237,2432,2641](../../backend/app/api/v1/endpoints/control_plane.py) |
| 🟡 | 前端复制 FSM 转移条件，且给 v2 acceptance 多加了一道后端没有的门（绑到 v1 `verifying/completed`） | [HandoverDetailPage.tsx:448](../../frontend/src/pages/HandoverDetailPage.tsx) |
| 🟡 | 证据归属靠 `object_uri` 路径字符串前缀匹配，换存储布局即静默漏证据 → 改造时用真外键 | [HandoverDetailPage.tsx:153](../../frontend/src/pages/HandoverDetailPage.tsx) |
| 🟡 | 交接相关读端点（`GET /handovers`、`/evidence`）无 namespace 过滤 → 改造时补 | [control_plane.py:1977](../../backend/app/api/v1/endpoints/control_plane.py) |

**ingestion 骨架改造**：`CollectionJob` 空转（发起后无消费者，永停 PENDING）在改为「事件触发投递」后自然消失；reporter 鉴权 + 作业队列 + `artifact_service` 对象存储层直接复用（见向量能力查证）。

### 4.3 🗑 退场模块 — 记录存档、不修

以下缺陷所在模块将退场，**不投入修复**（仅登记以防误改）：

- v1 采集全链：reporter cron 上报、WorkTrace 采集、analysis 报告分析、collection-jobs。
- 纯展示面：个人工作台、agent-overview、资产中心（control-plane 的 asset/runtime/worktrace CRUD）。
- 跨租户读端点中属退场部分：`GET /worktraces`、`/collection-jobs`、`/reports/upload-sessions` 无 namespace 过滤（[control_plane.py:1795,1261,1269](../../backend/app/api/v1/endpoints/control_plane.py)）。
- GA 授权（spec 016）：冻结，含两份失败证据（见 §5）。
- ~~v1 Skills 发布门禁绕过~~ → **已因 O1（Skills v1 保留）升级为 P0 要修，移至 §4.1**。见下方「发布门禁 v1 / Skills」表。

---

## 5. 开放决策项（已于 2026-08-10 裁决，结论见 §3.1）

| # | 决策 | 建议 |
|---|---|---|
| O1 | **Skills 注册表 v1（clawhub/registry）vs Agent Package Registry v2** 去留。两代并存已被 specs 漂移确认，ai_asset 是公共锚点 | clawhub 作 CLI 兼容层可留，但**发布门禁统一到 v2**；若 Skills v1 退场，§4.3 的 v1 门禁绕过一并作废不修 |
| O2 | **两份失败证据定性**（`015/capacity-limit-probe-300rps-failed`、`016/capacity-harness-membership-failed`），而 015 Status 是「RC completed」 | 按宪法 VII，无显式豁免记录即**未闭环**；补豁免记录或将 015 降级 |
| O3 | 交接 → 改造 vs 退场 最终确认 | 建议**改造**为「交接知识制品库」，复用 FSM + 四眼审批 |
| O4 | specs 治理规则：修宪（轻量 evidence-driven）vs 补作业 | 建议**修宪**，底线：tasks 必须含 checkbox（机器可判） |
| O5 | tenant vs namespace 正式钉死 | CONTEXT.md 已按现状（Namespace 唯一隔离）记录；确认后清理 001 spec 矛盾文本 |
| O6 | 文档漂移处理原则 | 采纳「不固化易变计数」，本次一并清理 CLAUDE.md + architecture.md |

---

## 6. 优先级 Backlog

- **P0（安全，保留模块，立即）**：RBAC 提权链（§4.1 🔴）、Release Control 门禁绕过链（🔴/🟠）、**v1 Skills 发布门禁绕过**（O1 保留后升级，含 ClawHub 审批绕过 🔴）。这三组都是「产品不可信」级别，且全在**保留**模块。
- **P1（保留模块正确性）**：前端幂等键失效、权限缓存永不刷新、RBAC UI 断头路、门禁决策易失、EvalHub 白屏。
- **P2（转型前置）**：落地 [ADR-0213](../adr/0213-collection-core-to-knowledge-service.md) + [CONTEXT.md](../../CONTEXT.md)；新建 `specs/<NNN>-knowledge-service/`（data-model + contracts）；文档漂移清理（O6）；交接改造设计（O3）。
- **P3（退场执行）**：删 v1 采集线与纯展示面；冻结 spec 016；解决 009 编号冲突。

---

*方法论说明：本报告的缺陷条目均有代码依据（file:line）。分流依据是 2026-08-10 grilling 确立的方向；若方向调整（尤其 O1/O3），退场/保留/改造归类需相应重算。*
