# GA 授权文件

`production-authorization.example.json` 是 DuckDock 2.0 正式生产授权模板。
复制到受控证据目录后填写，禁止把真实人员身份、内部报告路径、签名或
allowed-signers 误提交到公开仓库。

`approval-policy.example.json` 是独立的组织信任根模板。它必须由发布机构而非
任一审批者通过只读控制路径提供，绑定唯一共享 allowed-signers 文件、四个审批角色
以及批准的外部安全评估机构/identity。内部审批人与外部评估人的 identity、公钥均
不得复用。授权文件只保存 policy ID 和 SHA-256；个人 approval 和安全证据都不允许
覆盖信任库。正式验证必须使用 `--approval-policy` 显式选择受控策略文件。

在收集任何四方签字前，必须使用同一个权威授权器执行预签字门禁：

```bash
python backend/scripts/verify_ga_production_authorization.py \
  /secure/duckdock-2.0.0-authorization.json \
  --approval-policy /release-authority/duckdock-ga-approval-policy.json \
  --require-evidence-ready
```

只有 `campaign_stage=APPROVAL_COLLECTION` 且
`evidence_ready_for_approval=true` 才会退出 0。`FOUNDATION` 或
`EVIDENCE_COLLECTION` 均表示当前 release digest 禁止签字；`--allow-blocked` 只用于
查看诊断，不是发布流水线成功条件。

结构检查：

```bash
python backend/scripts/verify_ga_production_authorization.py \
  ops/ga/production-authorization.example.json --lint
```

完整目标文件必须通过内容摘要、证据新鲜度和每类目标报告的内容级解析，不能用
无关 JSON 配合表层声明通过。Application readiness 必须由
`backend/scripts/collect_ga_target_readiness.py` 通过目标 HTTPS 采集；TLS、网络、容量
和 HA 使用各自工具输出。以下模板定义了其余必须由目标执行结果填充的版本化协议：

- `tls-trust-policy.example.json`：发布机构批准的外部 TLS probe/operator/vantage/
  全球可路由来源 CIDR 与精确签名身份；`probe_ga_target_tls.py` 生成带证书指纹和
  OpenSSL 原始输出的 v3 报告，外部执行人签名后由 `collect_ga_target_tls.py` 组合成
  最终 `duckdock-ga-tls-evidence-v3`，禁止直接手填 wrapper；
- `secrets-evidence.example.json`：`collect_ga_target_secrets.py` 输出的目标轮换 v2
  结构；只采集 Secret/Deployment/Pod 元数据，绑定 provider 与独立 verifier 的两份
  原始 OpenSSH 签名回执，禁止手填；
- `secrets-trust-policy.example.json`：批准的 Secret Manager、provider/verifier 精确
  身份、必测 secret 类别和工作负载；两类身份与公钥不得复用；
- `secret-rotation-receipt.example.json`、`secret-verification-receipt.example.json`：
  provider 证明版本轮换/停用/审计，独立 verifier 证明旧版本拒绝和新版本可用；
  只能记录 opaque version/receipt/audit ID，绝不能记录凭证值；
- `network-trust-policy.example.json`：发布机构批准的外部 network probe signer/key、
  probe/vantage、全球可路由来源 CIDR，以及精确 kube context/Namespace/CNI；
- `network-evidence.example.json`：`collect_ga_target_network.py` 输出并由外部执行人签名
  的原始 v3 结构示例；`collect_ga_target_network_evidence.py` 验签并组合最终 network
  evidence v3。原始报告保留 nmap、CNI、NetworkPolicy 与 ingress/egress 正反例，
  禁止手填 wrapper；
- `capacity-trust-policy.example.json`：托管 MySQL provider、必查计数器、数据增长/
  pending outbox/replica lag 阈值，以及负载、存储、清理三种互斥精确身份的信任策略；
- `capacity-growth-receipt.example.json`、`capacity-cleanup-receipt.example.json`：
  存储观察人签署压测前后真实 MySQL 行数/bytes/lag，独立清理验证人签署 Namespace
  删除、凭证撤销与 exercise 残留归零；`collect_ga_target_capacity.py` 会把它们与 G2
  原始 v2 负载报告及三份签名组合成不可手填的 `duckdock-target-capacity-gate-v3`；
- `alerting-evidence.example.json`：`collect_ga_target_alerting.py` 输出的主动演练 v2
  结构；绑定 Alertmanager active/inactive API 观测与三份原始签名回执；禁止手填；
- `alerting-trust-policy.example.json`：delivery 服务身份和命名 on-call schedule 的
  独立信任策略；对应 allowed-signers 禁止通配 principal 或公钥复用；
- `alert-delivery-receipt.example.json`、`oncall-acknowledgement.example.json`：
  通知集成与实际值班人员在演练期间分别写入并签名的精确原始协议；每个 delivery
  receipt 至少包含两个不同 channel/receiver/provider receipt；
- `recovery-evidence.example.json`：`collect_ga_target_recovery.py` 输出的 v2 异地
  不可变介质与非生产破坏性恢复报告；RPO/RTO 从签名时间线重算，禁止手填；
- `recovery-trust-policy.example.json`：存储服务、恢复执行人和独立验证人三类互斥
  身份及公钥的内容寻址策略，并固定三类 artifact/stage 与至少 30 天保留期；
- `backup-media-receipt.example.json`、`restore-execution-receipt.example.json`、
  `recovery-verification-receipt.example.json`：分别保留对象版本/Object Lock、实际
  恢复阶段 exit code/日志摘要，以及 MySQL/对象/Git/服务就绪的独立验证结果；
- `security-assessment-report.example.json`：由外部评估方填写并直接签名的原始 JSON；
  逐条 findings、重测时间和严重度统计，并内容寻址绑定最终 PDF；
- `independent-security-evidence.example.json`：
  `collect_ga_independent_security.py` 输出的 v2 wrapper；只信任组织 approval policy
  预授权的 provider/identity，门禁重读原始签名报告并重算统计；
- `high-availability-evidence.example.json`：目标多故障域故障注入。
- `state-services-trust-policy.example.json`：固定四类托管状态服务 provider、provider
  signer 与独立 verifier 的职责分离和独立公钥；两类身份/公钥还必须与审批和安全
  评估信任库完全分离；
- `state-services-provider-receipt.example.json`、
  `state-services-verification-receipt.example.json`：分别由基础设施提供方签署真实
  跨故障域切换事件，由独立验证人签署故障前后数据摘要及写后读结果；
  `collect_ga_state_services_ha.py` 验证两份签名并生成 v2 wrapper，再由 approval
  policy 中的 Operations 身份签署后交给目标 HA 演练。

所有最终目标报告以及 readiness v1、TLS/network evidence v3、容量 v3 报告都必须绑定
相同 target ID、source commit、backend/frontend 镜像摘要；最终 wrapper 的
`observed_at` 必须与授权文件的证据时间相同，内嵌签名 probe 的时间必须早于 wrapper
且不超过协议允许的五分钟。模板中的 PASS 值只
描述合格结构，不是可提交的证据，所有 `__CHANGE_ME` 和示例快照都必须替换为
实际回执。完整流程见
[`docs/ga-production-authorization.zh-CN.md`](../../docs/ga-production-authorization.zh-CN.md)。
