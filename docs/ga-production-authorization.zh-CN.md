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

```bash
export DUCKDOCK_GA_SOURCE_COMMIT='40-character Git commit'
export DUCKDOCK_GA_BACKEND_IMAGE='registry.example.com/duckdock/backend@sha256:64-hex-digest'
export DUCKDOCK_GA_FRONTEND_IMAGE='registry.example.com/duckdock/frontend@sha256:64-hex-digest'
```

3. 运行 `python3 scripts/verify-production-baseline.py`，保留 JSON；在目标
   Kubernetes overlay 中替换镜像、域名以及宽泛 egress，并做 server dry-run。
   然后用独立、短期、专用管理员 token 通过真实目标 HTTPS 仅调用只读 readiness
   endpoint（token 只放环境变量，报告不会保留）：

```bash
export DUCKDOCK_GA_ADMIN_TOKEN='short-lived target validation token'
python backend/scripts/collect_ga_target_readiness.py \
  --base-url https://duckdock.example.com \
  --target-environment customer-production \
  --source-commit "$DUCKDOCK_GA_SOURCE_COMMIT" \
  --backend-image "$DUCKDOCK_GA_BACKEND_IMAGE" \
  --frontend-image "$DUCKDOCK_GA_FRONTEND_IMAGE" \
  --output /secure/evidence/target-readiness.json
unset DUCKDOCK_GA_ADMIN_TOKEN
```

   输出必须是 `duckdock-ga-target-readiness-v1`、`scope=target-production`、
   `transport=network HTTPS against target`，且 API 检查时间与证据采集时间相差不
   超过五分钟。裸 `/ga-readiness` 响应、本地 HTTP 报告或上一候选镜像的报告均
   不能授权生产。若 API 不是 `READY`，collector 仍保留 BLOCKED 报告并退出 2。
4. 使用 `probe_ga_target_tls.py` 探测真实公网 application/object-store health URL，
   并把报告绑定到当前 target、commit 和两个不可变镜像：

```bash
python backend/scripts/probe_ga_target_tls.py \
  --app-url https://duckdock.example.com/health \
  --object-store-url https://objects.example.com/minio/health/live \
  --target-environment customer-production \
  --source-commit "$DUCKDOCK_GA_SOURCE_COMMIT" \
  --backend-image "$DUCKDOCK_GA_BACKEND_IMAGE" \
  --frontend-image "$DUCKDOCK_GA_FRONTEND_IMAGE" \
  --output /secure/evidence/target-tls.json
```

   输出必须是 `duckdock-ga-tls-probe-v2`；旧 v1、local-validation 或绑定到其他
   候选版本的报告不能授权生产。
5. 在专用 performance Namespace 创建限时 Reporter/User token，通过环境变量运行
   真实 HTTPS 容量门禁（token 不得写入命令行或报告）：

```bash
export DUCKDOCK_CAPACITY_REPORTER_TOKEN='<dedicated reporter token>'
export DUCKDOCK_CAPACITY_USER_TOKEN='<dedicated namespace member token>'
python backend/scripts/g2_target_capacity_gate.py \
  --base-url https://duckdock.example.com \
  --target-environment customer-production \
  --acknowledge-target-mutation customer-production \
  --source-commit "$DUCKDOCK_GA_SOURCE_COMMIT" \
  --backend-image "$DUCKDOCK_GA_BACKEND_IMAGE" \
  --frontend-image "$DUCKDOCK_GA_FRONTEND_IMAGE" \
  --namespace-id 123 \
  --output /secure/evidence/target-capacity.json
```

   它输出 `duckdock-target-capacity-gate-v2`，默认执行 900 秒持续写入、60 秒突发、
   50,000+ AgentRun 和增长后时间线查询。
   保留 DBA 存储增长证据后删除专用 Namespace 并撤销两个 token。生产授权门禁
   会解析报告 schema/target/base URL/transport/指标，并逐项对照 commit 与镜像；
   本机 ASGI + MySQL 报告或上一候选版本的报告只能证明工程基线，不能替代当前
   release 的真实目标 HTTPS 报告。
6. 先运行本地参考演练，确认发布镜像能在受限安全上下文启动、三类无状态服务
   正常跨域、节点 drain 时持续可用、Beat 能迁移且故障域返回后重新均衡：

```bash
bash scripts/rehearse-kubernetes-ha.sh
```

   该命令输出 `duckdock-kubernetes-ha-failover-v1`，但其 scope 固定为
   `local-rehearsal`，状态服务、RWX 与 NetworkPolicy enforcement 固定为 false，
   因而绝不能授权生产。若 Docker Desktop 只有约 8 GiB 内存，先暂停本地
   DuckDock/Langfuse 栈，演练结束后再恢复，避免宿主 OOM 污染结果。

   目标生产演练必须使用 `collect_ga_target_ha.py`，不能手填 PASS 模板。先按后续
   网络步骤取得同一 release 的目标 CNI/外部扫描报告；再按
   `ops/ga/state-services-ha-evidence.example.json` 记录托管 MySQL、Redis、S3 和
   RWX 的真实 provider receipt、切换时段与数据完整性，由组织审批策略中的
   Operations 身份签署原文件：

```bash
ssh-keygen -Y sign \
  -f ~/.ssh/operations-ga-approval \
  -n duckdock-ha-state-services \
  /secure/evidence/state-services-ha.json
```

   在已批准的故障演练窗口运行以下命令。它会 taint 并 drain 所选 zone 的全部
   合格 worker 节点，属于有意的生产 disruption；确认值必须与 target ID 完全一致。
   命令先校验精确 kube context、RBAC、三类 3 副本、Beat 所在区和实际 Deployment
   镜像，故障期间从执行机持续探测公网 HTTPS，最后在 `finally` 中移除 taint、
   uncordon，并滚动再均衡返回的故障域：

```bash
python backend/scripts/collect_ga_target_ha.py \
  --context customer-production-admin \
  --namespace duckdock \
  --target-environment customer-production \
  --acknowledge-target-disruption customer-production \
  --source-commit "$DUCKDOCK_GA_SOURCE_COMMIT" \
  --backend-image "$DUCKDOCK_GA_BACKEND_IMAGE" \
  --frontend-image "$DUCKDOCK_GA_FRONTEND_IMAGE" \
  --base-url https://duckdock.example.com \
  --drain-zone cn-hangzhou-a \
  --network-evidence /secure/evidence/target-network.json \
  --state-services-evidence /secure/evidence/state-services-ha.json \
  --state-services-signature /secure/evidence/state-services-ha.json.sig \
  --state-services-signer-identity operations-release-approver@example.com \
  --approval-policy /release-authority/duckdock-ga-approval-policy.json \
  --output /secure/evidence/target-ha.json
```

   只有 `duckdock-kubernetes-ha-failover-v2` 可授权生产。报告保留 before/drain/
   rebalance Pod→node→zone 原始快照、全部 drained node、每次 HTTPS 状态码、cleanup
   结果，并内容寻址地绑定网络证据和 Operations 签名的状态服务回执。旧 v1、仅有
   YAML 的声明、手填 Pod 名称或清理不完整的报告都会被门禁拒绝。
7. 使用 `collect_ga_target_secrets.py` 在目标 Kubernetes 环境采集
   `duckdock-ga-secrets-evidence-v2`，不能手填 PASS 模板。先由发布机构从
   `ops/ga/secrets-trust-policy.example.json` 建立只读、内容寻址的信任策略；策略
   固定批准的 Secret Manager、`application-signing`/`database`/`object-store`
   三类 secret、`backend`/`worker`/`beat` 三个消费者，以及互不重叠的 provider
   和独立 verifier 精确身份。allowed-signers 禁止通配 principal 和公钥复用。

   采集器启动前四个 receipt/signature 路径必须不存在。它先通过 Kubernetes
   JSONPath 读取 `duckdock-runtime-secrets`、Deployment 和 Pod 的元数据；不会读取
   Secret `data`，也没有任何接收 credential 值的参数。外部 Secret Manager 完成
   每类版本轮换、停用旧版本和审计落盘后，写入
   `duckdock-ga-secret-rotation-receipt-v1`，再由 provider 服务身份签名。独立验证
   系统实际以旧版本和新版本做正反向认证探测，写入
   `duckdock-ga-secret-verification-receipt-v1`，证明旧版本拒绝、新版本可用，并由
   不同身份/密钥签名：

```bash
ssh-keygen -Y sign \
  -f /release-authority/secret-provider-key \
  -n duckdock-secret-rotation-receipt \
  /secure/evidence/secret-rotation.json

ssh-keygen -Y sign \
  -f /release-authority/secret-verifier-key \
  -n duckdock-secret-verification-receipt \
  /secure/evidence/secret-verification.json
```

   两份签名出现后，采集器等待 Secret `resourceVersion` 变化、三个 Deployment
   generation 前进且恢复全量 ready/updated/available，并证明 before/after Pod UID
   完全不重叠：

```bash
python backend/scripts/collect_ga_target_secrets.py \
  --context customer-production-admin \
  --namespace duckdock \
  --target-environment customer-production \
  --acknowledge-target-rotation customer-production \
  --source-commit "$DUCKDOCK_GA_SOURCE_COMMIT" \
  --backend-image "$DUCKDOCK_GA_BACKEND_IMAGE" \
  --frontend-image "$DUCKDOCK_GA_FRONTEND_IMAGE" \
  --provider "External Secrets" \
  --secret-name duckdock-runtime-secrets \
  --exercise-id ga-secrets-20260806 \
  --secrets-policy /release-authority/duckdock-secrets-policy.json \
  --provider-signer-identity secret-provider@example.com \
  --verifier-signer-identity secret-verifier@example.com \
  --rotation-receipt /secure/evidence/secret-rotation.json \
  --rotation-signature /secure/evidence/secret-rotation.json.sig \
  --verification-receipt /secure/evidence/secret-verification.json \
  --verification-signature /secure/evidence/secret-verification.json.sig \
  --output /secure/evidence/target-secrets.json
```

   生产授权器会重新读取策略、信任库、两份原始 JSON 和签名，重算 Kubernetes
   before/after 差异及时间线；旧 v1、自报布尔值、预先存在的 receipt、provider
   代替 verifier 签名、未滚动 Pod 或包含 secret/token/password 值都不能通过。
   opaque version/receipt/audit ID 只用于关联外部系统，不得放入任何 credential 值。
8. 使用 `collect_ga_target_network.py` 采集 `duckdock-ga-network-evidence-v2`，不能
   手填 PASS 模板。执行机必须位于目标网络之外，同时具备目标集群只读
   NetworkPolicy/CNI DaemonSet 与三类 probe Pod `exec` 权限，并已安装 `nmap`。
   预先准备三个不挂载生产 Secret、包含 Python 3 的 Ready probe Pod：入口受信
   Namespace 带 `duckdock.io/ingress=true`，监控受信 Namespace 带
   `duckdock.io/monitoring=true`，非受信 Namespace 不得带任一标签。另准备一个不在
   backend egress allowlist 中、可由非受信 probe 访问的 TCP 对照监听器；只有同一
   目标由对照组连通、backend 被拒绝，才证明拒绝来自策略而非目标宕机。

```bash
python backend/scripts/collect_ga_target_network.py \
  --context customer-production-admin \
  --namespace duckdock \
  --target-environment customer-production \
  --acknowledge-external-vantage customer-production \
  --source-commit "$DUCKDOCK_GA_SOURCE_COMMIT" \
  --backend-image "$DUCKDOCK_GA_BACKEND_IMAGE" \
  --frontend-image "$DUCKDOCK_GA_FRONTEND_IMAGE" \
  --base-url https://duckdock.example.com \
  --scanner-id external-scanner-hz-01 \
  --scanner-source-ip "$EXTERNAL_SCANNER_SOURCE_IP" \
  --database-address 10.20.1.10 \
  --redis-address 10.20.1.11 \
  --object-store-direct-address 10.20.1.12 \
  --trusted-probe-namespace duckdock-ingress-probes \
  --trusted-probe-pod network-probe \
  --monitoring-probe-namespace duckdock-monitoring-probes \
  --monitoring-probe-pod network-probe \
  --untrusted-probe-namespace duckdock-untrusted-probes \
  --untrusted-probe-pod network-probe \
  --approved-egress-host mysql.internal.example.com \
  --approved-egress-port 3306 \
  --unapproved-egress-host network-control.example.com \
  --unapproved-egress-port 443 \
  --cni-daemonset-name cilium \
  --output /secure/evidence/target-network.json
```

   执行器从外部对公网入口扫描全部 1–65535 TCP 端口，并分别探测 MySQL 3306、
   Redis 6379 和对象存储直连 9000；在集群内同时验证受信 ingress 放行、非受信
   ingress 拒绝、批准 egress 放行和同一对照目标的非批准 egress 拒绝。报告保留
   原始 nmap XML 及其摘要、CNI DaemonSet 不可变镜像、Namespace/Pod UID、完整
   NetworkPolicy spec 与 kubectl exit code。生产授权器会重新计算 spec 摘要并拒绝
   任意 `0.0.0.0/0`/`::/0` egress，即使汇总字段仍自报 PASS。该文件必须先于第 6
   步的目标 HA disruption 生成，并由 HA v2 报告内容寻址绑定。配置文件、server
   dry-run、本地 kind 回执或旧 v1 报告都不是目标运行证据。
9. 使用 `collect_ga_target_recovery.py` 从异地、加密、不可变备份介质对明确命名的
   非生产恢复目标做破坏性恢复，输出 `duckdock-ga-recovery-evidence-v2`。`backup`
   必须引用 `seal-backup.sh` 生成的原始 `manifest.json`、`.sig`、备份 signer identity
   与 allowed-signers；采集器和生产门禁都会在 namespace `duckdock-backup` 实际验签，
   解析 release commit、age 加密方式及三类 artifact digest/size。

   发布机构先从 `ops/ga/recovery-trust-policy.example.json` 建立只读、内容寻址的
   恢复策略，分别授权存储服务、恢复执行人和独立验证人。三类精确 identity 与公钥
   必须互不重合；策略固定 `minio/mysql/repos` 三个加密 artifact、
   `mysql/repositories/object-store` 三个恢复阶段和至少 30 天保留期。采集器启动前
   六个 receipt/signature 路径必须不存在：

   - 存储集成从供应商 API 取得对象 version ID、digest、size、Object Lock mode 与
     retained-until，写入 `duckdock-ga-backup-media-receipt-v1` 并由服务身份签名；
   - 恢复执行工作流只在已确认的 `recovery`/`staging` 环境执行，记录三个阶段的
     command ID、exit code 和脱敏日志 SHA-256，写入
     `duckdock-ga-restore-execution-receipt-v1` 并由执行人签名；
   - 与执行人独立的验证方核对 MySQL 行集摘要、对象清单摘要、Git refs 摘要、HTTPS
     与后台 worker 就绪，写入 `duckdock-ga-recovery-verification-receipt-v1` 并签名。

```bash
ssh-keygen -Y sign -f /release-authority/storage-key \
  -n duckdock-backup-media-receipt /secure/evidence/backup-media.json
ssh-keygen -Y sign -f /release-authority/restore-executor-key \
  -n duckdock-restore-execution-receipt /secure/evidence/restore-execution.json
ssh-keygen -Y sign -f /release-authority/recovery-verifier-key \
  -n duckdock-recovery-verification-receipt /secure/evidence/recovery-verification.json
```

```bash
python backend/scripts/collect_ga_target_recovery.py \
  --target-environment customer-production \
  --restore-target-environment customer-recovery-staging \
  --acknowledge-destructive-restore customer-recovery-staging \
  --source-commit "$DUCKDOCK_GA_SOURCE_COMMIT" \
  --backend-image "$DUCKDOCK_GA_BACKEND_IMAGE" \
  --frontend-image "$DUCKDOCK_GA_FRONTEND_IMAGE" \
  --exercise-id ga-recovery-20260806 \
  --maximum-rpo-seconds 900 \
  --maximum-rto-seconds 14400 \
  --storage-provider "AWS S3" \
  --backup-manifest /secure/evidence/manifest.json \
  --backup-signature /secure/evidence/manifest.json.sig \
  --backup-allowed-signers /release-authority/backup-allowed-signers \
  --backup-signer-identity backup-operator@example.com \
  --recovery-policy /release-authority/duckdock-recovery-policy.json \
  --storage-signer-identity backup-storage@example.com \
  --restore-signer-identity restore-executor@example.com \
  --verifier-signer-identity recovery-verifier@example.com \
  --media-receipt /secure/evidence/backup-media.json \
  --media-signature /secure/evidence/backup-media.json.sig \
  --restore-receipt /secure/evidence/restore-execution.json \
  --restore-signature /secure/evidence/restore-execution.json.sig \
  --verification-receipt /secure/evidence/recovery-verification.json \
  --verification-signature /secure/evidence/recovery-verification.json.sig \
  --output /secure/evidence/target-recovery.json
```

   采集器不接收解密密钥、数据库密码或对象存储 credential。它会核对异地对象摘要/
   大小与已签 manifest 完全一致，并从 `recovery_point_at → failure_injected_at →
   independent verification completed_at` 重算 RPO/RTO。旧 v1、手填 RPO/RTO、短于
   30 天的 Object Lock、生产/恢复 target 相同、角色/公钥复用、预先存在的 receipt、
   自报 `signature_verified` 或修改签名后内容都不能通过。
10. 使用 `collect_ga_target_alerting.py` 主动触发并恢复唯一的目标 Alertmanager
    告警。发布机构先从 `ops/ga/alerting-trust-policy.example.json` 建立独立、只读、
    内容寻址的告警信任策略：`delivery_identities` 是能从通知供应商取得真实投递回执
    的服务身份，`oncall_schedules` 将命名 schedule 映射到实际值班人员身份；两类
    身份及公钥不得重合，allowed-signers 禁止通配 principal。

    采集器运行期间，通知集成根据它输出的 `exercise_id`/`alert_name` 原子写入 firing
    与 resolved 的 `duckdock-ga-alert-delivery-receipt-v1` 文件；每份文件必须列出至少
    两个不同 channel、receiver 和 provider receipt ID，且 firing/resolved 覆盖完全
    相同的目标，再由 delivery 身份签名。实际值班人员确认告警后写入
    `duckdock-ga-oncall-acknowledgement-v1`，由本人签名。三个 receipt/signature 路径
    在演练开始前必须不存在，防止复用历史回执：

```bash
ssh-keygen -Y sign \
  -f /release-authority/alert-delivery-key \
  -n duckdock-alert-delivery-receipt \
  /secure/evidence/alert-firing.json

ssh-keygen -Y sign \
  -f ~/.ssh/oncall-ack-key \
  -n duckdock-oncall-acknowledgement \
  /secure/evidence/alert-ack.json
```

    firing 与人工 ack 签名出现后，采集器才会向同一 Alertmanager alert 写入 resolve；
    通知集成随后以相同 delivery 身份签署 resolved receipt。正式命令示例：

```bash
python backend/scripts/collect_ga_target_alerting.py \
  --target-environment customer-production \
  --acknowledge-oncall-exercise customer-production \
  --source-commit "$DUCKDOCK_GA_SOURCE_COMMIT" \
  --backend-image "$DUCKDOCK_GA_BACKEND_IMAGE" \
  --frontend-image "$DUCKDOCK_GA_FRONTEND_IMAGE" \
  --alertmanager-url https://alerts.example.com \
  --ca-file /release-authority/target-alertmanager-ca.pem \
  --bearer-token-file /run/secrets/alertmanager-probe-token \
  --exercise-id ga-20260806-001 \
  --oncall-schedule platform-primary \
  --alerting-policy /release-authority/duckdock-alerting-policy.json \
  --delivery-signer-identity alert-delivery@example.com \
  --oncall-signer-identity oncall-primary@example.com \
  --firing-receipt /secure/evidence/alert-firing.json \
  --firing-signature /secure/evidence/alert-firing.json.sig \
  --ack-receipt /secure/evidence/alert-ack.json \
  --ack-signature /secure/evidence/alert-ack.json.sig \
  --resolved-receipt /secure/evidence/alert-resolved.json \
  --resolved-signature /secure/evidence/alert-resolved.json.sig \
  --output /secure/evidence/target-alerting.json
```

    输出协议只能是 `duckdock-ga-alerting-evidence-v2`。生产授权器会重新读取策略和
    三份原始 JSON，核对摘要、双接收目标、精确身份角色、OpenSSH namespace，并
    重新解析受大小限制的 Alertmanager 原始 HTTP 请求/响应来验证 exact labels、
    startsAt/endsAt 与 active→inactive 匹配数。加载规则、仅送达无签名 webhook、预先存在
    的 receipt、自报 `signature_verified` 或 delivery 服务代替人签 ack 均不能通过。
    若任一阶段失败，采集器会尽力自动 resolve 已注入的告警；重试必须使用新的
    exercise ID 和全新的 receipt/signature 路径，不能复用失败演练留下的文件。
11. 委托与项目实现方独立的安全机构按指定范围执行渗透测试和人工代码审查，
    关闭并复测全部 Critical/High。按
    `ops/ga/independent-security-evidence.example.json` 记录独立性、五类必测范围、
    签名报告摘要，并绑定相同 target、contract、commit 和两个镜像摘要。评估方需
    用自己的 OpenSSH key 在 namespace `duckdock-security-assessment` 对报告原文件
    签名；门禁会读取 allowed-signers 和 `.sig` 实际验签，不接受布尔值自报验签。
12. 为每份证据填绝对或授权文件相对路径、SHA-256 与 UTC 时间。Readiness、TLS、
    Secrets、网络、告警、恢复、容量、HA 和独立安全报告内部 `observed_at` 必须与
    各自授权证据时间相同，且 `scope` 只能是 `target-production`。本地 dev/kind
    回执不能转换成该 scope。
13. 先由组织发布机构（不能是任一审批者临时自建）从
    `ops/ga/approval-policy.example.json` 建立受控审批策略。策略必须使用
    `duckdock-ga-approval-policy-v1`，为 Product、Architecture、Security、
    Operations 分配互不重叠的精确 identity，并引用同一份 OpenSSH
    allowed-signers。信任库不得使用通配 principal；其 SHA-256 写入策略，策略
    SHA-256 和 policy ID 再写入生产授权文件的 `approval_policy`。策略与信任库
    应由发布流水线/发布负责人以只读方式注入验证器，不能接受审批者随授权包提交
    的任意替代路径。例如：

```bash
shasum -a 256 /release-authority/duckdock-ga.allowed-signers
# 将摘要填入 /release-authority/duckdock-ga-approval-policy.json
shasum -a 256 /release-authority/duckdock-ga-approval-policy.json
# 将策略摘要和 policy_id 填入 authorization.json
```

14. 使用受控策略运行门禁取得 `release_digest`，四个不同负责人分别签署：

```bash
python backend/scripts/verify_ga_production_authorization.py \
  /secure/duckdock-2.0.0-authorization.json \
  --approval-policy /release-authority/duckdock-ga-approval-policy.json \
  --allow-blocked

bash scripts/sign-ga-approval.sh \
  --digest <release_digest> \
  --role Product \
  --identity product-release-approver@example.com \
  --approved-at 2026-08-06T10:30:00+08:00 \
  --key ~/.ssh/product-ga-approval \
  --output /secure/signatures/product.sig
```

签名采用 OpenSSH namespace `duckdock-ga`。每个 identity 必须属于组织策略中对应
角色，四个 approval 必须使用不同 identity，并在最新证据之后签署。v2 禁止在
approval 内提供 `allowed_signers_path`；所有签名只信任组织策略绑定的共享信任库。
规范化签名 statement 同时覆盖 release digest、role、identity、decision 和
approved_at，授权包组装者不能事后改写审批角色、决定或时间。任何证据、目标、
版本或审批策略摘要变化都会改变 release digest，使旧签名失效。

最终执行：

```bash
python backend/scripts/verify_ga_production_authorization.py \
  /secure/duckdock-2.0.0-authorization.json \
  --approval-policy /release-authority/duckdock-ga-approval-policy.json \
  --output /secure/duckdock-2.0.0-ga-authorization-result.json
```

退出码 `0` 且状态 `GA_AUTHORIZED` 才是正式生产授权；退出码 `2` 是证据或签字
阻断，退出码 `3` 是授权文件结构错误。授权结果、原文件、全部证据、签名和
审批策略、共享 allowed-signers 应与授权结果一同不可变归档，但生产验证时仍必须
从发布机构控制的独立只读路径选择策略，不能从待验证授权包自动发现信任根。
