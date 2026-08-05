<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan
<!-- SPECKIT END -->

# DuckDock — Agent 指南

> DuckDock 企业 AI Agent 资产与交接控制平面（Skills 注册表 + 控制平面双支柱）。

**最高约束**：先读 [`.specify/memory/constitution.md`](.specify/memory/constitution.md)。代码从 `specs/` 规格派生，
不要反过来事后补文档。新需求走 `specs/<NNN>-<slug>/`（spec → plan → tasks），里程碑前跑 `/speckit-analyze` 防漂移。

**关键架构约束**：
- 业务主库是 **MySQL 8.x**（`mysql+aiomysql://…@mysql:3306`，host `3307`），**不是** PostgreSQL。
  PG 16 + ClickHouse 仅 `observability` profile（自托管 Langfuse）。
- 仓库提供稳定的默认开发端口；发生冲突时只改 host 侧映射。
- `analysis-worker` / `observability` 是可选 profile，默认 `docker compose up` 不启用。
- 迁移在 `backend/alembic/versions/`（当前 head `0034`，共 34 个）；空 MySQL 必须能 `alembic upgrade head`。

**运行**：macOS / Linux / WSL 推荐 `bash scripts/dev.sh up` 全栈 Compose；Windows 可用 README §3.4 / `scripts/dev-start.ps1` 混合模式。

**架构**：双支柱全景（系统分层 / 发布门禁 / 交接 FSM / ER / RBAC）见 [`docs/architecture.md`](docs/architecture.md)。

**测试现状（2026-07-30）**：后端 **861 collected tests / 93 个测试文件**（默认 lane 845 passed；16 个 MySQL marker 在未配置 `TEST_MYSQL_URL` 时跳过）+ 核心模块覆盖率 65% 门；Runtime MCP 12 项；前端 **Vitest 5 文件 / 34 项 + Playwright 2 条 spec + eslint/build 门**。
剩余技术债：生产环境 backup→restore 演练、WorkBuddy 到点自主执行复验、后端 service 长尾覆盖率、mypy 收紧（见 specs/002-test-harness）。
