# DuckDock 收尾开发计划（specs/005 execution backlog）

## 0. 背景与既定决策

DuckDock 当前已完成 L0（可信收口 8 项全 merged）、L2 AI 链统一与降级显式化（后端契约完整）、P3 后端交接状态机（`draft→…→completed` FSM + 回执 + 审批 + 校验），以及 L4 的部分硬化（`docker-compose.prod.yml` 基线、限流、审计、结构化日志、备份脚本）。收尾的终态是 **"受治理的记录器 + AI 辅助"（governed recorder + AI assist）**，而非全自治控制面。三条已锁定决策：(1) **范围与顺序** = 先收 L2 尾巴，再做 P3 手动交接闭环（含前端），最后做 L4 生产硬化；L1 真实厂商实时采集适配器与 L3 厂商侧/自治执行 **均不在范围内**。(2) **P3 证据规则** = 敏感强制 / 普通可选：仅当底层资产/痕迹敏感度低于 `INTERNAL` **且** 关键度非 high 时，`ExecutionAction` 才允许无证据标记 `DONE`；敏感度 `>=INTERNAL` 或高关键度资产在 `DONE` 前 **必须** 附 `evidence_id`。(3) **L4 部署** = 单/少机 Docker Compose + 外置加密密钥（SOPS/age，密文入库、启动时解密注入），**非 Kubernetes**；应用层硬化（限流、审计留存、备份/恢复、健康/就绪、结构化日志/指标）在范围内。

## 1. 执行顺序总览

1. **L2-TAIL — AI 辅助降级收口**（目标：把已存在的后端 `ai_assist` 契约一路透出到 release-gate payload 与前端徽章，消除"后端诚实、前端沉默"的断层）。无外部依赖，先做。
2. **P3 — 手动交接闭环**（目标：补齐前端 API client + 交接详情/审批/执行/校验 UI，并在后端落地"敏感强制/普通可选"证据规则与 `ExecutionAction→Evidence` 正向链路，并补 IAM 命名空间越权回归与回执幂等）。依赖 L2-TAIL（前端 client 改动同文件，避免冲突）。
3. **L4 — 生产硬化**（目标：`analysis-worker` 进 prod compose、`0001` 静态 DDL、SOPS/age 密钥（决策 3 强制）、审计留存 GC、就绪探针/优雅停机、compose 资源/健康收口、恢复手册与可观测性钩子）。依赖 P3 完成（功能冻结后再固化部署面）。

依赖图（粗）：`L2-TAIL → P3-FE(client) → P3-FE(UI) → P3-FE(E2E)`；`P3-BE(evidence rule + IAM 回归 + 幂等)` 与 P3 前端并行；`L4-*` 多数互相独立，唯 `L4 SOPS/age` 与 `L4 audit-GC` 需在 compose/CI 收口前落地，`L4-02`（0001 静态 DDL）须在 `P3-03`（新增 evidence_ids 列）之后回归。

## 2. L2-TAIL — AI 辅助降级收口

- **[L2T-01] release-gate clinic payload 透出 ai_assist** — *must*
  - 目标: release gate 当前读取最新 `ClinicEvaluation` 打分却丢弃其 `ai_assist`，导致门禁无法表达"分数本身是 LLM 还是启发式"。
  - 改动: `backend/app/services/release_gate_service.py` — `_clinic_gate_payload()`（方法起于约 line 149，返回的 `clinic_payload` dict 约在 line 191）加入 `evaluation.ai_assist`（存在时）。
  - 验收: 给定带 `ai_assist={mode,degraded,reason}` 的 `ClinicEvaluation`，`clinic_payload["ai_assist"]` 原样携带；扩展 `backend/tests/` 中 release-gate 测试断言该字段透传。
  - 依赖: none
- **[L2T-02]（可选/契约确认后再做）gate_result.technical_checks 扁平暴露 clinic ai_assist** — *should*
  - 目标: 注意 `_build_gate_result()`（起于约 line 194，`technical_checks` 约在 line 218）已通过 `gate_result["clinic"]`（`release_gate_service.py:209` 处的完整 `clinic_payload`）携带 clinic 全量数据。一旦 L2T-01 把 `ai_assist` 写入 `clinic_payload`，前端即可经 `gate_result.clinic.ai_assist` 读取，**无需任何额外后端改动**。
  - 改动: **仅当**确有"`technical_checks` 必须有扁平 `clinic_ai_assist` 键"的硬契约需求时，才在 `_build_gate_result()` 的 `technical_checks` dict 加 `clinic_ai_assist`（取自 L2T-01 的 `clinic_payload`）。否则 **本任务不写任何代码**，仅在 L2T-05 注明前端读 `gate_result.clinic.ai_assist`。
  - 验收: 若执行——`gate_result["technical_checks"]["clinic_ai_assist"]` 出现且测试断言；若跳过——确认 `gate_result["clinic"]["ai_assist"]` 路径已由 L2T-01 覆盖（避免重复落库同一数据）。
  - 依赖: L2T-01
- **[L2T-03] 前端类型补 ai_assist** — *should*
  - 目标: TS 层缺字段导致徽章无法类型安全消费。
  - 改动: `frontend/src/api/client.ts` — 给 `EvaluationSummary`/`EvaluationFull` 与 `ScanResult` 接口加 `ai_assist?: { mode: string; degraded: boolean; reason: string | null }`。
  - 验收: `npm run build` / `tsc` 通过；徽章组件可读取该字段无 `any`。
  - 依赖: none
- **[L2T-04] ClinicReportPage 降级徽章** — *should*
  - 目标: 报告页无任何 AI/启发式来源指示。
  - 改动: `frontend/src/pages/ClinicReportPage.tsx` — 总分环旁加徽章："AI 辅助"（`mode=llm && !degraded`）或 "启发式降级"（`degraded` 或 `mode=baseline`），hover 显示 `reason`。
  - 验收: 两种 `ai_assist` 输入下徽章文案/样式正确；扩展 Vitest 一例渲染断言。
  - 依赖: L2T-03
- **[L2T-05] SkillDetailPage 门禁区 ai_assist 徽章** — *should*
  - 目标: release-gate 展示区缺 clinic AI 来源指示。
  - 改动: `frontend/src/pages/SkillDetailPage.tsx`（clinic 分数 pill 附近，约 line 234）读取 clinic `ai_assist` 渲染徽章——优先经 `gate_result.clinic.ai_assist`（L2T-01 已提供），仅当 L2T-02 落地扁平键时改读 `gate_result.technical_checks.clinic_ai_assist`。
  - 验收: 门禁结果含 clinic ai_assist 时显示对应徽章；无字段时不渲染。
  - 依赖: L2T-01（+ L2T-03；L2T-02 视情）

## 3. P3 — 手动交接闭环

> 证据规则锚点（已确认）：资产关键度读 `AIAsset.criticality`（`models/control_plane.py:543`，枚举 `Criticality` line 156），痕迹敏感度读 `WorkTrace.sensitivity`（`models/control_plane.py:707`，枚举 `Sensitivity` line 180，默认 `INTERNAL`）。`HandoverItem.asset_id`→`AIAsset`，再经 `asset_id` 关联 `WorkTrace`。规则：**当 `asset.criticality in {HIGH, CRITICAL}` 或任一关联 `WorkTrace.sensitivity >= INTERNAL`（即不为低于 INTERNAL 的级别）时，`evidence_ids` 必填**，否则可选。

- **[P3-01] 回执加载资产关键度/敏感度上下文** — *must*
  - 目标: `complete_execution_action` 当前不加载资产，无法判定证据是否强制。先打通上下文读取。
  - 改动: `backend/app/api/v1/endpoints/control_plane.py`（`complete_execution_action`, 约 line 2035-2042）— 经 `action.handover_item_id`→`HandoverItem.asset_id` 加载 `AIAsset` 及其关联 `WorkTrace`；将 `asset_criticality` 与最高 `trace_sensitivity` 写入 `action.result_json` 供审计。
  - 验收: 回执完成后 `result_json` 含 `asset_criticality`/`trace_sensitivity`；`backend/tests/test_execution_receipt.py` 扩展断言上下文落库。
  - 依赖: none
- **[P3-02] ExecutionReceipt 加 evidence_ids 并落地敏感强制规则** — *must*
  - 目标: 实现"敏感强制 / 普通可选"证据门禁。
  - 改动: `backend/app/schemas/control_plane.py` — `ExecutionReceipt` 加 `evidence_ids: list[int] = []`；`backend/app/api/v1/endpoints/control_plane.py` `complete_execution_action` — 当 P3-01 判定为 `criticality in {HIGH,CRITICAL}` 或 `sensitivity >= INTERNAL` 时，`status==SUCCEEDED/DONE` 而 `evidence_ids` 为空则 `422`；校验每个 `evidence_id` 存在且属于本 case；审计 `evidence_ids`。
  - 验收: 高关键度/敏感资产无 `evidence_ids` 标 DONE → `422`；普通低敏资产无证据可成功；`test_execution_receipt.py` 增"敏感拒绝/普通放行"两例。
  - 依赖: P3-01
- **[P3-03] ExecutionAction→EvidenceItem 正向链路 + 迁移** — *must*
  - 目标: 当前 `USER_CONFIRM` 证据孤立，无法重建某 action 的证据链。
  - 改动: `backend/app/models/control_plane.py`（`ExecutionAction`, 790-810）加 `evidence_ids: list[int]`（JSON 列）；`complete_execution_action` 把新建 `USER_CONFIRM` 证据 ID 与 P3-02 传入的 `evidence_ids` 合并写入；`ExecutionActionOut` schema 暴露 `evidence_ids`；新迁移 `backend/alembic/versions/20260624_0021_execution_action_evidence_ids.py`（带 inspector 幂等守卫 + downgrade）。
  - 验收: 回执响应含 `evidence_ids` 回环；空 MySQL `alembic upgrade head` + `alembic check` 通过；测试断言 round-trip。
  - 依赖: P3-02
- **[P3-04] 敏感回执证据 visibility 推断** — *should*
  - 目标: 关键/敏感资产的回执 note 不应默认 `NORMAL` 可见。
  - 改动: `backend/app/api/v1/endpoints/control_plane.py` 创建 `USER_CONFIRM` 证据处（约 line 2043-2050）— 当 P3-01 判定关键/敏感时 `visibility=SENSITIVE`；审计该判定。
  - 验收: 关键资产回执产出 `SENSITIVE` 证据；`test_execution_receipt.py` 增一例。
  - 依赖: P3-01
- **[P3-05] verify 前置全条目终态校验** — *should*
  - 目标: 当前 `verify` 仅查 FAILED，case 可在 item 卡在 EXECUTING 时被标 COMPLETED。
  - 改动: `backend/app/api/v1/endpoints/control_plane.py` `verify_handover`（2080-2140）— 转 COMPLETED 前断言无 item 处于 `{PROPOSED,APPROVED,EXECUTING}`，否则 `409` 列出未闭合 item id。
  - 验收: 含非终态 item 的 verify → `409`；全 `DONE/FAILED/SKIPPED` 通过；`test_handover_flow.py` 增两例。
  - 依赖: none
- **[P3-06] 收紧 package 构建门** — *should*
  - 目标: 包当前在 `APPROVED`（执行未发生）即可构建，可能交付空执行结果。
  - 改动: `backend/app/api/v1/endpoints/control_plane.py`（package gate, 约 line 2143-2148，当前显式允许 `APPROVED|VERIFYING|COMPLETED`）— 收紧为仅允许 `case.status in {VERIFYING, COMPLETED}`，`APPROVED` 返回 `409`。
  - **行为变更说明**: 当前代码 **有意** 允许 `APPROVED` 以便 receiver 在执行前预览封装包；本任务按审计要求收紧为 `VERIFYING+`，**会移除该 APPROVED 预览能力**。若产品仍需预执行预览，应改为提供独立的只读预览端点而非放宽封包门——此处按锁定方向（执行后才可封包）落地。
  - 验收: `APPROVED` 构建包 → `409`，`VERIFYING+` 可构建；`test_handover_package.py` 增门禁断言并显式记录 APPROVED 预览不再可用。
  - 依赖: none
- **[P3-12] 回执幂等键（ExecutionReceipt.idempotency_key）** — *should*
  - 目标: `complete_execution_action` 无幂等键（不同于 `execute_handover`），网络重试会对已终态 action 返回 `409`。审计要求可选幂等键，使安全重试返回既有终态 action 而非冲突。
  - 改动: `backend/app/schemas/control_plane.py` — `ExecutionReceipt` 加 `idempotency_key: str | None = None`；`backend/app/api/v1/endpoints/control_plane.py` `complete_execution_action` — 当传入 `idempotency_key` 且该 action 已终态、且其记录的 `idempotency_key` 与本次相同时，幂等返回既有 action（200）而非 `409`；将 `idempotency_key` 落入 `action.result_json`（或既有幂等存储），与 P3-01/02/03 的回执写入合并，避免对同端点重复改写。
  - 验收: 同一 `idempotency_key` 重放回执返回既有终态 action（非 `409`）；不同 key 或缺 key 时维持原 `409` 行为；`test_execution_receipt.py` 增重放幂等一例。
  - 依赖: P3-03（与 P3-01/02/03 共址改写同一端点，串行合入）
- **[P3-13] IAM 命名空间越权回归（enterprise-admin 无 namespace binding → skill.publish 403）** — *must*
  - 目标: IAM 审计标记 MUST 级缺口——`enterprise-admin`（`RoleScope.SYSTEM`，持 `org.*`/`iam.manage`）不持有 `namespace.*` 权限，因此在 **无显式 namespace RoleBinding** 时 **不得** 发布/管理 skill。即便当前行为正确，也必须有回归测试锁定该不变量。
  - 改动: `backend/tests/`（IAM/RBAC 测试，如 `test_iam_service.py` / RBAC 套件）— 新增用例：构造 `enterprise-admin` 且 **无** namespace RoleBinding 的主体 → 调 `skill.publish` → 断言 `403`；并补一例：授予该主体显式 namespace RoleBinding 后 `skill.publish` 通过，证明 403 源于缺绑定而非角色本身。若发现实现实际放行（与审计判定相悖）则在 `iam_service`/发布门处补授权检查使其 `403`。
  - 验收: 无 namespace binding 的 enterprise-admin `skill.publish` → `403`；有绑定 → 通过；两例并入 RBAC 套件。
  - 依赖: none
- **[P3-14] 公共分享重开需重新审批** — *should*
  - 目标: 当 `public_sharing_requires_approval=true` 时，将一个 **已 APPROVED** 的 release 的 `is_public_shared` 关闭后再开回 `true`，当前会沿用旧审批直接放行而无需新审批（`backend/app/api/v1/endpoints/skills.py:1084-1095`），是治理漏洞。
  - 改动: `backend/app/api/v1/endpoints/skills.py`（`update_version_sharing`, 约 1084-1095）— 当 `public_sharing_requires_approval=true` 且本次为"由非公开切回公开"（`is_public_shared` 由 false→true）时，重置审批态为 `PENDING`（或要求新审批记录），不得复用上一次 APPROVED；审计该重置。
  - 验收: 已 APPROVED release 关闭分享再开 → 处于待审批而非直接公开；关闭/开启不涉及审批要求时维持原行为；`backend/tests/` 中分享/审批测试增一例。
  - 依赖: none
- **[P3-07] 前端 client 补交接闭环方法** — *must*
  - 目标: client 缺 submit/decide/execute/complete/verify/getHandover 等方法，UI 无法驱动后端闭环。
  - 改动: `frontend/src/api/client.ts` — `controlPlaneApi` 加 `getHandover(caseId)`、`submitHandover(caseId)`、`decideApproval(caseId,approvalId,decision)`、`executeHandover(caseId,itemIds?)`、`completeExecutionAction(caseId,actionId,{status,note,evidence_ids,idempotency_key?})`、`verifyHandover(caseId,{acknowledge_failures,note})`、`createHandoverPackage(caseId)`、`downloadHandoverPackageLink(caseId,reason)`。
  - 验收: 各方法路由到对应后端端点、类型完整、`tsc` 通过；扩展 Vitest 覆盖 client 方法签名/URL。
  - 依赖: L2T-03（同文件，先合 L2T 类型改动）
- **[P3-08] 交接详情页（状态机全视图 + 证据列表/下载 + receiver/owner 编辑）** — *must*
  - 目标: 无单 case 详情页，无法查看 items/审批/执行/证据，且 receiver/owner 转移无入口。
  - 改动: 新建 `frontend/src/pages/HandoverDetailPage.tsx`；`frontend/src/App.tsx` 加路由 `/handovers/:caseId`；展示 case 状态、items（资产名/建议动作/状态）、审批任务、执行动作。**显式包含**：(a) **证据列表区**——逐 action 列出 `evidence_ids` 对应证据（类型/visibility/创建时间）并提供 **下载链接**（受 reveal/签名 URL 控制）；(b) **receiver/owner 转移控件**——owner 可编辑 case 的接收人/责任人字段并提交。
  - 验收: 进入 `/handovers/:caseId` 渲染各区块（含证据列表+下载链接、receiver/owner 编辑控件）；状态随轮询/操作刷新。
  - 依赖: P3-07, P3-03（证据列表需 `ExecutionActionOut.evidence_ids` 已持久化/返回）
- **[P3-09] 审批 + 执行 + 校验 UI（含敏感证据上传 + 手动条目 + 封装包）** — *must*
  - 目标: 把闭环按钮接进详情页，回执表单遵守 P3-02 证据规则，并补封包/手动条目入口。
  - 改动: `frontend/src/pages/HandoverDetailPage.tsx` — 审批任务决策按钮（approve/reject→`decideApproval`）；执行区 `executeHandover` 触发；每个 action 回执表单（status=succeeded/failed + note + 敏感资产时强制 `evidence_ids` 选择，对应 P3-02 的 `422`；可选 `idempotency_key` 透传，呼应 P3-12）；校验区 `verifyHandover`（失败动作告警 + `acknowledge_failures` 勾选）；**封装包区**（仅 `VERIFYING+` 显示构建/下载，呼应 P3-06）；**手动交接条目**（nice：手动新增/标记 item 的入口，若后端支持）。
  - 验收: 全链路 approve→execute→complete→verify→completed 可在 UI 走通；敏感资产无证据时前端拦截并显示后端 `422`；封包区仅在 `VERIFYING+` 出现；手动条目入口（若实现）可创建/标记 item。
  - 依赖: P3-08, P3-02, P3-03, P3-06
- **[P3-10] analyze 结果 ai_assist 徽章** — *should*
  - 目标: 前端忽略 analyze 的 `X-AI-Assist` 头/`ai_assist` 字段。
  - 改动: `frontend/src/pages/ControlPlanePage.tsx`（analyze 调用处）+ `frontend/src/pages/HandoverDetailPage.tsx` — 读取响应 `ai_assist`/头，显示 "LLM" 或 "规则降级" 徽章 + tooltip。
  - 验收: 降级与 LLM 两态徽章正确；扩展前端测试一例。
  - 依赖: P3-07
- **[P3-11] 交接闭环 Playwright E2E** — *should*
  - 目标: 无 E2E 覆盖 approve→execute→verify→completed（specs/002 FR-005 Phase 2 / SC-003）。
  - 改动: `frontend/playwright.config.ts`（新建）+ `frontend/e2e/handover-closeloop.spec.ts`；覆盖登录→创建 case→指派审批→审批决策→执行→回执→校验→completed，并断言权限门控显隐；接入 `.github/workflows/ci.yml` 前端 job（先 non-blocking，稳定后转 blocking）。
  - 验收: `npx playwright test` 通过；CI 跑该 job。
  - 依赖: P3-09, P3-10

## 4. L4 — 生产硬化 (Compose + SOPS/age)

- **[L4-01] analysis-worker 进 prod compose** — *must*
  - 目标: `analysis-worker` 仅在 dev compose 的 profile 中，prod 缺该 service，可选分析链在生产无法运行。
  - 改动: `docker-compose.prod.yml` — 加 `analysis-worker` service（build/env/`depends_on: migrate,minio-init`），注入 `DUCKDOCK_ANALYSIS_API_BASE`、`DUCKDOCK_ANALYSIS_WORKER_TOKEN` 等；`restart: unless-stopped` + 资源上限。
  - 验收: `docker compose -f docker-compose.prod.yml config` 含该 service；`docker compose --env-file .env.prod up` 启动 analysis-worker。
  - 依赖: none
- **[L4-02] 0001 baseline 固化为静态 DDL** — *must*
  - 目标: `0001` 仍用 `Base.metadata.create_all()`，违背 DM-01-FULL（漂移基线不可静态校验）。
  - 改动: `backend/alembic/versions/20260409_0001_baseline_schema.py` — 用显式 `op.create_table`/索引/约束 DDL 替代 `create_all`；保持 downgrade 重建到上一基线态。
  - 验收: 空 MySQL `alembic upgrade head` + `alembic check` 无漂移；`test_migration_drift_gate.py` 通过。
  - 依赖: **P3-03**（漂移门 `test_migration_drift_gate.py` 比对 live models；P3-03 已在 `ExecutionAction` 加 `evidence_ids` 列，故 0001 静态 DDL 必须在 P3-03 之后回归，确保手写基线 + 后续迁移叠加后与 live models 零漂移）
- **[L4-03] 审计日志留存策略 + GC sweep** — *must*
  - 目标: 审计日志无限增长，`run_retention_gc` 仅清 skill 版本。
  - 改动: `backend/app/core/config.py` 加 `AUDIT_LOG_RETENTION_DAYS=90`；`backend/app/workers/lifecycle_tasks.py` 在 `run_retention_gc`/`_async_gc` 中删 `created_at < cutoff` 的 `audit_logs`，并清理过期 `public_skill_releases`（呼应 OPS-SWEEP），日志输出删除行数；如需策略表则加 `models/lifecycle.py` + 迁移（带幂等守卫）。
  - 验收: sweep 删除超期审计/过期发布并记数；`test_retention_schedule.py` 扩展断言派发与删除。
  - 依赖: none
- **[L4-04] SOPS/age 加密密钥基础设施** — *must*
  - 目标: 落地决策 3——密文入库、启动解密注入，取代裸 `.env.prod`。**此为 L4 终态强制项**：外置加密密钥是锁定决策 3 规定的生产密钥机制，非可选。
  - 改动: 新建 `.sops.yaml`（age key 策略）；加密 `.env.prod.example`→`.env.prod.enc`（密文可入库）；`scripts/prod.sh` 在 compose up 前校验 age key 可用并 `sops -d .env.prod.enc > .env.prod`，运行后清理；**缺 age key 时必须硬失败**，不得回退裸 `.env.prod`。
  - 验收: 缺 age key 时 `prod.sh` 失败并提示；提供 key 时解密成功后正常 `up`；文档说明轮换流程。
  - 依赖: none
- **[L4-05] /readyz 就绪探针（依赖连通性）** — *should*
  - 目标: 无 `/readyz`，编排无法区分"存活"与"就绪"。
  - 改动: `backend/app/main.py` — 加 `GET /readyz`，检查 MySQL/Redis/MinIO 连通，全通 `200 {ready:true}` 否则 `503`；`docker-compose.prod.yml` backend healthcheck 指向 `/readyz` 并加 `start_period: 30s`（避让迁移期）。
  - 验收: 依赖断开时 `503`；`test_prod_ops_config.py` 扩展断言端点与 compose 配置。
  - 依赖: L4-01（worker/服务定义稳定后统一收口）
- **[L4-06] FastAPI + Celery 优雅停机** — *should*
  - 目标: 无 lifespan/信号处理，停机可能截断在途请求/任务。
  - 改动: `backend/app/main.py` 加 lifespan（停机时 flush 日志上下文、drain 连接、等在途请求带超时）；`backend/app/workers/celery_app.py` 加 SIGTERM 处理（停收新任务、drain 当前批 60s、关连接）。
  - 验收: `docker compose stop` 期间无 SIGKILL；`test_celery_reliability.py` 断言信号注册。
  - 依赖: none
- **[L4-07] prod compose 资源/健康/非 root 收口** — *should*
  - 目标: backend 缺内存上限、beat/redis/minio 缺健康检查、backend 镜像以 root 运行。
  - 改动: `docker-compose.prod.yml` — backend `deploy.resources.limits.memory: 2g`、redis 限额 + 健康检查、minio 健康检查（`/minio/health/live`）、beat 健康检查（`celery ... inspect ping`）、frontend 限额；`backend/Dockerfile` 加 `useradd appuser` + `USER appuser`。
  - 验收: `docker compose config` 含上述；`docker run --rm <backend-image> id` 非 root；`test_prod_ops_config.py` 扩展断言。
  - 依赖: L4-01
- **[L4-08] SCAN_LLM_TIMEOUT_SECONDS 统一为 settings 驱动** — *should*
  - 目标: scanner deep-scan 用硬编码 `60s` LLM 超时，clinic 已走 settings 值；审计要求统一为 settings 驱动 `SCAN_LLM_TIMEOUT_SECONDS`，避免两处不一致与不可调。
  - 改动: `backend/app/core/config.py` 加 `SCAN_LLM_TIMEOUT_SECONDS`（默认与现行一致，如 60）；scanner deep-scan 路径（scanner engine / scan service）改读该 settings，移除硬编码 `60`。
  - 验收: scanner deep-scan 超时取自 settings；改 settings 即生效；`backend/tests/` scan 测试断言读取 settings 值。
  - 依赖: none
- **[L4-09] Sandbox 后端就绪自检端点（check_readiness）** — *should*
  - 目标: 审计要求把 `SandboxValidationService.check_readiness()` 经 API 暴露，使运维在提交前看到后端沙箱配置缺口。
  - 改动: `backend/app/services/`（`SandboxValidationService`）补/暴露 `check_readiness()`（探测沙箱所需后端配置/依赖）；`backend/app/api/v1/endpoints/`（scan/sandbox 相关）加只读端点（如 `GET /scan/sandbox/readiness`，RBAC 受限），返回 ready 与缺项清单。
  - 验收: 配置齐备时端点返回 ready；缺项时列出缺口；`backend/tests/` 增一例。
  - 依赖: none
- **[L4-10] ClinicEvaluation.trace_id 存储 + Langfuse 启用文档** — *should*
  - 目标: 审计要求在 `ClinicEvaluation` 存 Langfuse `trace_id`，以打通评估到可观测性回链（呼应 L4 observability profile）。
  - 改动: `backend/app/models/clinic.py`（`ClinicEvaluation`，`trace_id` 落在 `clinic.py:50`；**不在** control_plane.py）加 `trace_id: str | None`（可空列）+ 新迁移（带幂等守卫 + downgrade）；clinic 评估写入处在有 Langfuse trace 时回填 `trace_id`；`docs/`（或 L4-08 runbook）补"启用 Langfuse（observability profile）+ 经 trace_id 回链评估"的路径说明。
  - 验收: 有 trace 时 `ClinicEvaluation.trace_id` 落库；空 MySQL `alembic upgrade head` + `alembic check` 通过；文档含启用路径。
  - 依赖: none（新迁移须排在 P3-03 的 `0021` 之后取下一版本号，且 L4-02 回归须在其后——见 §5）
- **[L4-11] nginx 安全头 + 生产部署 runbook（含三件套恢复）** — *should*
  - 目标: nginx 缺 OWASP 基线头；缺密钥/TLS/恢复 runbook。审计将"恢复手册缺失"评为 should——`scripts/prod.sh` 有备份但 **无** MySQL+repos+MinIO 三件套的恢复脚本/文档，故恢复文档单列并提级到 should。
  - 改动: `frontend/nginx.conf` 加 HSTS/X-Frame-Options/X-Content-Type-Options/CSP（文档化 TLS 上游终止假设）；新建 `docs/production-deployment.md`（age key 设置、SOPS 解密、TLS 反代、凭证轮换）；**新建/补全恢复流程**——`scripts/restore.sh`（或 runbook 明确步骤）覆盖 **MySQL + repos + MinIO 三件套** 从备份的恢复，并与 `scripts/prod.sh` 的备份产物对齐。
  - 验收: `curl -I` 见安全头；`docs/production-deployment.md` + `scripts/restore.sh`（或等价文档化步骤）完整覆盖三件套恢复与 SOPS/轮换流程；至少一次"备份→恢复"演练记录于文档。
  - 依赖: L4-04

## 5. 风险与前置依赖

- **迁移幂等性**：P3-03（`0021`）、L4-03（可选策略表）、L4-10（`ClinicEvaluation.trace_id`）新迁移 **必须** 带 inspector / `has_table`/`has_column` 守卫 + downgrade（遵循现有 0018/0019/0020 模式）；空 MySQL 全链路 `alembic upgrade head` 不得失败。新增迁移版本号按落地顺序在 `0021` 之后递增。（实际落地链：`0021` evidence_ids → `0022` execution_mode 默认归一 → `0023` clinic trace_id，head `0023`。）
- **0001 静态 DDL 风险（L4-02）**：手写 DDL 易与 live models 漂移；落地后必须以 `alembic check` 与 `test_migration_drift_gate.py` 双重把关。**关键顺序**：漂移门比对的是 live models，而 P3-03 会向 `ExecutionAction` 加 `evidence_ids` 列（L4-10 亦改 `ClinicEvaluation`），故 L4-02 必须在 **P3-03（及 L4-10）之后** 回归，确保静态基线 0001 + 后续迁移叠加后与最终 live models 零漂移——此为硬依赖，已在 §4 L4-02 deps 标注，非仅脚注。
- **前端同文件冲突**：L2T-03 与 P3-07 都改 `frontend/src/api/client.ts`，必须按依赖顺序（L2T-03 先）串行合入。
- **回执端点共址改写（P3-01/02/03/04/12）**：上述五个 task 均改 `complete_execution_action` 同一函数（上下文加载、证据规则、evidence_ids 链路、visibility 推断、幂等键），必须按 P3-01→02→03→（04/12）依赖顺序串行合入，避免互相覆盖。
- **证据规则边界（P3-02）**：默认 `Sensitivity` 为 `INTERNAL`，意味着 **绝大多数痕迹都会触发强制证据**；务必把判定逻辑与枚举序定义在一处（建议加常量 `SENSITIVITY_REQUIRES_EVIDENCE`），并写明"低于 INTERNAL 且非 high 关键度方可豁免"的精确分支，防止误放行。
- **package 门收紧的行为变更（P3-06）**：收紧到 `VERIFYING+` 会移除当前 `APPROVED` 预执行预览能力（当前代码有意允许）；若产品仍需预览须另开只读端点，不得以放宽封包门替代。
- **SOPS/age（L4-04，决策 3 强制）密钥分发**：CI/部署机需预置 age 私钥；缺失时 `prod.sh` 必须硬失败而非回退裸 `.env.prod`，否则违背决策 3。
- **E2E 阻断性（P3-11）**：Playwright 先以 non-blocking 接入 CI，稳定后再转 blocking，避免 flaky 阻塞主干。
- **顺序**：`L2-TAIL` 全部 → `P3-07`（client）→ `P3-08/09/10`（UI）→ `P3-11`（E2E）；P3 后端（P3-01..06、P3-12、P3-13、P3-14）可与前端并行（回执端点系列内部串行）；L4 在功能冻结后收口，`L4-02` 在 `P3-03`（及 `L4-10`）之后回归。

## 6. 验收门禁

每个 task 合入前必须全绿（standing gates）：
- **ruff**（F 规则，阻断）：`ruff check backend` 0 error。
- **mypy**：错误数 `<= 95`（只降不升 ratchet）；新增代码不得抬高基线。
- **pytest + 覆盖率**：`pytest` 全过；核心模块（`deps, iam_service, release_gate_service, credential_service`）覆盖率 `>= 65%`。
- **alembic**：空 MySQL `alembic upgrade head` 成功 + `alembic check` 无漂移。
- **import 冒烟**：`app`（FastAPI）与 `celery_app`（worker/beat）可导入启动。
- **前端**：`eslint` 0 error + `vitest` 通过 + `tsc/build` 通过（涉及前端的 task）。

新增 per-task 门禁：
- **L2T-01/02**：新增 release-gate 测试断言 `ai_assist` 透传至 `clinic_payload`（L2T-01）；若 L2T-02 落地，加断言 `technical_checks.clinic_ai_assist`，否则断言 `gate_result.clinic.ai_assist` 路径可读。
- **P3-02/03/04**：`test_execution_receipt.py` 覆盖"敏感强制拒绝 / 普通放行 / SENSITIVE 可见性 / evidence_ids round-trip"。
- **P3-05/06**：`test_handover_flow.py` + `test_handover_package.py` 覆盖非终态 verify `409` 与 `APPROVED` package `409`（并记录 APPROVED 预览不再可用）。
- **P3-12**：`test_execution_receipt.py` 断言同 `idempotency_key` 重放返回既有终态 action（非 `409`）。
- **P3-13**：RBAC 套件断言无 namespace binding 的 enterprise-admin `skill.publish` → `403`，有绑定 → 通过。
- **P3-14**：分享/审批测试断言已 APPROVED release 关闭再开公共分享时需重新审批。
- **P3-11**：`npx playwright test` 通过，CI 前端 job 执行（先 non-blocking）。
- **L4-01/05/07**：`docker compose -f docker-compose.prod.yml config` 含 analysis-worker / 资源上限 / 健康检查；`test_prod_ops_config.py` 断言 `/readyz` 与 compose 配置。
- **L4-03**：`test_retention_schedule.py` 断言审计/过期发布被清理并记数。
- **L4-04**：缺 age key 时 `scripts/prod.sh` 硬失败；有 key 时解密成功。
- **L4-08**：scan 测试断言 deep-scan 超时取自 `SCAN_LLM_TIMEOUT_SECONDS` settings。
- **L4-09**：sandbox readiness 端点缺项时列出缺口、齐备时 ready。
- **L4-10**：`ClinicEvaluation.trace_id` round-trip 落库；`alembic check` 无漂移。

---

关键文件锚点（仓库相对路径）：`backend/app/services/release_gate_service.py`（`_clinic_gate_payload` 起于约 149 / `clinic_payload` 返回约 191；`_build_gate_result` 起于约 194 / `technical_checks` 约 218 / `clinic` 全量 payload 约 209）、`backend/app/api/v1/endpoints/control_plane.py`、`backend/app/api/v1/endpoints/skills.py`（`update_version_sharing` 约 1084-1095）、`backend/app/models/control_plane.py`（`Criticality`:156 / `Sensitivity`:180 / `AIAsset.criticality`:543 / `WorkTrace.sensitivity`:707 / `ExecutionAction`:790-810）、`backend/app/models/clinic.py`（`ClinicEvaluation.trace_id`:50）、`backend/app/schemas/control_plane.py`、`backend/app/services/`（`SandboxValidationService`、`iam_service`）、`backend/app/core/config.py`、`frontend/src/api/client.ts`、`frontend/src/pages/{HandoverDetailPage.tsx(新建),ControlPlanePage.tsx,ClinicReportPage.tsx,SkillDetailPage.tsx}`、`backend/alembic/versions/`（head `0023`；本轮新增 `0021`/`0022`/`0023`）、`docker-compose.prod.yml`、`backend/Dockerfile`、`scripts/prod.sh`、`scripts/restore.sh(新建)`、`backend/app/workers/lifecycle_tasks.py`、`docs/production-deployment.md(新建)`。

---

# 复审修复轮 (REVIEW-FIX) — 2026-06-25

> 来源：实现交付后的多维度独立复审与门禁复跑。门禁全绿（ruff / mypy 76 / pytest 528 passed/4 skipped / 空 MySQL `upgrade head`+`check` 零漂移，head `0023`），无 critical/high。下列为判 "done" 前的修复项。**执行顺序**：FIX-01 → FIX-02 → FIX-03（同改 `control_plane.py` + `HandoverDetailPage.tsx`，串行）；FIX-04 / FIX-05 / FIX-07 独立可并行；FIX-06 在 FIX-01..04 之后。

## 7. 必修（合并判 done 前）

- **[FIX-01] 执行证据上传端点（解证据死锁）** — *must*
  - 目标: 让敏感/高危 `ExecutionAction` 能附**真实执行证据**（带 `object_uri` 的截图/日志/导出文件），消除"无顾问证据 → 永久 422"死锁，并停止用顾问"建议证据"给执行回执假背书。根因：`_case_evidence_ids`（`backend/app/api/v1/endpoints/control_plane.py:285-328`）只接受 `HandoverItem.evidence_id`（顾问）/ 前序 action 的 `evidence_ids` / `VERIFYING+` 才存在的封装包对象；而 `complete_execution_action` 要求 `case.status==EXECUTING`、回执 USER_CONFIRM 证据又在校验之后建——没有任何上传执行证据的入口。
  - 改动: 后端新增 `POST /handovers/{case_id}/evidence`（`control_plane.py`）——经 `artifact_service` 把上传内容写到 **case 前缀** `handovers/case-{case_id}/evidence/...`（复用 `handover_package_service` 的 put_object 方式），建 `EvidenceItem(source_type=USER_CONFIRM, object_uri=<该对象>, created_by, visibility)`，返回 `{id, object_uri}`；RBAC 与回执写权限一致。**最小改动让其落入允许集**：因 `_case_evidence_ids` 已按 `s3://{bucket}/handovers/case-{id}/` 前缀匹配封装包，执行证据写到同一 case 前缀即自然被接受——**优先此法，避免加库列**；若确需 case↔evidence 显式关联列，则加幂等迁移 `0024`（inspector 守卫 + downgrade）。前端：`HandoverDetailPage` 回执表单加"上传执行证据"控件，拿到 `id` 后并入 `evidence_ids` 提交。
  - 验收: 敏感条目无顾问证据时 → 先上传执行证据→拿 id→回执 `SUCCEEDED` 通过（非 422），上传对象在 case 前缀下且可下载；`backend/tests/test_execution_receipt.py` 增"上传执行证据→敏感回执通过"一例 + 上传端点鉴权/归属校验各一例；门禁全绿（含空 MySQL `upgrade head`+`check`，若加迁移）。
  - 依赖: none
- **[FIX-02] 前端证据必选门对齐 sensitivity** — *must*
  - 目标: 前端 `requiresEvidence` 与后端 `_receipt_requires_evidence`（`control_plane.py:277-282`，criticality ∪ sensitivity，`WorkTrace.sensitivity` 默认 `INTERNAL` 即触发）同口径，消除"主流路径前端不拦、提交后才 422"。
  - 改动: 后端在 case 详情 payload（`getHandover` 对应的 `HandoverDetailOut`/schema）为每个 item/action 暴露聚合 `requires_evidence: bool`（复用 `_receipt_requires_evidence(_execution_action_context(...))`），免去前端拉 `WorkTrace`。前端 `frontend/src/pages/HandoverDetailPage.tsx:290` 改读该后端标志，不再只看 `criticality`。
  - 验收: 默认 INTERNAL 痕迹的普通资产，前端显示"证据必选"并在零证据时拦截（不再提交才 422）；后端测试断言 payload 含 `requires_evidence`；前端 vitest 一例。
  - 依赖: FIX-01（同改 `control_plane.py` + `HandoverDetailPage.tsx`）
- **[FIX-03] 证据下载按钮按 object_uri 条件渲染** — *must*
  - 目标: 无 `object_uri` 的证据不显示下载，消除主流证据（LLM_ANALYSIS / USER_CONFIRM）下载必 404。根因：`HandoverDetailPage.tsx:494` `EvidenceRow` 无条件渲染下载，`issue_evidence_download_link` 对 `not object_uri` 返回 404（`control_plane.py:1671-1672`）。
  - 改动: 前端仅 `evidence.object_uri` 存在时渲染下载，否则灰显/隐藏并标注"无可下载对象"；`frontend/src/api/client.ts` 的 `EvidenceItem` 暴露 `object_uri?: string | null`。
  - 验收: 无对象证据不出现下载；有对象证据（FIX-01 上传 / 封装包）出现且能下载；vitest 一例。
  - 依赖: FIX-01
- **[FIX-04] L4-06 优雅停机真正生效** — *must*
  - 目标: drain 不被 SIGKILL 截断、软停机特性确实启用。根因：`docker-compose.prod.yml` 无 `stop_grace_period`（Docker 默认 10s SIGKILL < `worker_soft_shutdown_timeout=60`），且 `backend/requirements.txt` `celery>=5.4.0` 过松（软停机为 5.5 引入，5.4 静默忽略 → `test_celery_reliability` 假绿）。
  - 改动: `docker-compose.prod.yml` 给 backend/worker/beat 加 `stop_grace_period: 90s`；`backend/requirements.txt` 改 `celery>=5.5.0`（或加 constraints 锁定）；`scripts/prod.sh` 的 `docker compose down/stop` 加 `-t 90`；（可选）uvicorn 设 `--timeout-graceful-shutdown`。
  - 验收: `docker compose -f docker-compose.prod.yml config` 显示三服务 `stop_grace_period`；`backend/tests/test_prod_ops_config.py` / `test_celery_reliability.py` 断言 `stop_grace_period` 与 celery 下限；`docker compose stop` 期间无 SIGKILL（手测并记入 runbook）。
  - 依赖: none

## 8. 应补（计划要求的验收测试 + 低危）

- **[FIX-05] P3-13 正向对照测试** — *should*
  - 目标: 证明 `skill.publish` 403 源于"缺 namespace 绑定"而非角色本身。
  - 改动: `backend/tests/test_skill_publish_authz.py` 增一例：给主体绑 **namespace 域写角色**（如 namespace-developer；注意 SYSTEM 角色拒绝 `namespace_id`，见 `test_iam_binding_scope.py:146`）→ `publish_version` 通过；与既有否定例（无绑定→403）并存。
  - 验收: 否定例 + 正向例并存且全绿。
  - 依赖: none
- **[FIX-06] 验收级测试补齐（P3-10 / L4-05 / L4-09）+ 两处低危** — *should*
  - 目标: 把"行为靠肉眼"的验收落成测试，并修两处低危。
  - 改动:
    - P3-10: 前端测试覆盖 `X-AI-Assist` header 解析 + 在 `ControlPlanePage`/`HandoverDetailPage` 渲染徽章。
    - L4-05: `test_prod_ops_config.py` 增 `/readyz` 依赖断开→503 / 全通→200 行为测试（mock 依赖探测）。
    - L4-09: 增 `GET /scan/sandbox/readiness` 端点测试——`AdminUser` RBAC（非 admin→403）+ ready=true 正向分支。
    - 低危A（幂等回放）：`control_plane.py:2193-2196` 终态 action 同 `idempotency_key` 回放时，断言 `body.result`/`evidence_ids` 与存储回执载荷哈希一致，不一致→409（避免静默 no-op 吞掉异语义回执）；加测试。
    - 低危B（失败回执前端死锁）：`HandoverDetailPage.tsx:341` 回执提交 `disabled` 条件纳入 `result==='succeeded'` 判定，使高危 action 可标 `failed`。
  - 验收: 上述各有断言；门禁全绿。
  - 依赖: FIX-01..04（同文件区，串行合入）
- **[FIX-07] L4-11 备份→恢复演练记录** — *should*
  - 目标: 满足 L4-11 验收"至少一次备份→恢复演练记录"。
  - 改动: 实跑一次 `scripts/prod.sh` 备份 + `scripts/restore.sh` 恢复（MySQL + repos + MinIO 三件套），把时间/步骤/校验结果写进 `docs/production-deployment.md`（替换模板说明为真实演练条目）。
  - 验收: 文档含真实演练记录（非模板占位）。
  - 依赖: none

## 9. 延期 / 范围外（本轮不做，仅登记）

- **P3-11 Playwright 闭环 E2E** — ✅ done：harness、auth smoke、后端 seed 夹具与 seeded `approve→execute→receipt→verify→completed` 均已落地；CI 启动完整开发栈并将该路径作为阻塞门禁运行。
- **`alembic downgrade base` 预存 bug** — 全链 `downgrade base` 在 MySQL 复现失败（error 1091 drop 约束）。⚠ **先前把真因记为 `0006:224` 的 `public_skill_releases_ibfk_5` 系误判**：该约束名在 `0006` 全历史从未出现（0006 用对称命名 FK `fk_public_skill_releases_*`，create 150-165 / drop 224-226），真因待重新诊断（疑在别处，如 0009 活模型建表的基线）。**不影响 `upgrade head`/生产路径**，范围外，按需单独修。

## 门禁（同 §6，每个 FIX 任务合入前全绿）

ruff / mypy ≤95 / pytest + 核心覆盖率 ≥65 / 空 MySQL `alembic upgrade head`+`alembic check` 无漂移 / `app`+`celery_app` import 冒烟 / 前端 eslint + vitest + build。一任务一提交，首行引用 FIX-id，结尾 `Co-Authored-By:`。

---

# 技术债收口 (TD) — 2026-06-25（✅ independently reviewed）

> 来源：REVIEW-FIX 轮复审确认的 2 个 low 残留（幂等回执相关）。修复完成后经独立复审，未发现正确性、安全或边界问题；ruff、mypy、`test_execution_receipt.py`、全量 pytest 与核心覆盖率门禁均通过。

- **[TD-01] 幂等回执 hash 对 evidence_ids 顺序不敏感** — *low* — ✅ done（independently reviewed）
  - 原问题: `_receipt_payload_hash`（`backend/app/api/v1/endpoints/control_plane.py:256`）`sort_keys=True` 仅排序 dict 键，`evidence_ids` 列表按输入序序列化；同一证据集合不同顺序的**合法**重放会误判 409（FE 当前发确定顺序故未触发）。
  - 改动: hash 改用 `sorted(set(evidence_ids))`（写入端 `:2369` 与比对端 `:2331` 同函数，自动一致）。
  - 验收: `test_execution_receipt.py::test_receipt_payload_hash_ignores_evidence_id_order` —— `[1,2,3]` 与 `[3,1,2,1]` 同 hash、`[1,2]` 异 hash。✅
- **[TD-02] 遗留回执绕过 409 守卫** — *low* — ✅ done（independently reviewed）
  - 原问题: `control_plane.py:2331` 守卫 `if expected_hash and expected_hash != ...` 在 `receipt_payload_hash` 为 None（`15a0e0b`→`f11bbd1` 窗口内、加 hash 前写入的在途行）时短路 → 同 key 异 payload 重放仍静默返回旧 action。
  - 改动: 去掉 `expected_hash and`，缺 hash 即视为不可证同 → 保守 409（消息保持 "different receipt payload" 不变，不破坏既有断言）。
  - 验收: `test_execution_receipt.py::test_receipt_idempotency_legacy_row_without_hash_is_rejected` —— 终态 action 仅有 `receipt_idempotency_key`、无 hash 时，同 key 重放 → 409。✅

> 仍开放（与 TD 无关，按现状跟踪）：FIX-07 / L4-11 生产 backup→restore 演练（需部署主机带 SOPS age key）。P3-11 seeded 闭环已纳入阻塞 CI。

---

# 收敛复审记录 (CONV) — 2026-06-25（✅ independently reviewed）

> 文档/spec 收敛修正经独立复审：迁移数量/head、测试文件/函数规模、前端 Vitest 文件数和 L4-10 `ClinicEvaluation.trace_id` 主锚点均与当前仓库一致。复审期间修正了 `ClinicEvaluation` 的模型锚点；P3-11 seeded Playwright 已闭合，仍开放项为生产 backup→restore 演练。

---

# P3-11 E2E 复审记录 — 2026-06-25 / 2026-07-09（✅ seeded run closed）

> Playwright harness + 首次部署 checklist 复审发现 1 个真实门禁问题并修复。`npm test` 会把 `frontend/e2e/*.spec.ts` 当 Vitest suite 收进去；由于 `@playwright/test` 按设计不进 `package-lock.json`，常规 frontend CI 会在 `vite:import-analysis` 阶段失败。修复：`frontend/vitest.config.ts` 显式 `exclude: ["**/node_modules/**", "**/dist/**", "e2e/**"]`。

- **已验证**：`npm run lint` 0 errors（仍有 12 个既有 warning）；`npm test` 5 files / 19 tests passed；`npm run build` passed。
- **2026-07-09 收尾**：新增 `backend/scripts/seed_handover_e2e.py` 与 `backend/tests/test_handover_e2e_seed.py`；seed 默认创建 `pending_approval` 低风险交接单，Playwright 依次完成 approve、执行、回执和验收。
- **状态**：P3-11 seeded browser path 已完成，CI E2E job 启动完整 backend stack 并以 blocking 模式运行。
