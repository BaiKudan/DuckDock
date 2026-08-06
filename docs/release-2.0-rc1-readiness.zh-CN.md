# DuckDock 2.0.0-rc.1 发布就绪评估

> 评估日期：2026-08-06
> 候选版本：`2.0.0-rc.1`
> 分支：`codex/release-2.0-rc1`
> 结论：**达到应用与仓库工程 RC 发布门槛；未达到 2.0 GA 发布门槛，也不构成任意生产环境上线授权。**

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

本候选可用于内部发布、集成验收和受控试点。它不能被描述为“2.0 已正式发布”或“已经完成生产上线审批”。仓库已经提供目标 TLS/密钥/网络/告警/备份/HA/容量/授权的实现与 fail-closed 门禁，但真实目标环境的执行回执、独立安全结论和四方签字不能由本地验证替代。

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
| 浏览器 | PASS | Playwright 3/3：未登录守卫、登录表单、审批→执行→回执→验证→完成；应用内浏览器复核 Operations/Release Control，console 0 warning/error |
| 后端回归 | PASS | 1113 passed、19 skipped；核心覆盖率历史门禁 81.04%（门槛 65%） |
| Python 3.12 锁定环境 | PASS | hashed dev lock；runtime lock `pip-audit` 0 已知漏洞；Ruff/compile 通过 |
| 真实 MySQL 测试 lane | PASS | 独立临时数据库 17/17；验证后数据库与用户均已删除 |
| 前端 | PASS | npm audit 0；lint 0 warning；11 files / 56 tests；生产 build 最大入口块 569.69 kB（门槛 600 kB） |
| 类型基线 | PASS with debt | mypy 81 errors，未超过 ratchet ceiling 82；这不是“类型全清零” |
| 自有生产镜像 | PASS | backend/frontend/TLS gateway/Alertmanager 非 root；Docker Scout 均为 0 Critical / 0 High |
| 本地生产基础设施 | PASS | 隔离 Compose 10/10 healthy；TLS 1.2/1.3，拒绝 1.0/1.1；hostname/HSTS/告警 firing+resolved 通过，随后零残留清理 |
| 容量工程基线 | PASS | 60 rps×900s + 120 rps×60s，61,200 Run/Audit/Outbox；持续 p95 8.023 ms，增长后 timeline p95 5.187 ms |
| 本地 Kubernetes HA 演练 | PASS (local reference) | kind 1 control-plane + 3 zone workers；三类 3 副本、Beat 1；整区 taint/drain 后 30 秒恢复，7 个连续 health/API 样本 0 失败，故障域回归后各 ReplicaSet 恢复三域覆盖；状态服务/RWX/CNI 未授权 |
| GA 生产授权 | BLOCKED | 真实目标 HTTPS 容量/HA/异地恢复/告警回执、独立安全评估与四方签名尚未提供，授权器必须拒绝 |
| Compose/CI | PASS | dev/prod Compose config clean；CI action 固定 commit SHA，并阻断依赖、契约、测试、镜像 Critical/High 漏洞 |

## 3. 14 项实时门禁

本次应用门禁来自独立 Compose project、空业务库和真实本机 Hermes，不读取原开发库的历史 READY 状态。它证明应用闭环，不是目标环境生产授权。普通开发库在 Operations 页面显示 `BLOCKED` 是正确行为。

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

1. **独立安全审计未完成。** 当前有代码、依赖和镜像门禁，但没有与最终 commit/镜像摘要绑定的第三方渗透测试或审计报告。
2. **目标网络隔离尚未取得真实回执。** 仓库现提供 fail-closed 的 `duckdock-ga-network-evidence-v2` 执行器：从已确认的集群外视角执行 1–65535 TCP 与三个数据端口 nmap 扫描，记录 CNI/Namespace/Pod/NetworkPolicy 原始身份，并用可达对照目标验证 ingress/egress 正反向路径；GA 门禁会重新解析原始字段，不能靠手填 PASS 绕过。但该执行器尚未在真实目标网络/CNI 上运行。
3. **HA 已完成本机真实 Kubernetes 无状态演练，但未完成目标环境承诺。** `ops/kubernetes/ha` 的受限镜像、PDB/HPA、严格且 taint-aware/revision-aware 的拓扑分散已经在三模拟 zone 中执行节点/整区 drain、Beat 迁移与恢复后再均衡；仓库现提供 fail-closed 的目标 v2 执行器，可校验精确 context/镜像、整区节点、持续公网 HTTPS、自动清理、网络证据和 Operations 签名的状态服务回执，但尚未在真实目标集群运行。本地单节点 MySQL/Redis/MinIO、`emptyDir` 和未证明 enforcement 的 kindnet 不代表托管 MySQL/Redis/S3/RWX/CNI。
4. **目标容量尚未证明。** 本地真实 MySQL 工程基线通过，300 rps 边界探针也能 fail closed；仍需通过真实目标 HTTPS、负载均衡器和目标数据服务重跑 G2。
5. **目标运维回执缺失。** 仓库现提供主动 Alertmanager firing→人工 ack→resolved 的 v2 采集器，并要求 delivery 服务与值班人员用不同身份签署三份原始回执；Secret 轮换也已升级为 metadata-only v2 采集器，要求 provider 与独立 verifier 分别签署轮换/停用和旧拒绝/新可用回执，并重算 Secret resourceVersion、Deployment generation 与 Pod UID 全量替换。异地恢复已升级为 v2：存储服务、恢复执行人、独立验证人分别签署 Object Lock/对象版本、恢复阶段结果与 MySQL/对象/Git/服务验证，门禁重算 RPO/RTO。但这些执行器尚未在真实通知供应商、on-call schedule、生产 Secret Manager、异地不可变备份介质和非生产恢复目标上运行。
6. **随 Compose 打包的第三方数据镜像不属于 GA 路径。** 本地扫描发现 MySQL/MinIO 官方镜像仍含 Critical/High；单机 Compose 仅作加固参考，GA 强制使用经独立评估的外部 HA MySQL/Redis/S3。Prometheus 的 1 个 High 需要独立评估/VEX 确认，项目不得自行豁免。
7. **类型债务仍有 81 项。** ratchet 阻止恶化，但后续版本应持续清零。
8. **外部 Provider 兼容性是持续门禁。** Langfuse、Hermes、OTel Collector 或其他 Harness 升级后必须重跑对应 compatibility gate。
9. **RC 不是 GA。** 最终版本必须改为 `2.0.0`，使用 registry `@sha256` 镜像，在同一 commit 上重跑全部门禁并取得 `GA_AUTHORIZED`。

## 6. GA 前必须完成

- 用真实生产密钥、域名和 TLS 部署 `ops/kubernetes/ha` 目标 overlay，替换所有占位镜像/域名/egress；
- 从目标集群外运行 `collect_ga_target_network.py`，证明仅 443 公网开放、数据服务直连端口不可达，并实际执行受信/非受信 ingress 和批准/拒绝 egress；
- 在真实目标 HTTPS 上执行 G2 容量与数据增长门禁，并用 `collect_ga_target_ha.py` 完成节点/zone/Beat/托管数据服务故障注入；
- 用 `collect_ga_target_recovery.py` 从异地、加密、至少 30 天 Object Lock 的介质做破坏性 staging 恢复，取得存储/执行/独立验证三类签名回执；
- 用 `collect_ga_target_secrets.py` 采集生产 Secret Manager 轮换，取得 provider/verifier 不同密钥的原始签名回执，并证明 backend/worker/beat 全量滚动；
- 用 `collect_ga_target_alerting.py` 触发和恢复目标告警，由 delivery 服务签署投递回执、实际值班人员签署 ack，并证明目标接收人与 on-call schedule；
- 完成独立安全评审/VEX，关闭最终应用镜像和依赖中的全部 Critical/High；
- 将版本冻结为 `2.0.0`，使用 registry `@sha256` 镜像，在最终 commit 上重跑全部门禁；
- 由发布机构通过受控、内容寻址的组织策略固定共享信任库和角色身份，再由 Product、Architecture、Security、Operations 四个不同身份签署同一 release/target/evidence/policy 摘要；
- 运行生产授权器并取得唯一可接受结果 `GA_AUTHORIZED`。

机器级详细结果见 [`release-candidate-rc1-20260805.md`](../specs/015-ga-candidate/evidence/release-candidate-rc1-20260805.md)、[`capacity-reference-small-pass-20260805.json`](../specs/016-ga-production-authorization/evidence/capacity-reference-small-pass-20260805.json)、[`local-kubernetes-ha-rehearsal-20260805.json`](../specs/016-ga-production-authorization/evidence/local-kubernetes-ha-rehearsal-20260805.json) 和 [`local-infrastructure-20260805.json`](../specs/016-ga-production-authorization/evidence/local-infrastructure-20260805.json)。目标授权流程见 [`ga-production-authorization.zh-CN.md`](ga-production-authorization.zh-CN.md)。历史 `2.0.0-ga` 文档不再作为当前版本事实源。
