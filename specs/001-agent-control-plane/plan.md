# Implementation Plan: DuckDock 企业 AI Agent 资产与交接控制平面

**Branch**: `001-agent-control-plane` | **Date**: 2026-06-08 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/001-agent-control-plane/spec.md`

## Summary

把 DuckDock 从私有 Skills 仓库升级为企业 AI Agent 资产/交接控制平面:统一资产目录、
运行时(上报来源)注册、工作历程与证据链、离职/项目交接闭环;采集 = Push 上报
(Reporter + Analysis Worker;2026-06-12 决策,Pull Adapter 链退役,见 spec Clarifications)。
技术上以 **MySQL 单一主库 + FastAPI + Celery + MinIO + React/AntD** 落地,复用既有
发布门禁与审计能力,新增控制平面领域模型(迁移 `0009`–`0015` 已落地)。

## Technical Context

**Language/Version**: Python 3.12(后端 / worker)· TypeScript 5(前端)

**Primary Dependencies**: FastAPI · SQLAlchemy async + `asyncmy` · Alembic · Celery · Redis ·
MinIO(boto3/minio)· React + Ant Design + TanStack Query + Vite · Anthropic/兼容 LLM(交接建议、Clinic)

**Storage**: **MySQL 8.4 为唯一业务主库**(host `3307` → container `3306`)· MinIO(产物/交接包)·
Git bare repo(skill 版本)· PostgreSQL 16 + ClickHouse **仅** `observability` profile(Langfuse)

**Testing**: pytest(在 CI 的真实 MySQL service 上)· 前端 Vitest + RTL(2026-06-12 决策,见 specs/002 Clarifications;Playwright E2E 后续)

**Target Platform**: Linux 服务器 / 单机 Docker Compose(开发态支持 Windows 混合模式)

**Project Type**: Web service(`backend/` + `frontend/` + Celery `worker`)

**Performance/Scale Goals**: 同步成功率 > 95%(SC-001)· 交接包生成 < 30min(SC-002)·
审批审计覆盖 / 执行可回放 = 100%(SC-004/005)

**Constraints**: 默认开发端口集中管理;敏感内容默认不展示;凭证不明文返回;签名 URL 必过期;
跨租户查询强制 `tenant_id`;采集/执行幂等可重试

## Constitution Check

*GATE: 进入实现前必须通过;设计变更后复检。逐条对照 `.specify/memory/constitution.md`。*

| 宪法原则 | 本特性是否符合 | 说明 |
|---|---|---|
| I. MySQL 单一主库 | ✅ 通过 | 业务全走 `mysql+asyncmy`;PG/ClickHouse 已隔离到 observability profile |
| II. 串行 + 幂等写入 | ✅ 通过 | 发布沿用 advisory-lock;采集/执行设计为幂等 + `partial-failed`(FR-005) |
| III. 测试先行 | ⚠️ **违规** | 控制平面新链路测试缺失 → 记入 Complexity Tracking + tasks.md 优先清偿 |
| IV. 审计/证据不可绕过 | ✅ 通过 | FR-009/013 强制证据可追溯 + 写操作审计 |
| V. 最小暴露/凭证/LLM 仅建议 | ✅ 通过 | FR-003/008/011 + reveal 审批四件套;LLM 仅产建议 |

## Project Structure

### Documentation (this feature)

```text
specs/001-agent-control-plane/
├── spec.md      # 需求(本次已迁移)
├── plan.md      # 本文件
└── tasks.md     # Epic → 任务(本次已迁移,含 [P] 与 [Story] 标记)
# 可选后续产物:research.md / data-model.md / contracts/ / quickstart.md
# 注:详细数据模型与 API 契约暂以源 PRD 为准:
#   docs/agent-control-plane-prd/03-data-model-and-mysql.zh-CN.md
#   docs/agent-control-plane-prd/04-api-contract.zh-CN.md
```

### Source Code (repository root) — 现有真实布局

```text
backend/
├── app/
│   ├── api/v1/endpoints/   # analysis, control_plane, iam, namespaces, skills, clinic, …(17)
│   ├── models/             # 控制平面领域模型(17)
│   ├── schemas/
│   ├── services/           # adapter_collection / analysis / report_upload / component / …(23)
│   │   └── adapters/       # Pull 退役中(T081):仅存 backup 上传导入 + 归一化落库
│   ├── workers/            # Celery
│   └── core/
├── alembic/versions/       # 0001–0014(0009–0014 = 控制平面)
└── tests/                  # ⚠️ 仅 3 个文件 — 见 Complexity Tracking

frontend/
└── src/                    # React + AntD;⚠️ 0 测试
```

**Structure Decision**: 采用 Web service 双栈(`backend/` + `frontend/` + Celery worker),
沿用既有单体 FastAPI 应用,新增控制平面领域置于现有 `services/` + `services/adapters/`。

## Complexity Tracking

> 记录对宪法/简单性的合理偏离;每条都给出更简单方案被否决的理由。

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| 单体 FastAPI 内 23 个 service,未做成 Library-First 独立库 | 控制平面与既有注册表强耦合(共享 IAM/审计/MinIO/Git),拆库成本高于收益 | 拆成独立库会引入跨库事务与重复脚手架;当前单库 + 清晰 service 边界已可维护 |
| PostgreSQL + ClickHouse 与「MySQL 单一主库」并存 | 自托管 Langfuse 强依赖 PG + ClickHouse,无 MySQL 后端 | 放弃 Langfuse 会失去 evaluation observability;已用 `observability` profile 隔离,不碰业务数据 |
| Test-First 部分清偿(后端 52 测试:authz/RBAC/发布门禁/交接状态机/凭证/reveal;前端 Vitest 基线起步)| 历史代码先行;控制平面在快速迭代期 | 剩余缺口:Adapter mock(T036)、前端关键流程 E2E → 按 specs/002 持续回填 |
| 交接领域逻辑内联在 API 层(`control_plane.py` ≈1.6k 行),无独立 `handover_service` | MVP 闭环优先,避免过早抽象 | 立即抽 service 会在 LLM 建议服务(T042)落地前二次返工;待 T042 时一并重构(2026-06-12 analyze I2 立案) |
