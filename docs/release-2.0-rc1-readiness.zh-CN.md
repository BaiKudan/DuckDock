# DuckDock 2.0.0-rc.1 发布就绪评估

> 评估日期：2026-08-05
> 候选版本：`2.0.0-rc.1`
> 分支：`codex/release-2.0-rc1`
> 结论：**达到工程 RC 发布门槛；尚不构成 GA 或任意生产环境上线授权。**

## 1. 发布结论

DuckDock 2.0 的计划内核心闭环已经形成可运行、可审计、可重复验证的整体：

```text
Runtime/Reporter
  → metadata-only Session/Run/Artifact/Telemetry evidence
  → Eval Hub / Dataset / Comparison / Experience
  → signed Agent Package + SBOM
  → policy + approval + promotion + canary + rollback receipt
  → evidence snapshot + obligations + acceptance + signed handover package
  → identity offboarding + credential revocation + key rotation
  → Prometheus SLO + incident + recovery receipt + 14-item readiness gate
```

本候选可用于内部发布、集成验收和受控试点。它不能被描述为“已经完成生产上线审批”，原因不是当前代码门禁失败，而是目标环境的 TLS、密钥托管、容量、HA、备份、监控接管、安全审计和变更审批只能在实际部署环境完成。

## 2. 当前验证结果

| 范围 | 结果 | 证据摘要 |
|---|---:|---|
| 冻结 API v2 契约 | PASS | `2.0.0-rc.1`，SHA-256 `aa260f301acc5c3a8004d14980952a03ce0197f9d70dcdd78cd62e986c3b1a83`，drift check clean |
| 数据库迁移 | PASS | 空 MySQL 8.4 从 baseline 升至 `20260804_0062`；`alembic check` 无新增操作 |
| 真实 Hermes | PASS | 本机 OpenAI-compatible 实例返回 1 个模型身份；真实 `/v1/embeddings` 推理 HTTP 200、384 维数值向量，只记录 shape |
| Agent Package | PASS | 独立空库通过 HTTP 创建 Agent/Runtime/Package；Ed25519、manifest、CycloneDX SBOM 与组件覆盖均验证通过 |
| Release Control | PASS | policy `ALLOW`；主晋级和 Runtime receipt `APPLIED`；失败 Canary 进入 `ROLLED_BACK`，rollback 与 receipt 均成功 |
| Handover 2.0 | PASS | 初始义务 `BLOCKED`，履约后 `READY`；MinIO archive digest readback 与外部 Ed25519 验签通过 |
| Identity/Security | PASS | SCIM disable 后用户请求和旧 workload credential 均为 401；事件幂等、交接触发和 signing-key rotation 通过 |
| 恢复演练 | PASS | 独立临时 MySQL dump→drop→restore 3 行；MinIO 对象 backup→delete→restore 1 个；RPO 0 秒、RTO 1 秒 |
| SLO | PASS | `HEALTHY`；142 个发布窗口请求、0 错误；ingest p95 7.729 ms、timeline p95 12.636 ms、policy p95 54.426 ms |
| Readiness | PASS | `READY`；14 PASS / 0 WARN / 0 BLOCK |
| 浏览器 | PASS | 隔离 RC 登录、Dashboard、Fleet、Package Registry、Release Control、Operations、Eval Hub 均渲染；console 0 warning/error |
| 后端回归 | PASS | 1015 passed / 19 skipped / 3 warnings；核心覆盖率 81.04%（门槛 65%） |
| Python 3.12 锁定环境 | PASS | hashed dev lock 全量 1015 passed / 19 skipped；runtime lock `pip-audit` 0 已知漏洞 |
| 真实 MySQL 测试 lane | PASS | 独立临时数据库 17 passed / 1014 deselected；验证后已删除 |
| 前端 | PASS | npm audit 0；lint 0 warning；11 files / 56 tests；生产 build 最大入口块 569.69 kB（门槛 600 kB） |
| 类型基线 | PASS with debt | mypy 81 errors，未超过 ratchet ceiling 82；这不是“类型全清零” |
| 生产镜像 | PASS | backend/frontend 非 root、精确 digest base、provenance/SBOM；Docker Scout 均为 0 Critical / 0 High |
| Compose/CI | PASS | dev/prod Compose config clean；CI action 固定 commit SHA，并阻断依赖、契约、测试、镜像 Critical/High 漏洞 |

## 3. 14 项实时门禁

本次门禁来自独立 Compose project、空业务库和真实本机 Hermes，不读取原开发库的历史 READY 状态。

| Check | Status |
|---|---:|
| Database migration head | PASS |
| Frozen API v2 contract | PASS |
| Runtime inventory | PASS |
| Verified Agent Package | PASS |
| Applied release receipt | PASS |
| Signed Handover 2.0 package | PASS |
| Directory offboarding evidence | PASS |
| Signing-key rotation | PASS |
| v1/v2 reconciliation | PASS |
| Transactional outbox | PASS |
| Recovery drill | PASS |
| Release SLO evaluation | PASS |
| Open operations incidents | PASS |
| Prometheus readiness | PASS |

## 4. 与 2.0 发布范围的匹配

当前实现已经覆盖 2.0 规格中作为控制平面的主要产品面：provider-neutral Runtime/Fleet、metadata-only execution evidence、Generic OTLP 与 Pack/ATIF、Hermes Reporter profile、Eval Hub、Langfuse 兼容适配层、签名 Package/SBOM、Release Control、Canary/rollback receipt、Handover 2.0、身份生命周期以及 Operations/readiness。

Langfuse 保持可替换的 provider adapter：关闭或不可用时，依赖它的 provider 查询明确返回 `503 Evaluation provider is unavailable`，DuckDock 的 Package、Release、Handover、Identity、Recovery 和 Readiness 核心状态机仍独立工作。该边界避免 Langfuse 升级直接绑死 DuckDock 数据模型。

## 5. RC 后仍存在的风险

1. **独立安全审计未完成。** 当前有代码、依赖和镜像门禁，但没有第三方渗透测试或审计报告。
2. **默认生产拓扑是单节点 Compose。** 未承诺控制面、MySQL、Redis、MinIO、Prometheus 的 HA 或跨故障域恢复。
3. **容量只完成工程门禁。** 目标并发、数据保留量、对象存储增长和长时间稳定性需要按部署方负载重新压测。
4. **目标环境控制未签字。** TLS、网络隔离、SOPS/age 或外部 Secret Manager、备份介质、告警接收人和变更审批仍是上线前硬门槛。
5. **类型债务仍有 81 项。** ratchet 阻止恶化，但后续版本应持续清零。
6. **外部 Provider 兼容性是持续门禁。** Langfuse、Hermes、OTel Collector 或其他 Harness 升级后必须重跑对应 compatibility gate。
7. **RC 不是 GA。** 发现阻断级缺陷时允许调整契约或迁移；GA 前必须冻结最终版本号并在目标环境重跑门禁。

## 6. GA 前必须完成

- 在目标生产等价环境填写并签署 README 第 12 节部署清单；
- 完成独立安全评审，至少覆盖身份、凭证、对象签名 URL、租户边界、SSRF、Pack archive 与供应链；
- 用真实生产密钥/TLS/域名完成部署，证明弱密钥会 fail closed；
- 按目标容量完成持续负载和存储增长测试；
- 完成生产备份介质的恢复演练，并把 RPO/RTO receipt 保存在受控证据系统；
- 接管 Prometheus/告警并明确 on-call；
- 在最终 GA commit 和生产镜像上重新执行本文件全部门禁；
- 由 Product、Architecture、Security、Operations owner 完成变更审批。

机器级详细结果见 [`release-candidate-rc1-20260805.md`](../specs/015-ga-candidate/evidence/release-candidate-rc1-20260805.md)。历史 `2.0.0-ga` 文档不再作为当前版本事实源。
