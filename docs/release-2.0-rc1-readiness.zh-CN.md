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
| 后端回归 | PASS | 1211 passed、19 skipped；核心覆盖率历史门禁 81.04%（门槛 65%） |
| Python 3.12 锁定环境 | PASS | hashed dev lock；runtime lock `pip-audit` 0 已知漏洞；Ruff/compile 通过 |
| 真实 MySQL 测试 lane | PASS | 独立临时数据库 17/17；验证后数据库与用户均已删除 |
| 前端 | PASS | npm audit 0；lint 0 warning；11 files / 56 tests；生产 build 最大入口块 569.69 kB（门槛 600 kB） |
| 类型基线 | PASS with debt | mypy 81 errors，未超过 ratchet ceiling 82；这不是“类型全清零” |
| 自有生产镜像 | PASS | backend/frontend/TLS gateway/Alertmanager 非 root；Docker Scout 均为 0 Critical / 0 High |
| 最终发布供应链协议 | PASS（协议）/ PENDING（真实执行） | `v2.0.0` tag 将重跑 backend/frontend/E2E/Compose，四镜像 commit 候选均通过扫描、原始 SARIF 留存且最终 tag 不存在后才晋升；builder 签名报告绑定 tag/source/SLSA v1/SPDX/SARIF/scan，最终授权器独立复验并作为 FOUNDATION；真实 tag、registry digest 和签名 bundle 尚未产生 |
| 目标证据组装协议 | PASS（协议）/ PENDING（真实输入） | 组装器从 release provenance 和九份目标 wrapper 自动投影审批空底稿，approval policy 仅能从独立 CLI 参数选择，落盘前后均要求 `APPROVAL_COLLECTION` 且 foundation/evidence 无失败；真实目标证据仍未产生 |
| 全局组织信任拓扑 | PASS（协议）/ PENDING（真实配置） | 九策略 manifest 和独立预检要求每个组织职责的 identity/公钥全局唯一；最终授权器从证据引用策略再次重算，跨策略复用停在 `FOUNDATION`；发布机构尚未提供真实策略、人员身份和公钥 |
| 四方签字活动协议 | PASS（协议）/ PENDING（真人执行） | 发布机构先以不可覆盖、限时 freeze 绑定空 approvals base、policy 与 release digest；每位审批人重跑完整 preflight 并签署同一 campaign/freeze，finalizer 拒绝跨轮混签、过期与窗口外签字，四份 entry 只有让持久化文件达到 `GA_AUTHORIZED` 才能输出；真实 Product/Architecture/Security/Operations 决策仍未发生 |
| 授权归档与独立复验协议 | PASS（协议）/ PENDING（真实 bundle） | manifest v2 记录原始路径到内容寻址成员的完整索引；只在当前时间和 bounded canonical time 均为 `GA_AUTHORIZED` 时生成确定性不可覆盖 tar.gz/manifest/SHA-256；接收方以外部摘要、严格无主机回退路径重映射复验全部成员/签名/授权；真实 bundle/digest 尚未产生和外部发布 |
| 本地生产基础设施 | PASS | 隔离 Compose 10/10 healthy；TLS 1.2/1.3，拒绝 1.0/1.1；hostname/HSTS/告警 firing+resolved 通过，随后零残留清理 |
| 容量工程基线 | PASS | 60 rps×900s + 120 rps×60s，61,200 Run/Audit/Outbox；持续 p95 8.023 ms，增长后 timeline p95 5.187 ms |
| 本地 Kubernetes HA 演练 | PASS (local reference) | kind 1 control-plane + 3 zone workers；三类 3 副本、Beat 1；整区 taint/drain 后 30 秒恢复，7 个连续 health/API 样本 0 失败，故障域回归后各 ReplicaSet 恢复三域覆盖；状态服务/RWX/CNI 未授权 |
| GA 生产授权 | BLOCKED | 真实目标 HTTPS 容量/HA/异地恢复/告警回执、独立安全评估与四方签名尚未提供，授权器必须拒绝 |
| Compose/CI | PASS | dev/prod Compose config clean；CI action 固定 commit SHA；最终 tag 等完整四类 job 后才推镜像；生产静态基线 26/26 PASS |

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

1. **独立安全审计尚未由第三方执行。** 仓库现提供 `collect_ga_independent_security.py` 和 v2 门禁：组织发布策略预先固定评估机构 identity/公钥，评估方签署逐条 finding 的原始 JSON 并绑定最终 PDF，门禁重算严重度统计且拒绝自选信任根、隐藏 High、投影篡改或签名后改 PDF。但仍没有与最终 target/contract/commit/镜像摘要绑定的真实第三方渗透测试或审计报告。
2. **目标 TLS 与网络隔离尚未取得真实回执。** TLS 已升级为 v3 双层证据：发布机构策略固定外部 probe identity/key、probe/vantage ID 与全球可路由来源 CIDR，探测者签署含证书指纹、有效期、HSTS header 和旧协议 OpenSSL 原始输出的报告，组合器与最终门禁重新验签并重算；它能拒绝 wrapper 投影篡改、签名后改报告、伪造 legacy 摘要和未批准来源，但尚未从真实外部探测点运行。网络也已升级为 v3 双层证据：发布机构策略额外固定精确 kube context、Namespace 与 CNI，外部执行者签署含 1–65535 TCP、三个数据端口 nmap 原始 XML、CNI/Namespace/Pod/NetworkPolicy 身份和 ingress/egress 正反例的报告；组合器和最终门禁重新验签、验证来源/cluster 身份并重算原始字段，能拒绝签名后篡改、未批准观测点、wrapper 投影和伪造扫描摘要。但该执行器同样尚未在真实目标网络/CNI 上运行。
3. **HA 已完成本机真实 Kubernetes 无状态演练，但未完成目标环境承诺。** `ops/kubernetes/ha` 的受限镜像、PDB/HPA、严格且 taint-aware/revision-aware 的拓扑分散已经在三模拟 zone 中执行节点/整区 drain、Beat 迁移与恢复后再均衡；仓库现提供 fail-closed 的目标 v2 执行器，可校验精确 context/镜像、整区节点、持续公网 HTTPS、自动清理和网络证据。托管 MySQL/Redis/S3/RWX 也已升级为多方 v2：发布策略固定 provider/verifier 身份与公钥，provider 签署自动跨域切换事件，独立 verifier 签署故障前后数据摘要与写后读结果，Operations 只签署组合 wrapper，最终门禁重验三层签名；但尚未在真实目标集群和服务商上运行。本地单节点 MySQL/Redis/MinIO、`emptyDir` 和未证明 enforcement 的 kindnet 不代表托管 MySQL/Redis/S3/RWX/CNI。
4. **目标容量尚未证明。** 本地真实 MySQL 工程基线通过，300 rps 边界探针也能 fail closed；仓库现提供容量 v3 闭环：负载执行人签署 G2 原始报告，存储观察人签署压测前后 MySQL 行数/bytes/outbox/lag，独立清理验证人签署 Namespace 删除、凭证撤销与残留归零，组合器和最终门禁重算三份原始证据。它能拒绝只靠 HTTP 201、篡改汇总、合法重签但增长不足或清理不完整的报告，但尚未在真实目标 HTTPS、负载均衡器和托管数据服务上执行。
5. **目标运维回执缺失。** 仓库现提供主动 Alertmanager firing→人工 ack→resolved 的 v2 采集器，并要求 delivery 服务与值班人员用不同身份签署三份原始回执；Secret 轮换也已升级为 metadata-only v2 采集器，要求 provider 与独立 verifier 分别签署轮换/停用和旧拒绝/新可用回执，并重算 Secret resourceVersion、Deployment generation 与 Pod UID 全量替换。异地恢复已升级为 v2：存储服务、恢复执行人、独立验证人分别签署 Object Lock/对象版本、恢复阶段结果与 MySQL/对象/Git/服务验证，门禁重算 RPO/RTO。但这些执行器尚未在真实通知供应商、on-call schedule、生产 Secret Manager、异地不可变备份介质和非生产恢复目标上运行。
6. **随 Compose 打包的第三方数据镜像不属于 GA 路径。** 本地扫描发现 MySQL/MinIO 官方镜像仍含 Critical/High；单机 Compose 仅作加固参考，GA 强制使用经独立评估的外部 HA MySQL/Redis/S3。Prometheus 的 1 个 High 需要独立评估/VEX 确认，项目不得自行豁免。
7. **类型债务仍有 81 项。** ratchet 阻止恶化，但后续版本应持续清零。
8. **外部 Provider 兼容性是持续门禁。** Langfuse、Hermes、OTel Collector 或其他 Harness 升级后必须重跑对应 compatibility gate。
9. **真实组织信任拓扑尚未建立。** 仓库预检和最终门禁已能拒绝九份策略之间复用 identity 或 OpenSSH 公钥，但真实发布机构仍需确定审批人、评估方、builder、probe、provider、on-call、执行人与 verifier，建立九份内容寻址 policy/trust store 并生成不可覆盖的 topology PASS 回执；本地测试身份不能代替该职责分配。
10. **RC 不是 GA。** 仓库已新增最终供应链 FOUNDATION：只有受控 `ci.yml@refs/tags/v2.0.0` 在 backend/frontend/E2E/Compose 全通过后才推送 commit 候选，四镜像均通过 Critical/High 扫描且最终 tag 从未存在后才晋升；独立 builder 策略和 OpenSSH 签名报告绑定 tag object/验证输出、source archive/tree、SLSA v1、SPDX 2.3、漏洞扫描和两个 `@sha256` 镜像，门禁会重读全部内容并拒绝跨镜像、predicate 修改、伪造统计或 builder/审批人公钥复用。但真实签名 tag、最终 registry digest 和 bundle 尚未产生。权威授权器仍严格分为 FOUNDATION、EVIDENCE_COLLECTION、APPROVAL_COLLECTION 和 AUTHORIZED；只有 `--require-evidence-ready` 证明全部非审批检查通过后才能收集四方签名，最终仍必须取得 `GA_AUTHORIZED`。

## 6. GA 前必须完成

- 在任何真实目标压测、恢复或故障注入前，发布机构先定稿九份 trust policy/allowed-signers，填写 `trust-topology-manifest.example.json` 的精确路径与摘要并运行 `verify_ga_trust_topology.py`，只有不可覆盖的 PASS 回执才允许继续；
- 用真实生产密钥、域名和 TLS 部署 `ops/kubernetes/ha` 目标 overlay，替换所有占位镜像/域名/egress；
- 从发布机构批准的公网 probe/vantage 运行 `probe_ga_target_tls.py`，由批准身份签署 v3 原始报告，再用 `collect_ga_target_tls.py` 生成最终 TLS evidence v3；
- 从目标集群外运行 `collect_ga_target_network.py`，证明仅 443 公网开放、数据服务直连端口不可达，并实际执行受信/非受信 ingress 和批准/拒绝 egress；
- 在真实目标 HTTPS 上执行 G2，由三种互斥身份签署负载、MySQL 数据增长和清理回执，再运行 `collect_ga_target_capacity.py` 取得 v3 容量证据；同时用 `collect_ga_target_ha.py` 完成节点/zone/Beat/托管数据服务故障注入；
- 用 `collect_ga_target_recovery.py` 从异地、加密、至少 30 天 Object Lock 的介质做破坏性 staging 恢复，取得存储/执行/独立验证三类签名回执；
- 用 `collect_ga_target_secrets.py` 采集生产 Secret Manager 轮换，取得 provider/verifier 不同密钥的原始签名回执，并证明 backend/worker/beat 全量滚动；
- 用 `collect_ga_target_alerting.py` 触发和恢复目标告警，由 delivery 服务签署投递回执、实际值班人员签署 ack，并证明目标接收人与 on-call schedule；
- 由组织策略预授权的第三方使用 `security-assessment-report.example.json` 交付并签署原始报告，再运行 `collect_ga_independent_security.py`，关闭最终应用镜像和依赖中的全部 Critical/High；
- 创建 GitHub 验证通过的签名 annotated `v2.0.0` tag，让受控 CI 在最终 commit 上重跑 backend/frontend/E2E/Compose 并推送带 BuildKit attestation 的 GHCR 镜像；按发布 provenance 策略由独立 builder 签署 tag/source/SLSA/SPDX/scan 原始报告，运行 `collect_ga_release_provenance.py`，将输出绑定到授权文件 `release.provenance`；
- 用 `preapproval-assembly-request.example.json` 只声明最终 release/target 和九份证据路径，通过独立 `--approval-policy` 运行 `assemble_ga_preapproval_authorization.py`；仅接受其落盘重验后的 `APPROVAL_COLLECTION` 空审批底稿和 assembly receipt，禁止手工投影 control 字段；
- 由发布机构通过受控、内容寻址的组织策略固定共享信任库和角色身份；在四方签字前运行生产授权器 `--require-evidence-ready`，确认 `campaign_stage=APPROVAL_COLLECTION`、`evidence_ready_for_approval=true` 且 foundation/evidence 失败列表为空；
- 运行 `freeze_ga_approval_campaign.py` 生成同一份不可覆盖、限时 campaign freeze，再由 Product、Architecture、Security、Operations 四个不同身份在窗口内签署同一 release/target/evidence/policy/campaign 摘要；
- 用 finalizer 组装并重验，运行生产授权器取得唯一可接受结果 `GA_AUTHORIZED`，随后生成可搬运授权归档并通过独立渠道发布其摘要。

机器级详细结果见 [`release-candidate-rc1-20260805.md`](../specs/015-ga-candidate/evidence/release-candidate-rc1-20260805.md)、[`capacity-reference-small-pass-20260805.json`](../specs/016-ga-production-authorization/evidence/capacity-reference-small-pass-20260805.json)、[`local-kubernetes-ha-rehearsal-20260805.json`](../specs/016-ga-production-authorization/evidence/local-kubernetes-ha-rehearsal-20260805.json) 和 [`local-infrastructure-20260805.json`](../specs/016-ga-production-authorization/evidence/local-infrastructure-20260805.json)。目标授权流程见 [`ga-production-authorization.zh-CN.md`](ga-production-authorization.zh-CN.md)。历史 `2.0.0-ga` 文档不再作为当前版本事实源。
