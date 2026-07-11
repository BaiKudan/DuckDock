# Feature Specification: 005 — 补全收口(可信 + AI 辅助 + 可上线)

**Feature Branch**: `005-completion-hardening`
**Created**: 2026-06-22
**Status**: Planning（方向已裁决，待逐阶段细化确认）
**Input**: 2026-06-22 全模块完成度审计（9 模块）+ 独立 code review（5 findings）+ 产品方向裁决。

## 问题域

完成度审计结论：DuckDock 的**治理 / 注册 / 管道层是生产级真实**（注册表 90 · IAM/企业/发布门禁 82），
但**招牌的"智能与自动化"层 overwhelmingly 默认关或桩**（控制平面执行 55 · 采集 62 · clinic 68）。
即 `docker compose up` 默认起来得到的是一个**称职的治理型记录器**，但几乎不跑任何宣传里的 AI，
且无显著信号提示智能层是关的。此外独立 review + 审计发现若干**真实安全/正确性缺口**。

本特性把"未完成"收口为**可信 + AI 辅助 + 可上线**，按依赖分层堆叠执行。

## 决策已定（2026-06-22 · 用户裁决）

- **终局 = 治理记录器 + AI 辅助**。启用 AI 建议/分析/评分层；交接**执行保持「人工 + 真实回执」并补强**（前端可完整驱动闭环）。
- **真实厂商 live 采集适配器（L1）与真实 provider-side / 自治·探针执行（L3 / FR-019·T085）明确延后为独立特性，不在本特性范围**。数据入口维持 Push 自报。
- **起步 = L0 安全与正确性收口**。
- **AI = 统一单一 LLM 供应商 + 默认开 + 降级显式化**：无 key / 调用失败时，API 字段与 UI 徽标明确标注「启发式降级」，不再静默伪装成 AI。

## 范围内（In scope）

- **L0 可信收口**：限流防绕过 + 真·失败锁定、FR-012 自由文本遮蔽 + 异步上传、Webhook SSRF、SSO 密钥加密 + id_token 验签、DM-03 存量库迁移、保留期 GC 自动调度。
- **L2 AI 层启用与质量**：供应商统一可配 + 默认开 + 降级显式；clinic 8 维全 LLM、分析 LLM 模式、扫描 AI、交接顾问默认走 LLM。
- **Phase 3 手动交接闭环补强**：前端 approve→execute→verify 完整接线；执行回执要求证据。
- **L4 生产硬化与可观测**：prod compose 硬化、analysis-worker 入 prod、Langfuse 可选、DM-01-FULL。
- **横切 · 测试补强**：scanner/sandbox、SSO、前端 E2E、mypy 收紧。

## 范围外（Out of scope · 后续独立特性）

- **L1**：真实厂商 API/探针 live 采集、Adapter Worker（`adapters/*.collect()` 维持现状，Push 为唯一入口）。
- **L3**：交接执行对厂商平台的真实动作（停用/转移/归档）、FR-019 auto/探针执行（T085）。
- **clinic auto-fix**：推荐项的自动修复执行（保持人工标记 done）。

## 需求（FR）

- **FR-501（CRITICAL · 安全）**：启用限流后 MUST NOT 被伪造 `X-Forwarded-For` 绕过；限流 MUST 为真·失败锁定（成功登录不计入/不锁定）；FR-012 交接包 MUST NOT 含可识别凭证明文（遮蔽覆盖所有自由文本字段）；Webhook 投递 MUST 拒绝内网/元数据/非 http(s) 目标；SSO client_secret/ldap_bind_password MUST 静态加密，OIDC id_token MUST 验签。
- **FR-502（HIGH · 正确性）**：4 个可空 FK 的 `ON DELETE SET NULL` MUST 经幂等迁移对**存量 MySQL** 生效（不只新库）；保留期 GC MUST 被周期调度（非仅手动）。
- **FR-503（HIGH · AI）**：单一 LLM 供应商 MUST 可配且默认启用；无 key/失败时 MUST 在 API 响应 + UI 显式标注「启发式降级」；clinic 8 维 MUST 全部经 LLM、分析/扫描/交接顾问 MUST 默认走 LLM 路径（有 key 时）。
- **FR-504（HIGH · 闭环）**：管理员 MUST 能从前端完整驱动一单交接 approve→execute（含回执+证据）→verify→completed。
- **FR-505（MEDIUM · 上线）**：prod compose MUST 含 analysis-worker 与资源限额，MUST 提供 TLS/密钥/监控接入位；Langfuse 可选启用。
- **FR-506（持续）**：scanner/sandbox/SSO/前端关键路径 MUST 补回归测试；mypy 基线逐步收紧。

## Success Criteria

- **SC-1（可信）**：启用限流后伪造 XFF 不绕过、成功登录不被锁；FR-012 包无可识别凭证模式；Webhook 拒内网；SSO 密钥密文存储——均有自动化测试。
- **SC-2（AI 真实）**：配置供应商 key 后 AI 真跑；不配 key 时 API/UI 明确「降级」，且 clinic 门禁不再以启发式分数伪装 AI 评分。
- **SC-3（闭环可用）**：一名管理员在 UI 走完整交接闭环 approve→execute（回执+证据）→verify→completed。
- **SC-4（可上线）**：prod 一键起含 analysis-worker；后端门禁全绿；空 MySQL `alembic upgrade head` + `alembic check` 通过。

## 非目标 / Assumptions

- 单租户部署假设不变（specs/003）；TLS 终止仍由外层反代承担。
- 测试默认 SQLite 内存库；MySQL 实测道（specs/004）继续用于迁移/并发回归。
- 不在本特性内建真实厂商集成或自治执行（见范围外）。

## 决策待确认（执行到对应阶段前 MUST 找用户）

- **⚠ L2-PROVIDER**：统一哪个 LLM 供应商 + 型号？是否同一供应商覆盖 scan / clinic / analysis / handover-advisor 四条链？（当前 scan 用 Anthropic、analysis 用 DashScope-Qwen，需统一或网关化。）
- **⚠ P3-SCOPE**：手动执行"补强"的确切边界——是否每个 ExecutionAction 完成 MUST 附证据？是否要完成前校验清单？
- **⚠ L4-TARGET**：部署目标（单机 compose + 外层反代 / k8s？）与密钥方案（env-file / vault / 云 KMS）？
