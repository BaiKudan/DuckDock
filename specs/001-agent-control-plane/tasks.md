---
description: "Task list for DuckDock Agent Control Plane (migrated from 08-implementation-backlog)"
---

# Tasks: DuckDock 企业 AI Agent 资产与交接控制平面

**Input**: `specs/001-agent-control-plane/` (spec.md, plan.md)

**Format**: `[ID] [P?] [Story] Description` · **[P]** = 可并行(不同文件/无依赖) · **[Story]** = 对应用户故事

> **状态说明**:`[x]` 已落地(2026-06-12 经 `/speckit-analyze` + 代码证据对账),`[ ]` 待办,🟡 部分完成。
> 对账结论:交接闭环 API(collect/analyze/submit/approvals/execute + 审批闸门 409)已实现于
> `control_plane.py`,但无独立 `handover_service`(见 plan.md Complexity Tracking);JVS Adapter 为最大 P0 缺口。

## Epic 依赖图 (来自 08-backlog)

```text
E00(MySQL底座) → E01, E02, E05
E01,E02 → E03 ;  E02 → E04
E02,E03,E04,E05 → E06(OpenClaw), E07(JVS)
E03,E04,E05 → E08(交接) ;  E02–E08 → E09(前端)
[P1] E10(ArkClaw 待厂商确认), E11(WorkBuddy), E12(报表/图谱)
```

> ⚠️ **2026-06-12 Push-only 决策**:采集仅走 Reporter 上报。E06 外联部分 / E07 / E10 / E11 撤销,
> 采集主链 = 报告上传 + Analysis Worker;Pull 链退役实施见 Phase 8(T080–T082)。

---

## Phase 1: 技术底座 (E00) — Setup

- [x] T001 PostgreSQL → MySQL 8.x 迁移,`DATABASE_URL` 改 `mysql+asyncmy://…@mysql:3306`
- [x] T002 引入 `asyncmy`,替换 PG 专属 SQL(jsonb→json、advisory-lock→GET_LOCK)
- [x] T003 重生成 Alembic baseline,空 MySQL 可 `upgrade head`(迁移 `0001`–`0014`)
- [x] T004 更新 `docker-compose.yml`(mysql 服务 3307:3306)与 `.env.example`
- [x] T005 [P] 配置 `ruff` + `mypy`(后端)并接入 CI(`fc2c596`);前端 `tsc` 已在 build 内,eslint 归入 T053
- [x] T006 修正 `docker-compose.yml` 顶部端口表注释(`6cf499e`)

**Checkpoint**: `docker compose up` 起 mysql/redis/minio/backend/worker/frontend;基础登录/IAM/Namespace/Skill 不回归。

---

## Phase 2: Foundational (E01 身份租户 · E02 Runtime · E05 证据审计) — 阻塞所有故事

- [x] T010 [US1] Runtime model/schema/service/api(provider: openclaw/jvs/arkclaw/workbuddy/custom),迁移 `0010_adapter_runtime_foundation`
- [x] T011 [US1] 凭证加密存储已落地(2026-06-12):`credential_records` 表(迁移 `0015`)+ `credential_service`(Fernet,`db:`/`env:` 引用,就地轮换)+ create/update runtime 接线(审计剔除明文)+ 8 例测试;Adapter 侧用 `resolve_credential()` 取密
- [x] T012 [P] 审计增强:对象类型/request_id/reason/before-after(`audit_service.py`)
- [x] T013 [P] `evidence_item` 模型与证据签名 URL(FR-009)
  🟢 2026-06-16:模型早已落地(迁移 0009);本次补**证据签名下载**——`POST /evidence/{id}/download-link`
  签发限时预签名 URL(原则 V:权限 + 原因 + 审计 + 过期)。敏感级(SENSITIVE/RESTRICTED)在
  `evidence.read` 之上叠加 `evidence.sensitive.read`(系统 admin 例外),**授予与拒绝皆审计**;
  报告包内部证据签名所在归档对象并回传 archive 内路径(片段不可直接签名);文案不外泄桶名/存储结构。
  新 `evidence_service`(解析 + 签名,boto 异常收敛为 502)+ 18 例测试(含 3 agent 对抗式 review 修正)。
- [x] T014 权限收口(2026-06-12 同日实现):8 个权限门(`deps.py`)+ 38 个端点接线(读→`*.read`、写→`*.manage`,自我范围端点保持 CurrentUser)+ `decide_approval` 改为"指派审批人本人或 handover.manage 可决";敏感内容 ownership/org 过滤随 T032 reveal 落地(FR-014,specs/003)

**Checkpoint**: Runtime + 审计 + 证据底座就绪,故事可并行启动。

---

## Phase 3: US-001 创建运行时连接 (P1) 🎯 MVP

- [x] T020 [US1] 测试连接,返回 provider 版本 + 能力声明(FR-002)— `POST /runtimes/{id}/test` + `/adapters/capabilities` 已上
- [x] T021 [US1] 运行时新增/编辑/禁用 API + 前端(FR-001)— `/runtimes` GET/POST/PATCH + ControlPlanePage
- [x] T022 [P] [US1] Runtime 单测 + 凭证不外泄断言——由 `tests/test_credentials.py` 覆盖(create/update + 永不回明文,2026-06-12 analyze 复检确认)

**Checkpoint**: 单独配一个连接并测试,即可验证价值。

---

## Phase 4: US-002 同步资产 + 资产目录 (P1) — E03/E04/E06/E07

- [x] T030 [US2] `collection_job` 状态机 `pending/running/succeeded/failed/partial-failed`(FR-004/005)— `adapter_collection_service.py`
- [x] T031 [US2] `ai_asset` upsert + 归属推断 + `runtime_binding`(`/assets` CRUD+ownership+feedback 已上);Skill→AIAsset 映射待核验
- [x] T032 [P] [US2] worktrace 默认摘要 + reveal 已落地(2026-06-12):敏感级(confidential/restricted)默认遮蔽 `metadata_json`;`POST /worktraces/{id}/reveal` 要求 reason≥5 字 + 本人或 `worktrace.content.read` + 审计留痕;产物清单不含存储路径;5 例测试。产物**下载**签名 URL 留作后续(FR-012 范畴)
- [~] T033 [P] [US2] OpenClaw Adapter 外联采集(collect_*)→ **2026-06-12 撤销**(Push-only);test_connection 语义重定义归 T080;backup **上传式**导入(import_openclaw_backup)保留
- [~] T034 [P] [US2] JVS Adapter → **2026-06-12 撤销**(同日先降 P1、后随 Push-only 决策整体退役;FR-015 已撤销)
- [x] T035 [US2] 交接/备份包上传 MinIO + manifest + sha256(FR-012)— report_pack/upload service + finalize/ingest 端点已上
- [x] T036 [P] [US2] **测试先行**(2026-06-12 改向 Push 链):上报会话状态机 + 归一化落库(persist_collection_result)补测;analysis 管线已有 11 例打底 ⬅️ 宪法原则 III
  🟢 2026-06-16:归一化(`normalize_duckdock_report_pack`)+ 落库(`persist_collection_result`)+ **幂等**(原则 II)+ 非法 schema 拒绝 4 例(`tests/test_report_collection_flow.py`)。
  ✅ 上报会话 **FSM** 补齐:`tests/test_report_upload_fsm.py` 12 例——create(PENDING+签 URL/幂等复用/终态/超限 413)、
  finalize(PENDING→UPLOADED、size/sha 不匹配 422、跨 runtime 403、终态幂等、存储异常→FAILED)、
  ingest(UPLOADED→SUCCEEDED、坏包→FAILED+AdapterError 并抛出),全程 mock MinIO。

**Checkpoint**: 资产盘点可独立交付(SC-001 同步成功率 > 95%)。

---

## Phase 5: US-003~006 离职交接闭环 (P2/P3) — E08

- [x] T040 [US3] `handover_case`/`handover_item`/`approval_task`/`execution_action` 模型 + 状态机(FR-010,10 态枚举,比原 spec 更全——spec 已按实现修订)
- [x] T041 [US3] 创建交接单(员工/接收人/范围)→ `POST /handovers` + `/collect` + PeopleHandoverPage 已上
- [x] T042 [US4] LLM 交接建议服务
  🟢 2026-06-16:可插拔顾问 `handover_advisor_service`——`RuleBasedHandoverAdvisor`(默认、确定性兜底)
  / `LLMHandoverAdvisor`(OpenAI 兼容,批量产出每资产 {推荐动作, 置信度, 理由},异常即降级)。
  `HandoverItem.confidence` 列(迁移 0017 幂等,fresh+legacy MySQL 已验)+ HandoverItemOut 暴露给审批人;
  `HANDOVER_LLM_ENABLED` **默认关闭**(默认栈仍规则版,无意外 LLM 开销)。原则 V 守住:产出仅 PROPOSED 建议,
  审批闸门未绕过(测试断言 DISABLE 高置信仍停在 PENDING_APPROVAL);LLM 输出全程校验(动作白名单/置信度 clamp/
  幻觉 id 丢弃/float·string id 兼容)。12 例测试 + 3 视角对抗式 review(修复 float id 丢弃 critical bug)。
  遗留:证据评分用 evidence_id FK + risk_reason 承载(未单列 evidence_score);LLM client base_url SSRF 加固宜统一到所有 LLM client(follow-up)。
- [x] T043 [US5] 审批闸门:非 APPROVED 调 execute → 409(已代码验证,FR-011);断言测试归 T045
- [x] T044 [US6] 执行动作框架(`/execute` + ExecutionAction + PARTIAL_FAILED 在);幂等/重试**断言**归 T045 测试
- [x] T045 [P] [US3-6] 交接状态机 + 审批闸门测试已落地:`test_handover_flow.py` 6 例(DRAFT 起步/全票通过/拒绝阻断/越权审批 403/非法决定 422/analyze 生成项)+ `test_control_plane_authz.py` 5 例(权限门 + 接线锁)

**Checkpoint**: 离职交接完整闭环(SC-002/005)。

---

## Phase 6: 前端控制台 (E09)

- [x] T050 引入 AntD + 权限路由 + API client + QueryClient;通用表格/状态标签/证据抽屉
  🟢 2026-06-16 对账确认已落地:`frontend/package.json` antd^5 + pro-components;`main.tsx` QueryClientProvider;`authRoutes.ts` 权限路由;18 页用 AntD ProTable/Modal/Tag。
- [x] T051 页面:首页/运行时/资产/工作历程/交接/风险/审计;无权限按钮隐藏或禁用
  🟢 2026-06-16 对账确认已落地:`frontend/src/pages/` 18 页(Dashboard/ControlPlane/PeopleHandover/Audit/AnalysisControl…),按 `useAuthStore` permissionKeys 条件渲染。
- [ ] T052 [P] 异步任务轮询 + 错误展示;`npm run build` 通过 🟡 页面有手动 refetch,缺定时轮询/全局错误展示
- [~] T053 [P] **E2E**:创建交接单流程 + 审批流程 + 权限隐藏(前端 0 测试 → 补齐)⬅️ 宪法原则 III
  🟢 2026-06-16:前端 **eslint 门**(flat config,卡 unused/undefined/Hooks 误用,0 error)+ CI eslint 步骤已上;
  权限门控纯函数测试 `authRoutes.test.ts`(8 例:管理可见性/命名空间工具/默认落地路由)。前端测试 4→12。
  剩:Playwright E2E(创建交接/审批端到端,需浏览器环境,留独立任务)。

---

## Phase 7: P1 增强 — E10/E11/E12(可延后)

- [~] T060 [P] ArkClaw Adapter → **2026-06-12 撤销**(Push-only;未来接入做 ArkClaw 版 Reporter 技能)
- [~] T061 [P] WorkBuddy Adapter → **2026-06-12 撤销**(同上)
- [ ] T062 [P] 报表中心 + 资产血缘图谱(复用率/无人维护/离职风险/平台分布)

---

## Phase 8: Pull 链退役(2026-06-12 Push-only 决策)

- [x] T080 `test_runtime` 重定义为**上报链路自检**(2026-06-12):report token 有效性 + 最近上报状态,不再外联 adapter
- [x] T081 下线 Pull 触发(2026-06-12):删 `sync_runtime` / `run_collection_job` 端点 + `_enqueue_collection_job` + `run_collection_job_now`(adapter.collect 外联)+ celery `adapter_tasks` 任务/注册 + 前端「同步」「运行」按钮 + client 的 `syncRuntime`/`runCollectionJob`;**保留** `persist_collection_result`(Push 共用)与 backup 上传导入。遗留:base/generic/registry + OpenClawAdapter 已不可达的死文件,待拆出 openclaw 备份解析后物理删除(T081b,低优先)
- [x] T082 [P] 文档清理(2026-06-12):README / architecture.md / CLAUDE.md **无** Pull 采集叙事残留(已核;Pull 叙事仅存于历史 PRD 源文档,不改)
- [x] T083 FR-019 已决(2026-06-12):**双模式可选,`manual` 默认**——人工回执 = 自我二次审查/数据最小化;`auto` = 探针 lease(高风险动作可策略强制 manual)
- [x] T084 manual 回执 MVP 已落地(2026-06-12):`execution_mode` 字段(迁移 `0016`,默认 manual)+ `POST /handovers/{case}/actions/{id}/complete`(SUCCEEDED|FAILED + note→审计 + **USER_CONFIRM 证据** + item DONE/FAILED + 全终态→case VERIFYING)+ 5 例测试
- [ ] T085 auto·探针 lease 协议(复刻 analysis worker:注册/lease/heartbeat/finalize),**待现场探针支持后实施**(P1)
- [x] T086 [US6] 交接验收/归档(2026-06-12):`POST /handovers/{case}/verify`——接收人本人或 `handover.manage` 在 VERIFYING 态确认 → COMPLETED + USER_CONFIRM 证据 + 审计;非 VERIFYING→409、越权→403;3 例测试(补上 T084 留下的 VERIFYING→COMPLETED 闭环洞)

---

## Polish & 技术债清偿(最高优先)

- [~] T070 **测试回填**:交接状态机/Adapter 补测(authz/RBAC/发布门禁/分析已覆盖);含"每个写端点必审计"断言(analyze AUD 项)⬅️ 宪法红线
  🟢 2026-06-16:**"每个写端点必审计"回归锁已上**(`tests/test_audit_coverage.py`,13 例)——自动发现 91 个 v1 写端点,断言每个 handler 自身审计或在带理由的豁免表里(10 项:delegated/non-mutating/worker-protocol);含负向自检(空豁免表必抓出 10 个)+ 豁免表防腐。交接状态机已由 `test_handover_flow` 覆盖。剩:Adapter mock-server 补测(Push-only 后优先级降)
- [x] T071 同步修正 README 与 `docs/` 的栈/迁移漂移(`6cf499e`);`/speckit-analyze` 已跑(2026-06-12,本文件即对账产物)
- [x] T072 [P] 控制平面管理员手册(2026-06-12):`docs/control-plane-admin-guide.zh-CN.md`——权限收口后绑 `enterprise-admin`、`DUCKDOCK_CREDENTIAL_KEY`、Push 上报接入、交接生命周期、reveal、上线清单

## Dependencies & 并行机会

- Phase 1 → Phase 2 → (Phase 3 ∥ Phase 4 ∥ Phase 5) → Phase 6;Phase 7 可在底座后并行。
- `[P]` 任务跨文件无依赖可并行;Adapter(T033/T034)天然并行;测试任务与其实现绑定。
- **每个故事独立可测**:US-1/US-2 = 资产盘点 MVP;US-3~6 = 交接闭环增量。
