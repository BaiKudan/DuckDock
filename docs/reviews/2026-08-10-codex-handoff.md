# DuckDock 转型执行 — Codex Handoff（2026-08-10）

本文件是给执行方（codex）的**可执行任务包**。缺陷卡片经独立子代理逐行核实（confirm / root-cause / fix / tests），并做过一轮 adversarial 复核 —— 其中 1 条被**反驳为误报**（§3.7），保留为「不要改」护栏。

## 0. 怎么用这份文件

先读四份上下文，缺一不可：
1. [CONTEXT.md](../../CONTEXT.md) — 术语（知识制品 / 派生索引 / Namespace…）
2. [docs/adr/0213](../adr/0213-collection-core-to-knowledge-service.md) — 方向决策与否决理由
3. [本目录/2026-08-10-project-review.md](./2026-08-10-project-review.md) — 缺陷全景 + 6 裁决 + backlog
4. [.specify/memory/constitution.md](../../.specify/memory/constitution.md) — 硬约束（先红后绿 / 关键链路真 MySQL 8 / 空库须 `alembic upgrade head`）

**执行纪律**：
- 严格按 §2 顺序，先做完 P0（§3）再碰其他。**每个 P0 项单独提交，停下等确认，再做下一项** —— 授权/门禁改动不可批量。
- 每个修复**先写失败测试再改代码**。授权/门禁/迁移类必须有真 MySQL 集成测试（`TEST_MYSQL_URL`，`conftest.py:97`）；纯逻辑/前端在默认 sqlite/jsdom lane 即可。
- **不碰退场模块**（v1 采集线、个人工作台 / agent-overview / 资产中心、GA spec 016）—— 那些缺陷标「记录不修」。
- handoff 与代码现状不符时**停下报告**，不要自行猜测。

## 1. 全站注意事项（跨卡片）

- **迁移单一真实 head = `20260804_0062_operations_slo_recovery`（共 62 个文件）**。CLAUDE.md 的「0034」、以及任何卡片里出现的「0035」都是过时/笔误。三条新迁移（§3.3 必做、§3.2 与 §3.4-E 可选）若都落，**必须线性串成 `0063 → 0064 → 0065`，绝不各自 fork off 0062**。每条「先 `UPDATE` 回填、后 `ADD` 约束」并提供可逆 downgrade。
- **共享测试基座**：缺陷 1/2/3/4 都会打破现有「单 actor / fail-open」夹具与单操作者 demo 种子（见记忆 `demo-data-and-product-demo-deck`）。**一次性引入「双用户（author + 独立 reviewer/admin）」+「缺 license / 未扫描」共享夹具**，跨卡片复用，别每卡各造。
- **共享函数 `create_release_promotion`（release_promotion_service.py:644）**：§3.2 与 §3.3 同改此函数、共享 dispatch document/digest 与迁移链。**所有新守卫必须插在 replay 查询（:716）之前**（与候选审批四眼 :165 同范式），使违规请求永不产生可 replay 的脏行。

## 2. 执行顺序总览

| 顺序 | 缺陷 | 严重度 | 依赖 / 为什么这个位置 | 需真 MySQL |
|---|---|---|---|---|
| 1 | §3.1 RBAC 提权链 | 🔴 critical | 授权地基。`effective_permissions` 是全站权限原语；不先修好，§3.3 装的 admin 门仍可被绕过 | 否（逻辑层 sqlite） |
| 2 | §3.3 晋升授权 / 审批 / SoD | 🟠 high | 依赖 1（admin 门要先有意义）；带**必做迁移**（0063），排迁移链首环 | 是（CHECK 约束） |
| 3 | §3.2 可自选宽松策略版本 | 🔴 critical | 与 2 同改 `create_release_promotion`，同一协调窗口；补「规则门版本时效」轴 | 服务守卫否；可选迁移是 |
| 4 | §3.4 v1 Skills 门禁绕过 | 🟠 high | 文件不与 RBAC/release 重叠，可并行；排后保迁移链线性 | 端到端是 |
| 5 | §3.5 前端幂等键 | 🟠 high | 纯前端，无后端改动无迁移 | 否（Vitest） |
| 6 | §3.6 权限缓存永不刷新 | 🟠 high | 纯前端 store，与 5 同一前端 pass | 否（jsdom） |
| — | §3.7 SHADOW 晋升 | ⚪ refuted | **零代码护栏**：做 2/3 时别误改 enforcement mapping | — |

---

## 3. P0 修复卡片

### 3.1 🔴 RBAC 一步提权到全租户（先做，授权地基）

**缺陷**：两处叠加成一步全租户提权。
- **评估期泄漏（主因）**：`effective_permissions`（[iam_service.py:265](../../backend/app/services/iam_service.py)）累加循环把 `namespace_id IS NULL` 的 SYSTEM/全局绑定所属角色的**全部** permission key 无差别并入，不看每个 key 的 scope → 一个全局绑定若含 `namespace.admin/write`，会在**每个** namespace 生效。
- **配置期缺校验**：`update_role_permissions`（[iam.py:445](../../backend/app/api/v1/endpoints/iam.py)）只验 key 存在，不验 permission.scope 与 role.scope 相容，也不拒 `is_system` 角色（改 is_system 会返回成功但下次 `ensure_builtin_rbac` 静默回滚，响应与行为矛盾）。

**利用链**：`iam.manage` 管理员建 SYSTEM 自定义角色 → 挂 `namespace.admin` → 建全局绑定（`_validate_binding_scope` 放行）→ 目标用户对所有 namespace 的 admin/writer 检查全过。

**修复（三层，A 是安全底线）**：
- **A. 评估期收敛**：累加循环里，对 `binding.namespace_id is None` 的绑定**跳过** `permission.scope == RoleScope.NAMESPACE` 的 key（scope 已由 `selectinload` 预加载，直接取 `row.permission.scope`）。⚠️**只收敛 NAMESPACE，不要收敛 ORG** —— 内置 enterprise-admin 是 SYSTEM 角色且持 `org.*` 全局绑定充当全局 org 管理员，收敛 ORG 会回归破坏。保留 legacy `NamespaceMember` 分支（:238-247）与 `expires_at` 过滤（:252）不动。
- **B. 配置期拦截**：`update_role_permissions` 加 scope 相容校验。⚠️**不能用严格 `perm.scope == role.scope`**（enterprise-admin 是 SYSTEM 却含 ORG，严格相等会让 `ensure_builtin_rbac` 崩）。用相容矩阵：NAMESPACE 角色仅允许 NAMESPACE 权限；ORG 角色仅 ORG；SYSTEM 允许 {SYSTEM, ORG} 但**禁止 NAMESPACE**。抽 helper `_permission_allowed_for_role_scope(role_scope, perm_scope)`，违规 raise 422/409。
- **C. is_system 守卫**：`update_role_permissions`（:447）与 `update_role`（:428）开头 `if role.is_system: raise HTTPException(409)`。

**改动文件**：`iam_service.py:265`、`iam.py:445`、`iam.py:428`。无需迁移（scope 列已存在，跨表一致性在应用层强制）。

**验收（先红后绿，默认 sqlite lane）**：
- SYSTEM 角色 + `namespace.write` RolePermission + 普通用户全局绑定 → `effective_permissions(user, namespace_id=NS)` 断言**不含** `namespace.write`，`has_permission` 为 False。
- `update_role_permissions` 给 SYSTEM 角色挂 `namespace.admin` → 抛 422/409 且权限未变。
- 对内置 `namespace-admin` 调 `update_role_permissions/update_role` → 409，权限不变。
- **回归**：enterprise-admin（SYSTEM+`org.*`）全局绑定下 org 查询仍含 `org.manage`；合法 per-namespace 绑定仍产出 `namespace.admin`；`ensure_builtin_rbac` 不因 B 报错。

### 3.2 🔴 可自选宽松策略版本绕过门禁（与 3.3 同窗口）

**缺陷**：`evaluate_release_policy`（[release_control_service.py:1010](../../backend/app/services/release_control_service.py)）直接用请求传入的 `policy_version_public_id`，`ReleasePolicyVersion` 无 active/superseded 概念，端点只要 `require_namespace_writer`。developer 可挑历史 SHADOW / 规则宽松的旧 ENFORCE 版本评估拿 ALLOW，再晋升。

**修复（第 1 层必做）**：在 `create_release_promotion`（release_promotion_service.py:644，:651 载入 decision 之后、:657 判定之前）插入：
- 新增 `get_effective_policy_version(db, *, policy_id)` 返回该 policy 的 `max(version)`（同 :314-321 求 max 模式）。
- 断言 `decision.policy_version.version == effective.version`，否则 `raise ReleaseControlStateError("promotion requires the current Release Policy version")`。
- 断言 `decision.policy_version.mode == ReleasePolicyMode.ENFORCE`（SHADOW/WARN 不得授权晋升），否则 raise。
- 把 `policy_version_public_id / 版本号 / mode / content_digest` 纳入 dispatch `document`（:701-714）再 `_digest`（新字段需确定性序列化，避免误判 replay 冲突）。

**修复（第 2 层，可选彻底堵洞）**：迁移 `0064`（接 0063）给 `release_environments` 加 `governing_policy_id`（FK→release_policies, RESTRICT，可空但 protected/PRODUCTION 应用层要求非空）；晋升按环境的治理 policy 解析当前 ENFORCE 版本。

**改动文件**：`release_promotion_service.py:644/657/701`、`release_control_service.py:293/1010`、`models/release_control.py:147`、（可选）迁移 `0064`。

**验收**：先红——同 candidate 建 SHADOW v1 + ENFORCE v2（v2 会 FAIL），用 v1 评估得 ALLOW，批准齐后晋升应抛 `ReleaseControlStateError`（sqlite 可测）；WARN + `acknowledge_warnings=True` 仍因 mode≠ENFORCE 被拒；**回归**：`test_release_approval_exception_runtime_receipt_canary_and_rollback`（用当前 ENFORCE passing_version）仍绿。
⚠️只实现「当前 ENFORCE 版本可晋升」门，**不要**顺手实现 §3.7 被否决的 SHADOW 守卫。

### 3.3 🟠 晋升权限最低 + 默认零审批 + 无职责分离（带必做迁移 0063）

**缺陷（四处叠加）**：
1. `dispatch_release_promotion`（[release_control.py:564](../../backend/app/api/v2/endpoints/release_control.py)）只要 `require_namespace_writer`（=DEVELOPER），而建环境/策略/审批/回滚都要 `require_namespace_admin`。
2. `minimum_approvals` 默认 0（[schemas/release_control.py:56](../../backend/app/schemas/release_control.py)），CHECK 仅 `protected=1 OR minimum_approvals=0`，可建 protected PRODUCTION 却零审批。
3. `create_release_promotion`（:637）不校验 `actor.id not in distinct_reviewers`（晋升者可自审）。
4. `create_release_candidate_evaluation_review`（[release_candidate_evaluation_service.py:442](../../backend/app/services/release_candidate_evaluation_service.py)）不校验 `actor.id != binding.created_by_user_id`（自建证据自批）。

**修复**：
- **A. 端点提权**：:564 改 `require_namespace_admin`（与 rollback :640 对齐；顺带评审 canary :611 是否也升 admin）。
- **B. 审批下限**：`validate_protection` 加 `PRODUCTION 且 minimum_approvals < 1 → ValueError`（建议扩到 `protected ⇒ ≥1`）；`models/release_control.py:161` 加 `CheckConstraint("kind <> 'PRODUCTION' OR minimum_approvals >= 1")`；**迁移 0063**：先 `UPDATE release_environments SET minimum_approvals=1 WHERE kind='PRODUCTION' AND minimum_approvals<1` 回填，再加约束，downgrade 删约束。
- **C. 晋升 SoD**：`create_release_promotion` 拿到 approved 后、构造 digest 前 `if target_environment.protected and actor.id in {r.reviewed_by_user_id for r in approved}: raise`（建议再加 `actor.id == candidate.created_by_user_id` 守卫），置于 replay 查询之前。
- **D. 证据四眼**：`create_release_candidate_evaluation_review` 载入 binding 后、replay 前 `if actor.id == binding.created_by_user_id: raise`（镜像 :165）。

⚠️**B 与 C 必须同批**：min_approvals=0 时 approved 为空，SoD 永不触发。

**验收**：pydantic 单测（PRODUCTION+protected+min_approvals=0 → ValueError）；service 单测（actor 属审批者集合 → raise；actor==binding author → raise）；**API/RBAC 集成**（DEVELOPER POST 晋升端点应 403 —— 必须走 endpoint 层，service 直调绕过授权）；**真 MySQL**（迁移后插 PRODUCTION+min_approvals=0 触发 IntegrityError，回填 UPDATE 使既有行不违约）。

### 3.4 🟠 v1 Skills 发布门禁多条绕过（O1 保留后升级）

**缺陷（逐行核实，第 5 条为 partially）**：
1. ClawHub 兼容导入 `_publish_compat_skill`（[clawhub.py:530](../../backend/app/api/v1/endpoints/clawhub.py)）从 tag 派生 `share_public` 后调 `set_public_release`（:668）**不传** policy / license / risk 四项 —— 原生路径 `skills.py:778-786` 对这四项强制。
2. `set_public_release`（[public_release_service.py:60](../../backend/app/services/public_release_service.py)）policy=None 时短路默认 `APPROVED`，license_attested/risk 保持 False → 无 license 归属即对外公开。
3. `require_sandbox_success` fail-open（[release_gate_service.py:172](../../backend/app/services/release_gate_service.py)）：`sandbox_run is None` 时整条 AND 短路跳过；ClawHub 只建 ScanResult 不建 SandboxValidationRun。
4. scan 门只拦 `FAILED`（:208），None/PENDING/RUNNING 一路下行可达 PRODUCTION（:227）。
5. **(partially)** clinic 评测与 skill/version 无关（:244 只按 namespace+COMPLETED 取最新；`ClinicEvaluation` 模型只有 namespace_id）→ A skill 的评测满足 B skill 门禁。

**修复（fail-closed）**：
- **A**：`_publish_compat_skill` 的 share_public 分支复用 `skills.py:778-786` 四项校验（`clawhub_publish_skill:924` 需新增解析 license 字段），透传 policy 等给 `set_public_release`。
- **B**：`set_public_release` policy=None 默认 **PENDING** 而非 APPROVED；APPROVED 前若 `not (license_attested and risk_acknowledged and license_name)` 降级 PENDING/raise。
- **C**：:172 改 `if policy.require_sandbox_success and (sandbox_run is None or sandbox_run.status != PASSED):` 追加 `sandbox_not_passed`。
- **D**：:208 前加 `scan is None or status in {PENDING, RUNNING}` → `scan_incomplete`，final_status 落 REVIEW 不得 PRODUCTION；保留 `WARNED` 仍放行（勿误伤 `scan.py:13` medium 语义）。
- **E（clinic，需产品决策）**：先按 **namespace 级**明确语义（删/注释 `evaluate` 的误导 `skill` 形参）；per-skill 门禁作为**独立 spec**（要迁移给 `ClinicEvaluation` 加 skill_id/version_id），别在此 PR 顺手做。

**改动文件**：`clawhub.py:530/666/924`、`public_release_service.py:60`、`release_gate_service.py:172/208/244`、`models/clinic.py:29`。

**验收**：`set_public_release(shared=True, policy=None)` → PENDING（sqlite）；`evaluate(require_sandbox=True, sandbox_run=None, scan=PASSED)` → 含 `sandbox_not_passed` 且非 PRODUCTION；`evaluate(scan=PENDING)` → 含 `scan_incomplete` 非 PRODUCTION；**真 MySQL 端到端** `POST /clawhub/skills tags=['public']` 缺 license → 422 或 `is_publicly_available()==False`；**回归**：原生 publish 带全 license 字段仍正常 PRODUCTION。

### 3.5 🟠 前端幂等键失效（纯前端）

**缺陷**：`operationKey()=\`${prefix}-${Date.now()}-${random}\``（[ReleaseControlPage.tsx:47](../../frontend/src/pages/ReleaseControlPage.tsx)、OperationsPage.tsx:24）每次点击产生新 Idempotency-Key，后端按 `(namespace_id, idempotency_key)` 去重的重放锚点失效 → 双击**晋升/执行交接/创建快照**产生两条记录（这些表无二级唯一约束）。`requestException` 的 `expires_at=Date.now()+24h` 也参与后端 digest，需一并稳定。

**修复**：一次性写操作用**目标身份派生键**（镜像 `HandoverDetailPage.tsx:418` 既有稳定键）：晋升 `ui-promotion-${candidate.public_id}-${decision.public_id}`、审批 `ui-approval-...`、例外 `ui-exception-${decision.public_id}`（同时把 `expires_at` 用 useMemo 稳定化）、执行交接 `exec-${handover.id}`、快照 `snapshot-${handover.id}`。**可重复测量类**（SLO 评估 OperationsPage:94、canary :443）用 `crypto.randomUUID()`。键长 ≥8（`release_evidence.py:51` 约束）。删除/改名裸 `operationKey` 防误用。**无需 DB 迁移**（后端重放逻辑已正确）。

**改动文件**：`ReleaseControlPage.tsx`（:47/346/360/376/392-393/407/433/443/457）、`OperationsPage.tsx:24/94`、`HandoverDetailPage.tsx:321/575`。

**验收（Vitest）**：mock client，对同一 candidate+decision 连调 `dispatchPromotion()` 两次 → 断言 `createPromotion` 两次收到**相同** idempotency_key（现状红）；`requestException` 两次 → `expires_at` 也相同。⚠️测试必须**模拟同一 handler 调两次**，不是 UI 双击（busy 位会掩盖）。

### 3.6 🟠 权限缓存永不刷新（纯前端 store）

**缺陷**：`store/auth.ts` 的 `persist` 无 `partialize`，把 `permissionsLoaded/permissionKeys/system_role` 持久化到 localStorage；`Layout.tsx:45` `if (!accessToken || permissionsLoaded) return` 在 reload 后短路，`getMyPermissions()` 永不重跑；401 拦截器无限续期会话 → 管理员改绑定后用户一直用登录时快照，直到手动登出。

**修复**：给 `persist` 加 `partialize: (s) => ({ accessToken: s.accessToken, refreshToken: s.refreshToken, user: s.user })` —— **只持久化这三项**（⚠️必须保留 `user`，否则丢 `system_role` 会 reload 登出），不持久化 `permissionKeys/permissionsLoaded`。reload 后 `permissionsLoaded` 回落 `false`，`Layout` 每会话重新拉取。**可选纵深防御**：TTL / 路由变更再拉取，或 403 分支置 `permissionsLoaded=false`。

**改动文件**：`store/auth.ts:48`、（可选）`Layout.tsx:44`、`client.ts:39`、`test/auth-store.test.ts:44`。

**验收（Vitest/jsdom，无需 MySQL）**：`setPermissions([...])` 后 `JSON.parse(localStorage['duckdock-auth']).state` 断言**不含** `permissionsLoaded/permissionKeys`（现状红）；预置带 `permissionsLoaded:true` 的 localStorage 触发 rehydrate → `getState().permissionsLoaded===false`；Layout 挂载（`permissionsLoaded=false`）断言 `getMyPermissions` 恰调一次。保留既有 logout 清零测试绿。

### 3.7 ⚪ SHADOW 晋升 —— 已反驳，不要改（护栏）

原报告列的「SHADOW 模式 FAIL 候选无声晋升」经复核为**误报**：要到 PRODUCTION 仍须走 DEV→STAGING→CANARY 且满足环境的 `minimum_approvals`/四眼，SHADOW 只是「记录失败规则不阻断」，正是 `specs/011-release-control/spec.md §4.5` 的设计。**codex 不得改 enforcement mapping 或晋升门** —— 那会让代码与 spec 脱钩、违反 spec-drives-code。做 §3.2/§3.3 时注意别把 SHADOW 守卫混进 `create_release_promotion`。若组织确实要收紧「PRODUCTION 环境禁止绑 SHADOW policy」，**先改 spec 再实现**。

---

## 4. P1 — 其余保留模块正确性（P0 之后）

见 [审查报告 §4.1/§4.2](./2026-08-10-project-review.md)。要点：
- 门禁决策易失（EvalHub 门禁只存 React state、后端无 GET）—— 让门禁结论成为可查询事实（补 GET + 前端读后端）。
- RBAC UI 断头路（403 提示指向不存在的 RoleBinding UI）—— 建绑定/角色/配权限的管理页。
- EvalHub `Promise.all` 26 请求无 catch 全页白屏 → 改 `Promise.allSettled` + 逐项降级。
- `field-control` CSS 类不存在（30 处表单裸奔）→ 补类或改用 `.input-control`。
- 数据一致性：handler 中途 `commit` 拆事务（违反宪法 III）→ 收敛事务边界。

## 5. P2 — 转型工作（知识服务）

- **新建 `specs/017-knowledge-service/`**（避开 009 编号冲突），按宪法补 spec + plan + tasks(带 checkbox) + **data-model**（`KnowledgeArtifact` / 三层可见性 / 派生索引重建规则）+ **contracts**（投递 REST API、检索 API、MCP `archive_artifact` / `search_knowledge` schema）。
- 复用清单（查证已确认）：`artifact_service`（对象存储层）、reporter 鉴权、作业队列骨架、embedding adapter seam；新建：成果物文件一等化、内容提取（PDF/图文→text）、ingest 向量化、LanceDB 派生索引 + 检索、qwen 多模态 embedding + rerank。
- **交接改造**（O3）：把 handover 语义改为「交接知识制品库」，改造时顺手修 §3.3 未覆盖的交接 FSM 缺陷（`CANCELLED` 不可达、v2 acceptance `REJECTED` 无守卫、acceptance 只要读权限、证据 object_uri 字符串匹配归属 → 真外键）。见[审查报告 §4.2](./2026-08-10-project-review.md)。
- **文档清理**（O6）：CLAUDE.md / architecture.md 改「指向命令」不固化计数；修 008 Status 矛盾、009 编号冲突。

## 6. P3 — 退场执行

- 删 v1 采集线（reporter cron / WorkTrace 采集 / analysis 报告分析 / collection-jobs）与纯展示面（个人工作台 / agent-overview / 资产中心）。
- 冻结 GA spec 016；015 的 "RC validation completed" 降级标注「含未闭环 300rps 容量项」（O2）。
- 清理 001 spec 的 tenant/namespace 矛盾文本（O5，Namespace 为唯一隔离单元）。

---

*卡片来源：2026-08-10 深挖 workflow（7 agent 独立核实 + adversarial 复核 + 依赖排序综合）。所有 file:line 基于 head `20260804_0062` 时的代码；codex 执行前应以当前代码为准复核行号。*
