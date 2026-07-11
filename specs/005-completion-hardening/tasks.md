---
description: "补全收口执行清单——2026-06-22，基于完成度审计、独立 review 与产品裁决"
---

# Tasks: 补全收口(L0 可信 → L2 AI → P3 闭环 → L4 上线)

**Input**: specs/005-completion-hardening/spec.md。**目标读者**: 维护者与自动化执行工具。
**方向已定**: 治理记录器 + AI 辅助;L0 优先;统一供应商 + 默认开 + 降级显式化。真实厂商适配器 / 自治执行 = 范围外。

> **📌 收尾执行清单见 [`tasks-remaining.md`](./tasks-remaining.md)（2026-06-24）。**
> L0 + L2 + 二轮独立 review 修复均已合入 `main`。`⚠DECISION` 门已全部裁决：
> (1) 范围 = 全量收尾 L2尾巴 → P3 → L4;(2) P3 证据 = 敏感强制/普通可选;
> (3) L4 部署 = 单/少机 Docker Compose + 外置加密密钥(SOPS/age)。
> `tasks-remaining.md` 是带 task-id / 文件锚点 / 验收 / 依赖的可执行 backlog；
> 本文件下方为旧的高层分解，保留作背景参考。

---

## ⚙️ 执行协议(每个任务都遵守)

1. **一次一个任务**,按 L0 → L2 → P3 → L4 顺序;组内可并行,同文件任务串行。
2. **改行为必有测试**(宪法原则 III):先红后绿。
3. **每任务跑完整门禁全绿才提交**,不得削弱(ruff / `.mypy-baseline=95` / 核心覆盖率 65 / eslint / build)。
4. **一任务一提交**,首行引用 task id;结尾 `Co-Authored-By:`。
5. **迁移**:空 MySQL 能 `alembic upgrade head`、幂等(inspector 守卫)、含 `downgrade`、过 `alembic check`。
6. **遇到 `⚠DECISION` 的任务先停下找用户确认**,不擅自实现或猜产品方向。
7. 不改既定决策(Push-only 数据入口 / 执行非自治 / Fernet / 权限键隔离);冲突即停报告。

门禁命令见 specs/004-prod-hardening/tasks.md §门禁(同款)。

---

## Phase L0 — 可信收口(安全 + 正确性 · 先做,无 ⚠DECISION,可直接执行)

### [L0-SEC-XFF] CRITICAL · 限流可被伪造 XFF 绕过
- **文件**: `backend/app/core/ratelimit.py`(`client_ip` L127-134)、`backend/app/core/config.py`、`auth.py`。
- **根因**: 无条件取 `x-forwarded-for` 第一跳且有 XFF 就忽略 `request.client`;轮换 XFF 即换桶绕过。
- **改法**: 加 `RATE_LIMIT_TRUSTED_PROXIES`(IP/CIDR 列表,默认空)。仅当对端在信任代理内才解析 XFF(取最右可信跳或按信任跳数),否则用 `request.client.host`。默认空 ⇒ 一律用 peer IP。
- **测试**: 伪造 XFF(对端非信任)→ 仍按 peer IP 分桶(绕过失败);配置信任代理 → 正确取真实客户端。
- **验收**: 非信任来源无法靠 XFF 绕过。

### [L0-SEC-RL-FAIL] HIGH · 限流"失败锁定"实为"尝试锁定"(成功也计入)
- **文件**: `ratelimit.py`(`check`)、`auth.py`(login L104)、token/register 路径。
- **根因**: `check()` 在验密码前无条件 `incr`,成功登录也消耗次数 → 正常用户超阈值被锁。
- **改法**: 拆为 `precheck(scope,identity,ip)`(仅查 `is_locked` → 命中 429,**不计数**)+ `register_failure(...)`(失败后 incr,超阈值 set_lockout)+ 成功后 `reset(...)` 清零。login/token 校验**失败**时才 `register_failure`,**成功**时 `reset`。
- **测试**: 连续成功登录 N+1 次不锁;连续失败超阈值锁定;锁定期内即便密码对也 429。
- **验收**: 仅失败计入锁定。

### [L0-SEC-PKG-MASK] CRITICAL · FR-012 交接包自由文本未遮蔽
- **文件**: `backend/app/services/handover_package_service.py`(`_evidence_ref` L89、execution `note` L123、item `risk_reason` L150、非敏感 trace.summary L78)。
- **根因**: 仅按敏感级遮蔽 WorkTrace 摘要;evidence.summary / note / risk_reason / 普通 trace.summary 原样进 zip;docstring 却称"绝不含明文凭证"。
- **改法**: 抽 `_sanitize_freetext(s)`(复用/对齐既有敏感过滤),对包内所有自由文本字段过一遍(凭证/密钥模式遮蔽);docstring 把"绝不"改为实际保证(选择性遮蔽 + 模式扫描)。
- **测试**: 在 evidence.summary / note / risk_reason / 普通 trace.summary 各放一个凭证样式串 → 断言包内被遮蔽。
- **验收**: 包内无可识别凭证模式。

### [L0-PERF-PKG-IO] MEDIUM · FR-012 在 async 端点里同步上传阻塞 event loop
- **文件**: `handover_package_service.py`(`_put_object` L50、`build_package` L209)。
- **改法**: `_put_object` 走 `asyncio.to_thread`(或复用 artifact_service 的 `*_async`);可选把 zip 构建/sha256 也卸载。
- **测试**: 断言上传经 `to_thread`(mock)/ build_package 不在事件循环内阻塞。
- **验收**: 打包上传不阻塞事件循环。

### [L0-SEC-WEBHOOK-SSRF] HIGH · Webhook 目标 URL 无 SSRF 校验
- **文件**: `backend/app/schemas/webhook.py`、`backend/app/workers/webhook_tasks.py`(L43)。复用 LLM 端点那套 SSRF guard。
- **改法**: 创建/更新 webhook 时校验 URL scheme∈{http,https} 且解析 IP 非私网/环回/链路本地/元数据(169.254.169.254);投递前再校验一次(防 DNS rebinding)。
- **测试**: 配内网/元数据/file:// URL → 拒(422);公网 URL → 通过。
- **验收**: 租户 webhook 不能打内网。

### [L0-SEC-SSO] HIGH · SSO 密钥明文 + id_token 未验签
- **文件**: `backend/app/models/iam.py`(client_secret L210 / ldap_bind_password L213)、`backend/app/services/sso_service.py`(L89-171、id_token L168)。
- **改法**: ① client_secret/ldap_bind_password 静态加密(复用 `credential_service` 的 Fernet),读写处解密;② OIDC id_token 经 JWKS 验签(issuer/aud/exp),去掉 unverified-claims 回退;③ SSO 出站 HTTP 加 SSRF guard。
- **测试**: 密钥落库为密文且能解出;伪造/过期 id_token 被拒;SSO 出站内网被拒。
- **验收**: SSO 凭证密文存储 + 令牌验签。
- **注**: 涉及模型列变更 → 需幂等迁移(列加密为应用层,列类型不变则可能免迁移;若加列存 nonce 等则加迁移)。

### [L0-DM-03-MIGRATION] HIGH · FK ondelete 对存量库未生效
- **文件**: 新迁移 `backend/alembic/versions/`;模型已是 `ondelete="SET NULL"`(`models/control_plane.py:348/540/698/699`)。
- **根因**: fc3a22c 仅改模型(靠 0009 create_all 对新库生效),存量 MySQL FK 仍隐式 RESTRICT → 删 runtime/asset 被挡。
- **改法**: 幂等迁移:对 collection_jobs.runtime_id / ai_assets.source_runtime_id / work_traces.runtime_id / work_traces.asset_id,drop 现有 FK 约束 + 重建为 `ON DELETE SET NULL`(inspector 查约束名守卫 + downgrade 反向)。
- **测试/验收**: 空 MySQL `alembic upgrade head` + `alembic check` 通过;存量库迁移后删父行子行 FK 置 NULL。

### [L0-OPS-RETENTION-BEAT] HIGH · 保留期 GC 从未被调度
- **文件**: `backend/app/workers/celery_app.py`(beat_schedule)、`lifecycle_tasks.py`。
- **改法**: 把 `run_gc` 包成 celery task 并加 beat 条目(周期可配,默认如每日);确认 beat 服务已部署(specs/004 已加 beat 容器)。
- **测试**: 断言 beat_schedule 含 GC 条目 + task 可同步跑通。
- **验收**: 保留策略稳态自动执行。

### [L0-OPS-SWEEP] MEDIUM(可选,放 L0 尾)· 审计日志 / 过期公开发布无清理
- **改法**: GC task 顺带按保留窗清理 audit_logs 与过期 public_skill_releases(批量、可配);默认保守。
- **验收**: 表不再无界增长。

---

## Phase L2 — AI 层启用与质量(⚠DECISION:供应商)

> **⚠ L2-PROVIDER（先确认再开做整组）**:统一哪个 LLM 供应商 + 型号?是否同一供应商覆盖 scan/clinic/analysis/advisor?是否引入统一 LLM 网关抽象?——确认后再细化下列任务的 client/config。

- **[L2-PROVIDER-UNIFY]** 统一 LLM 配置:单一 `DUCKDOCK_LLM_*`(或网关)供 scan/clinic/analysis/advisor 共用;`*_ENABLED` 默认开。
- **[L2-DEGRADE-EXPLICIT]** 降级显式化:无 key/调用失败时,在响应加 `ai_degraded: true` + `mode: "heuristic"` 字段,前端显示「启发式降级」徽标;clinic 评分标注来源(LLM vs baseline),门禁消费分数时记录其来源到 gate_result。
- **[L2-CLINIC-8DIM]** clinic LLM judge 覆盖全 8 维(非仅 4),去掉 55/45 盲混或显式记录混合权重;补评分逻辑测试。
- **[L2-ANALYSIS-LLM]** 分析 worker:`auto` 模式无 key 不静默降级 —— 显式标注;LLM 模式接入统一供应商;补 live-契约测试(mock provider 校验 schema)。
- **[L2-SCAN-AI]** 发布 AI 深扫默认开(有 key);scanner/sandbox 补单测(当前零覆盖)。
- **[L2-ADVISOR-LLM]** 交接顾问默认走 LLM(有 key);失败回退规则版但显式标注 degraded。

---

## Phase 3 — 手动交接闭环补强(⚠DECISION:补强边界)

> **⚠ P3-SCOPE（先确认)**:执行回执是否 MUST 附证据?完成前是否要校验清单?执行仍为**人工动作 + 记录**(非 provider-side 真实调用,见范围外)。

- **[P3-FE-CHAIN]** 前端接线 approve→execute→verify:`frontend/src/api/client.ts` 补缺失的 control-plane 方法;ControlPlanePage 增审批/执行/验收 UI(蒙层 + 回执表单 + 证据上传引用),驱动既有后端 FSM。
- **[P3-RECEIPT-EVIDENCE]** 执行回执强化:`complete_execution_action` 按 P3-SCOPE 要求附证据/校验(沿用 HANDOVER-05 的失败确认)。
- **[P3-FE-E2E]** 前端 Playwright E2E:覆盖登录 + 一单交接闭环(横切测试的一部分)。

---

## Phase L4 — 生产硬化与可观测(⚠DECISION:部署目标)

> **⚠ L4-TARGET（先确认)**:部署目标(单机 compose + 外层反代 / k8s)?密钥方案(env-file / vault / 云 KMS)?

- **[L4-PROD-WORKER]** analysis-worker(+ 其 env)纳入 `docker-compose.prod.yml` 与 `.env.prod.example`;否则分析流水线在 prod 无运行体。
- **[L4-PROD-HARDEN]** prod compose 补:backend 资源限额、TLS 终止指引/位、密钥方案接入位、日志/监控接入(沿用 OPS-05 JSON 日志 + request-id)。
- **[L4-LANGFUSE]** Langfuse 可选启用路径文档化 + clinic trace_id 落库(当前 best-effort)。
- **[L4-DM-01-FULL]** DM-01-FULL:0001 baseline 固化为静态 DDL(替代 create_all)、0006 ENUM 收尾;空库 upgrade + alembic check 全绿。(高风险,独立小版本)

---

## 横切 · 测试与门禁补强(贯穿)

- scanner / sandbox 单测;SSO/OIDC/LDAP 流程测试;前端 E2E;mypy 基线逐步从 95 收紧。

---

## 执行顺序与依赖

```
L0（优先，全部无 DECISION，可立即执行）
  并行: SEC-XFF · SEC-RL-FAIL · SEC-PKG-MASK · PERF-PKG-IO · SEC-WEBHOOK-SSRF · SEC-SSO · DM-03-MIGRATION · OPS-RETENTION-BEAT (+ OPS-SWEEP)
L2(L0 后;先确认 ⚠L2-PROVIDER)
P3(可与 L2 并行;先确认 ⚠P3-SCOPE)
L4(收尾/并行;先确认 ⚠L4-TARGET)
横切测试: 全程
```

**完成定义**: SC-1~4 满足;门禁未削弱;范围外项(L1/L3/auto-fix)明确记录为后续特性。
