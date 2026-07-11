# Feature Specification: 后端/前端测试地基与质量门禁

**Feature Branch**: `002-test-harness`

**Created**: 2026-06-08

**Status**: Implementing（Tier 0 · 后端测试 + 前端 Vitest + ruff/mypy-ratchet/核心覆盖率 65% 门禁已上；2026-06-16 加 **前端 eslint 门 + CI 步骤**(flat config，0 error)+ 权限门控单测；剩 Playwright E2E）

**Input**: 把"3 测试 / 23 service、前端 0 测试、CI 仅 compile"提升为可信赖的测试地基与静态门禁。

## Clarifications

### Session 2026-06-12

- Q: FR-005 前端测试框架选型? → A: **分阶段**——Phase 1 用 Vitest + Testing Library(轻、与 Vite 同生态、单元/渲染足够),已接入 CI;Playwright E2E 与 eslint 留 Phase 2。
- Q: FR-006 覆盖率门槛取多少? → A(2026-06-12): **核心安全模块(deps/iam_service/release_gate_service/credential_service)≥ 65% ratchet**(当前 69%,`--cov-fail-under=65` 进 CI),全局当前 ~31% 不设硬门、逐步抬升。配套:mypy baseline ratchet(95,只降不升)。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 共享测试夹具 (Priority: P1)

开发者写新测试时，能直接复用统一的 DB 会话夹具与 mock 模式，而不必各自重复建表/造数据。

**Why this priority**: 没有共享地基，测试不会被持续补齐；这是 23 service 补测的前置。

**Independent Test**: 一个新测试注入 `async_session` 夹具即可建表、写入、查询（已由 `tests/test_harness_smoke.py` 验证）。

**Acceptance Scenarios**:
1. **Given** `tests/conftest.py` 提供 `async_session`, **When** 新测试注入它, **Then** 得到内存 SQLite 上已建表的 AsyncSession。

### User Story 2 - 核心 service 测试覆盖 (Priority: P1)

核心写路径（发布门禁串行、状态机、Adapter、越权）有 contract/integration 测试。

**Acceptance Scenarios**:
1. **Given** 发布同一 skill 的并发场景, **When** 运行 publish, **Then** advisory-lock 串行化被测试断言。
2. **Given** 交接/analysis 状态机, **When** 非法转移, **Then** 被拒绝且有测试覆盖。
3. **Given** 越权访问, **When** 低权限用户请求他人资产, **Then** 返回 403/404 且有测试。

### User Story 3 - 前端关键流程测试 (Priority: P2)

前端关键页面有渲染/权限测试，创建交接单与审批有 E2E。

### Edge Cases
- Adapter 依赖外部厂商 → 用 mock server，不打真实 API。
- 异步任务状态 → 测试覆盖 pending/running/succeeded/failed/partial-failed 全转移。

## Requirements *(mandatory)*

### Functional Requirements
- **FR-001**: 后端 MUST 提供 `tests/{unit,integration,contract}` 分层与共享 `conftest` 夹具。
- **FR-002**: CI MUST 运行 `ruff`（阻塞，select=F 起步）与 `mypy`（非阻塞起步）。
- **FR-003**: 核心 service MUST 有单元 + 集成测试；Adapter MUST 用 mock server 测试。
- **FR-004**: 测试 MUST 覆盖越权（authz）矩阵。
- **FR-005**: 前端测试分两阶段(2026-06-12 决策):Phase 1 = **Vitest + Testing Library**(单元/渲染,已入 CI);Phase 2 = Playwright E2E(创建交接单/审批流程)。eslint 随 Phase 2 一并引入。
- **FR-006**: CI MUST 对核心安全模块(deps / iam_service / release_gate_service / credential_service)设 **≥65%** 覆盖率门槛（`--cov-fail-under`，当前 69%），并配 mypy baseline ratchet（错误数只降不升）。全局覆盖率逐步抬升，暂不设硬门。

### Key Entities
- 无新增数据实体（工程性特性）。

## Success Criteria *(mandatory)*

- **SC-001**: 核心 service（发布门禁/状态机/Adapter）单测 + 集成测试齐全。
- **SC-002**: CI 新增 ruff（阻塞）+ mypy（非阻塞）+ 覆盖率报告，全绿。
- **SC-003**: 前端关键流程（创建交接单、审批）有通过的 E2E。
- **SC-004**: 后端核心模块覆盖率达到约定门槛。

## Assumptions
- 单元/集成测试用内存 SQLite；Adapter/外部依赖用 mock，不依赖 live MySQL/MinIO。
- ruff 起步只卡 `F`（pyflakes），逐步加 E/I/B/UP；mypy 由非阻塞逐步收紧（见 `backend/pyproject.toml`）。
- 已完成的起步：`backend/pyproject.toml`（ruff/mypy/pytest 配置）、`conftest.py`、CI 的 ruff/mypy step。
