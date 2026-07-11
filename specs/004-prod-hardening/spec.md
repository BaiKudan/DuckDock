# Feature Specification: 生产硬化(P0/P1/P2 remediation)

**Feature Branch**: `004-prod-hardening`
**Created**: 2026-06-17
**Status**: Ready-for-execution（`tasks.md` 为可执行清单）
**Input**: 2026-06-17 全面 review(8 维 fan-out + 对抗式验证)确认的 31 条 findings。

## 问题域

最新 `main`(dec47e7)基础健康(156 后端测试 / 12 前端 / 门禁全绿),但 review 确认了
**2 个 CRITICAL(生产 MySQL 必崩,被 SQLite 测试掩盖)+ 14 个 HIGH + ~13 个 MEDIUM**。
本特性把它们收口为可执行、可验证的修复任务,使项目达到「可上生产」。

## 需求(每条 = 缺陷消除 + 回归测试锁定)

- **FR-001(CRITICAL)**: 所有 `DateTime(timezone=True)` 列与 aware `now` 的比较,在 MySQL(读回 naive)下 MUST NOT 抛 `TypeError`。
- **FR-002(CRITICAL 安全网)**: MUST 有能在 MySQL 上捕获 tz-aware 比较类缺陷的测试道(否则 SQLite 绿灯继续掩盖)。
- **FR-003(HIGH)**: 交接状态机 MUST 无死锁(零执行项)、拒绝 MUST 为终态、执行 MUST 幂等、前段转移 MUST 有来源态守卫。
- **FR-004(HIGH)**: 角色权限变更 MUST 对存量 RoleBinding 生效(权限缓存失效)。
- **FR-005(HIGH)**: analysis 读端点 MUST 经权限门 + 敏感过滤 + 审计,MUST NOT 用裸 CurrentUser。
- **FR-006(HIGH)**: 分析 job 在 worker 崩溃后 MUST 能被回收为终态(reaper),MUST NOT 永久卡死。
- **FR-007(HIGH)**: 失败状态(ingest/采集)MUST 持久化(不被回滚),Celery engine MUST dispose(不泄漏连接)。
- **FR-008(HIGH)**: 生产部署 MUST 能正确连上对象存储、有探活、CORS 可配;迁移 MUST 可信。
- **FR-009(MEDIUM)**: partial-failed MUST 可达;自动建议 MUST 回链证据;幂等键 MUST 被校验;限流/可观测性/备份补齐。
- **FR-010(决策已定 2026-06-17)**: FR-012 出站交接包 → **本期实现精简版**(tasks `[FR-012-OUTBOUND-PACKAGE]`);FR-019 auto → **本期不做**(manual + 501 兜底为最终态,探针执行器另立特性);DM-01 迁移基线 → **本期只做 LITE**(漂移闸 + server_default 对齐),FULL 独立排期。执行可连续推进至 P2。

## Success Criteria

- **SC-001**: P0 完成后,在**真实 MySQL** 上设过期 robot token / 过期 public release,鉴权与公开下载不再 500(有自动化测试覆盖)。
- **SC-002**: P1 完成后,review 中全部 CRITICAL + HIGH 关闭,每条有回归测试;门禁(ruff/mypy baseline/核心覆盖率/eslint/build)保持全绿。
- **SC-003**: 交接闭环端到端无死锁/无翻案/无重复执行;空 MySQL `alembic upgrade head` 可靠且 `alembic check` 无漂移。
- **SC-004**: 每个修复任务一个提交,提交信息引用 finding id;无任何现有门禁被削弱。

## 非目标 / Assumptions

- 不在本特性内引入新业务功能(除非 FR-010 决策点被批准)。
- 单租户部署假设不变(specs/003);TLS 终止仍由外层反代承担(prod compose 文件头已声明)。
- 测试默认 SQLite 内存库;新增 MySQL 测试道为补充,不替换现有夹具。
