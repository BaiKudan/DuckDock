# DuckDock 改进计划（基于 spec-kit / SDD）

**日期**: 2026-06-08 · **依据**: [.specify/memory/constitution.md](../.specify/memory/constitution.md) + [specs/001-agent-control-plane](../specs/001-agent-control-plane/spec.md)

**总原则**: 先补**宪法红线**（测试、门禁、隔离），再用 spec-kit 把"正在做"和"要做"的功能
收口成可追溯的 `specify → clarify → plan → tasks → analyze → implement` 循环。每个 Tier 的产出都挂回 constitution 的某条原则。

## 现状快照（实测，非推测，2026-06-08）

| 维度 | 现状 |
|---|---|
| 规模 | 23 services · 17 endpoints · 14 migrations |
| 后端测试 | 3 文件 / 23 service（违反原则 III） |
| 前端测试 | 0（`package.json` 无 test 脚本、无 vitest/playwright；但 `build` 含 `tsc -b`） |
| 质量门禁 | ❗ 无 ruff/mypy/eslint 配置；CI 仅 compile + pytest + migrate + build |
| 多租户隔离 | ❗ models 中 **0 处 `tenant_id`**；当前仅靠 Namespace/RBAC |
| 交接 E08 | 部分落地（handover 模型 + `0012` 迁移 + control_plane/people 端点，225 处引用），无独立 service，闭环成熟度待盘点 |

## Tier 0 — 宪法红线（本周）

- **0.1 测试地基**（原则 III · tasks.md T070）：后端 `tests/{unit,integration,contract}` + `conftest`；首批覆盖发布门禁串行、状态机、Adapter mock、**越权**；前端 Vitest + Testing Library + Playwright；核心 service 覆盖率底线。→ `specs/002-test-harness/`
- **0.2 质量门禁**：后端 `ruff`（阻塞）+ `mypy`（warning 起步）；前端 `eslint` + `tsc --noEmit` 进 CI。
- **0.3 一致性收口**：`/speckit-analyze` 校验 specs/001；修 docker-compose 端口表注释（T006）；扫 `docs/` 残留 Postgres 旧叙事。

## Tier 1 — 安全与隔离（本迭代 · PRD 列为严重风险）

- **1.1 多租户/命名空间隔离**（FR-014）❗：定隔离键（Namespace vs tenant）并强制所有跨实体查询带上 + 越权测试。→ `specs/003-tenant-isolation/`，先 `/speckit-clarify`
- **1.2 凭证安全 + 敏感内容门禁**（FR-003/008/016 · 原则 V）：凭证加密、不明文返回、签名 URL 过期、reveal 需 reason+审批+审计、审计不可删；跑 `/security-review`。

## Tier 2 — 把"正在做的"用 SDD 收口（本迭代）

- **2.1 交接闭环 E08 盘点 + 补齐**（US-003~006）：核对状态机/审批闸门/幂等执行/LLM 建议/证据链，缺口走 tasks T040–T045 + `/speckit-implement`。
- **2.2 解决 `[NEEDS CLARIFICATION]`**：`/speckit-clarify` 消解 FR-016/017/018（凭证库 / ArkClaw / WorkBuddy）。

## Tier 3 — 可维护性 & 流程制度化（持续）

- service 按域分组（registry / control-plane / platform）+ service map。
- 结构化日志 + correlation id + 幂等键延伸到采集/执行路径（原则 II/IV）。
- 新功能先 `specs/`；对已上线支柱补 `specs/000-*` 回溯 spec；发布用 `/speckit-checklist`。

## Tier 4 — 产品演进（clarify 后排期）

- 报表与资产图谱（E12）、多租户 SaaS 计费。
- ~~ArkClaw（E10）/ WorkBuddy（E11）Adapter~~ —— **已随 2026-06-12 Push-only 决策退役**（服务端不外联;未来接入做对应 Reporter 技能,见 specs/001 Clarifications + Phase 8）。

## 节奏

| 时间 | 重点 |
|---|---|
| 第 1 周 | Tier 0 全部 + 1.1 隔离决策 |
| 第 2–3 周 | Tier 1 完成 + 2.1/2.2 |
| 之后 | Tier 3 持续 · Tier 4 按 clarify 排期 |

## 执行进度

- [x] 计划存档（本文件）
- [x] Tier 0.2 CI 门禁：`backend/pyproject.toml`（ruff select=F 阻塞 / mypy 非阻塞）+ CI step；清理 19 处 dead import
- [x] specs/002-test-harness 骨架
- [x] specs/003-tenant-isolation 骨架
- [x] Tier 0.1 测试地基：`tests/conftest.py`（`async_session` 夹具）+ 冒烟测试（共 13 passed）
- [x] 补核心 service 测试：越权 `tests/test_authz.py`（6）+ RBAC `tests/test_rbac.py`（3）+ 发布门禁 `tests/test_release_gate.py`（6 · production/review/rejected/sandbox/scan/人工复核）→ 共 **28 passed**
- [x] Tier 0.3（部分）：修 `docker-compose.yml` 端口表注释 + `port-plan` + `architecture.md` 的 Postgres→MySQL 漂移
- [ ] Tier 0.3（剩余）：`operations-runbook` / `ci.zh-CN` / `dev-operations` 残留 + 跑 `/speckit-analyze`
- [ ] 续补测试：发布串行锁（需 MySQL `GET_LOCK`）、状态机、Adapter mock、前端（vitest/playwright）
- [x] `/speckit-analyze` 已跑(2026-06-12):001 账实对账 + FR-010 状态机修订
- [x] `/speckit-clarify` 已决(2026-06-12):隔离=激活权限键(方案 A)、凭证=Fernet 加密表
- [x] Tier 1.1 后端落地(2026-06-12):8 权限门 + 38 端点接线 + decide 审批人校验 + 11 个新测试 → **39 passed**
- [x] T011 Fernet 凭证存储落地(2026-06-12):`credential_records` 表 + 服务 + 端点接线 + 8 测试 → **47 passed**
- [x] T032 reveal 落地(2026-06-12):敏感 trace 默认遮蔽 + reveal(reason+权限+审计)+ 5 测试 → **52 passed**
- [x] JVS Adapter 降为 P1(2026-06-12 用户决策,已编码进 specs/001 Clarifications;发布标准由 OpenClaw 满足)
- [x] 前端 403 适配(2026-06-12):axios 拦截器全局友好提示(带节流),`npm run build` 通过
- [x] `/speckit-analyze` 复检 #2(2026-06-12):抓到 0015 迁移引发的 README/CLAUDE.md 漂移并修复;plan 计数刷新;T022 补勾
- [x] T053 Phase 1(2026-06-12):Vitest+RTL 地基(vitest.config + setup + auth-store 4 例)+ CI 前端测试 step;Playwright/eslint 留 Phase 2
- [x] **采集路径决策**(2026-06-12):仅 Push(Reporter + Analysis Worker),Pull Adapter 链整体退役 → specs/001 Clarifications + Phase 8(T080–T083)
- [x] FR-019 已决(2026-06-12):执行**双模式可选**——manual(回执+自我二次审查)默认 / auto(探针 lease)→ T084/T085
- [x] T084 manual 回执 MVP 落地(2026-06-12):execution_mode + 回执端点 + USER_CONFIRM 证据 + case→VERIFYING + 5 测试 → **57 passed**
- [x] T086 交接验收端点(2026-06-12):`POST /handovers/{case}/verify` VERIFYING→COMPLETED + 证据 + 审计 + 3 测试 → **60 passed**(补回 T084 的闭环洞)
- [x] Pull 链退役 T080–T082(2026-06-12):test_runtime→上报自检 · 删 sync/run 端点+celery 任务+前端 2 按钮 · 保留 Push 归一化层 → **60 passed + 前端 build/4 测试**
- [x] T072 管理员手册 + 宪法 v1.1.0(2026-06-12):`docs/control-plane-admin-guide.zh-CN.md`;宪法刷新原则 II(Push-only)/III(测试 3→13 文件)
- [x] 质量门禁 ratchet(2026-06-12):mypy baseline=95(只降不升,pin mypy 2.1.0)+ 核心安全模块覆盖率 `--cov-fail-under=65`(当前 69%)进 CI;宪法 Quality Gates 与 specs/002 FR-006 同步
- [ ] 跟进:T081b 死适配器文件物理删除 · T085 探针 lease · 前端 Playwright/eslint · T036 上报链路补测 · 产物下载 URL · T042 LLM 建议 · 全局覆盖率抬升
- [ ] 待办：mypy 当前 94 errors/29 files（非阻塞），逐步清零并收紧；ruff 逐步加 E/I/B/UP
