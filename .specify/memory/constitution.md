# DuckDock Constitution

> DuckDock 企业 AI Agent 资产与交接控制平面 · 不可绕过的工程原则
>
> 本宪法是项目的最高约束,优先级高于任何零散文档(README、handoff、roadmap)。
> 当代码、文档、规格三者冲突时,以本宪法 + `specs/` 下的规格为事实之源,并通过
> `/speckit-analyze` 持续校验一致性。

## Core Principles

### I. MySQL 单一主库 (MySQL-Only Primary Store)

- 业务主库**只用 MySQL 8.x**,异步驱动统一为 `aiomysql`;`DATABASE_URL` 形如
  `mysql+aiomysql://…@mysql:3306/duckdock?charset=utf8mb4`。
- **禁止** PostgreSQL 专属 SQL 进入业务路径:`jsonb` → `json`、
  `pg_advisory_xact_lock` → `GET_LOCK()`、`pgvector` → 外部向量服务(OpenSearch/Qdrant)。
- PostgreSQL 16 与 ClickHouse **只允许**出现在 `observability` compose profile 下,
  专供自托管 Langfuse,**不得**承载 DuckDock 业务数据。
- 验收红线:空 MySQL 实例必须能 `alembic upgrade head` 成功建表。

### II. 串行 + 幂等的写入 (Serialized & Idempotent Mutations)

- 同一 skill 的并发发布必须用 per-skill advisory lock 串行化;
  tag 唯一性校验、git push、MinIO 上传、`SkillVersion` 落库**全部在锁内**完成。
- 所有采集与执行动作(Push 上报落库、交接执行)必须**幂等且可重试**;
  部分失败降级为 `partial-failed` 等显式状态,**绝不**静默丢数据。
  *(2026-06-12:采集为 Push-only——现场 Reporter 上报,服务端不外联;见 specs/001 Clarifications。)*

### III. 测试先行 · 不可妥协 (Test-First, NON-NEGOTIABLE)

- Service 单元测试、状态机测试、Adapter mock-server 测试、权限越权测试
  **先于实现**编写,并在 CI 的真实 MySQL 上运行。
- 关键前端流程(创建交接单、审批)必须有 E2E;无权限按钮的隐藏/禁用必须可测。
- 合规进展(2026-07-11):后端 **55 测试文件 / ~495 个测试函数**(595 passed / 4 skipped;鉴权 / RBAC / 发布门禁 /
  交接状态机 / 凭证加密 / worktrace reveal / 上报会话 FSM / Reporter / 执行回执 + 证据上传 / 幂等 / SSRF / 验收),
  前端 **Vitest 5 文件 + Playwright 2 条 spec**。剩余缺口:后端 service 长尾覆盖率、mypy 收紧、目标环境恢复演练。

### IV. 审计与证据不可绕过 (Auditability & Evidence are Non-Bypassable)

- 每一次写操作和投递事件都进审计日志;审计日志**不可**由普通管理员删除。
- 每条交接建议、每个执行动作都必须可追溯到采集到的证据(`evidence_item`)。
- 执行动作可回放(目标:可回放率 100%、审批审计覆盖率 100%)。

### V. 最小暴露 · 凭证安全 · LLM 仅建议 (Least Exposure, Credential Safety, LLM-as-Advice)

- 敏感内容默认不展示;查看完整内容必须有**权限 + 原因 + 审批 + 审计**四件套。
- 凭证加密存储,**绝不**明文返回前端;对外签名 URL 必须设过期时间。
- LLM 产出的是**建议**而非最终处置;任何接管/转移/禁用动作都需人工审批后执行。
- 不做桌面监控、不采个人私域数据、不绕过厂商权限模型(见 PRD §8 非目标)。

## 技术栈与端口约束 (Tech Stack & Port Constraints)

- 后端 FastAPI(:8801)· 前端 React + Ant Design + TanStack Query(:5174)·
  Celery worker · Redis · MinIO · Git bare repo(skill 版本存储)。
- 仓库提供稳定的默认开发端口；发生冲突时只调整 host 侧映射，container 端口与服务内部 URL 保持不变。
- `analysis-worker` 与 `observability` 属于**可选 profile**,默认 `docker compose up` 不得启用。

## 质量门禁 (Quality Gates)

CI(`.github/workflows/ci.yml`)必须保持绿色,且不得削弱以下闸门:

1. `python -m compileall app alembic` — 后端可编译。
2. `python -m pytest` — 在真实 MySQL 8.x service 上跑测试。
3. `from app.main import app` — FastAPI 应用可导入。
4. `alembic upgrade head` — 迁移可在空 MySQL 落地。
5. `npm run build` — 前端可构建。
6. compose profile 校验 — 默认栈包含 mysql/redis/minio/backend/worker/beat/frontend；`analysis-worker`、
   `postgres`/`clickhouse`/`langfuse-*` 不得默认开启。

**门禁现状(2026-06-12)**:已加 `ruff`(阻塞)、`mypy` baseline ratchet(错误数只降不升,见 `backend/.mypy-baseline`)、
`tsc`(前端 build 内)、**核心安全模块覆盖率 ≥ 65%**(deps/iam/release_gate/credential,`--cov-fail-under`)。
**仍待补**:更多 service 的覆盖率抬升、mypy 错误逐步清零至 strict、生产恢复演练。

## Governance

- 本宪法优先于一切零散文档。新需求一律先进 `specs/<NNN>-<slug>/`(spec → plan → tasks),
  代码从规格派生,而非反过来事后补文档。
- README 与 `specs/` 必须与实现保持一致;每个里程碑前跑一次 `/speckit-analyze`,
  漂移(如数据库引擎、迁移数量与文档不符)视为必须修复的缺陷。
- 修订宪法需:版本号 + 日期 + 修订理由;对原则的合理偏离记录在对应 `plan.md` 的
  **Complexity Tracking** 表中,并说明为何更简单的方案不可行。
- 所有 PR / review 必须核对与本宪法的合规性。
- **修订记录**:v1.1.0(2026-06-12)—— 原则 II 同步 Push-only 采集决策;原则 III 刷新测试合规进展(3→13 文件);
  质量门禁补齐 mypy baseline ratchet + 核心模块覆盖率 65% 门槛。

**Version**: 1.1.0 | **Ratified**: 2026-06-08 | **Last Amended**: 2026-06-12
