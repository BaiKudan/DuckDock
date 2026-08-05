# DuckDock 2.0 GA 生产授权

DuckDock 有两个刻意分离的门禁：

1. `/api/v2/operations/ga-readiness` 证明应用数据、API 合约、发布回执、身份、
   恢复和 SLO 闭环处于 `READY`；
2. `backend/scripts/verify_ga_production_authorization.py` 证明一个具体的 `2.0.0`
   commit、镜像摘要和目标环境已经通过部署、安全、容量、HA、值班以及四方授权。

只有第二个门禁输出 `GA_AUTHORIZED` 才能称为生产 GA。应用侧 `READY`、仓库
基线 `PASS` 或 RC 验证都不能替代它。

## 执行顺序

1. 从 `ops/ga/production-authorization.example.json` 复制目标环境文件。
2. 将两个应用镜像改为流水线输出的 `registry/repo@sha256:...`，版本必须为
   `2.0.0`，commit 必须为完整 40 位。
3. 运行 `python3 scripts/verify-production-baseline.py`，保留 JSON；在目标
   Kubernetes overlay 中替换镜像、域名以及宽泛 egress，并做 server dry-run。
4. 使用 `probe_ga_target_tls.py` 探测真实公网 application/object-store health URL。
5. 在专用 performance Namespace 创建限时 Reporter/User token，通过环境变量运行
   真实 HTTPS 容量门禁（token 不得写入命令行或报告）：

```bash
export DUCKDOCK_CAPACITY_REPORTER_TOKEN='<dedicated reporter token>'
export DUCKDOCK_CAPACITY_USER_TOKEN='<dedicated namespace member token>'
python backend/scripts/g2_target_capacity_gate.py \
  --base-url https://duckdock.example.com \
  --target-environment customer-production \
  --acknowledge-target-mutation customer-production \
  --namespace-id 123 \
  --output /secure/evidence/target-capacity.json
```

   它默认执行 900 秒持续写入、60 秒突发、50,000+ AgentRun 和增长后时间线查询。
   保留 DBA 存储增长证据后删除专用 Namespace 并撤销两个 token。生产授权门禁
   会解析报告 schema/target/base URL/transport/指标；本机 ASGI + MySQL 报告只能
   证明工程基线，不能替代真实目标 HTTPS 报告。
6. 先运行本地参考演练，确认发布镜像能在受限安全上下文启动、三类无状态服务
   正常跨域、节点 drain 时持续可用、Beat 能迁移且故障域返回后重新均衡：

```bash
bash scripts/rehearse-kubernetes-ha.sh
```

   该命令输出 `duckdock-kubernetes-ha-failover-v1`，但其 scope 固定为
   `local-rehearsal`，状态服务、RWX 与 NetworkPolicy enforcement 固定为 false，
   因而绝不能授权生产。若 Docker Desktop 只有约 8 GiB 内存，先暂停本地
   DuckDock/Langfuse 栈，演练结束后再恢复，避免宿主 OOM 污染结果。

   然后必须在目标生产集群执行同一类节点和 zone 故障注入。目标报告 scope
   必须为 `target-production`，绑定同一 target ID、commit、两个不可变镜像摘要；
   通过真实目标 HTTPS 连续探测，证明 backend/frontend/worker 和单逻辑 Beat
   恢复；验证故障域回归后新 ReplicaSet 重新覆盖全部目标域；实际执行目标 CNI
   隔离，并证明托管 MySQL/Redis/S3、RWX 故障切换与数据完整性。任何本地报告、
   仅有 YAML 的声明或与授权字段不一致的报告都会被内容级门禁拒绝。
   报告字段模板见 `ops/ga/high-availability-evidence.example.json`；模板中的 Pod
   条目只是结构示意，必须替换为目标集群实际快照，不能复制后自报通过。
7. 按 `ops/ga/secrets-evidence.example.json` 在目标环境实际轮换每类生产
   credential。验证旧 credential 被拒绝、工作负载重新加载、审计事件落盘，且
   证据只记录 secret 类别，绝不能包含 secret/token/password 值。
8. 按 `ops/ga/network-evidence.example.json` 从集群外执行 TCP 扫描，并在目标
   CNI 中实际执行默认拒绝 ingress、非白名单 egress 拒绝、白名单 egress 放行和
   数据服务外部不可达测试。配置文件或 server dry-run 本身不是运行证据。
9. 按 `ops/ga/recovery-evidence.example.json` 从异地、加密、不可变备份介质对
   非生产恢复目标进行破坏性恢复。验证备份签名、manifest 摘要、外部解密密钥、
   MySQL 行、对象和 Git 仓库，并记录 RPO/RTO；禁止覆盖生产数据。
10. 触发测试告警，由命名 on-call schedule 实际确认，再恢复告警。按
    `ops/ga/alerting-evidence.example.json` 保留三个不同且有时序的 firing、ack、
    resolved receipt。加载规则或仅送达 webhook 不能证明有人值守。
11. 委托与项目实现方独立的安全机构按指定范围执行渗透测试和人工代码审查，
    关闭并复测全部 Critical/High。按
    `ops/ga/independent-security-evidence.example.json` 记录独立性、五类必测范围、
    签名报告摘要，并绑定相同 target、contract、commit 和两个镜像摘要。评估方需
    用自己的 OpenSSH key 在 namespace `duckdock-security-assessment` 对报告原文件
    签名；门禁会读取 allowed-signers 和 `.sig` 实际验签，不接受布尔值自报验签。
12. 为每份证据填绝对或授权文件相对路径、SHA-256 与 UTC 时间。Secrets、网络、
    告警、恢复、HA 和独立安全报告内部 `observed_at` 必须与各自授权证据时间相同，
    且 `scope` 只能是 `target-production`。本地 dev/kind 回执不能转换成该 scope。
13. 先运行门禁取得 `release_digest`，四个不同负责人分别签署：

```bash
python backend/scripts/verify_ga_production_authorization.py \
  /secure/duckdock-2.0.0-authorization.json --allow-blocked

bash scripts/sign-ga-approval.sh \
  --digest <release_digest> \
  --key ~/.ssh/product-ga-approval \
  --output /secure/signatures/product.sig
```

签名采用 OpenSSH namespace `duckdock-ga`。`allowed_signers` 将身份绑定到公钥，
四个 approval 必须使用不同 identity，并在最新证据之后签署。任何证据内容
变化都会改变 release digest，使旧签名失效。

最终执行：

```bash
python backend/scripts/verify_ga_production_authorization.py \
  /secure/duckdock-2.0.0-authorization.json \
  --output /secure/duckdock-2.0.0-ga-authorization-result.json
```

退出码 `0` 且状态 `GA_AUTHORIZED` 才是正式生产授权；退出码 `2` 是证据或签字
阻断，退出码 `3` 是授权文件结构错误。授权结果、原文件、全部证据、签名和
allowed-signers 应作为同一个不可变发布包归档。
