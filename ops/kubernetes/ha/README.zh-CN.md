# DuckDock 跨故障域 HA 参考部署

该目录是 DuckDock 2.0 的 Kubernetes 高可用基线，不是可不经审阅直接上线的
通用 Helm chart。它明确实现了三个 backend、frontend、worker 副本，跨 zone
调度、PDB、HPA、滚动更新、默认拒绝 NetworkPolicy、非 root/只读根文件系统和
TLS Ingress。Celery Beat 保持一个逻辑实例并由 Deployment 自动重建，避免重复
调度；其恢复时间必须包含在目标环境的故障演练中。

目标环境必须提供：

- Kubernetes 1.27 或更高版本、至少三个 Ready 可调度节点分布在三个 zone，且
  metrics-server 能报告全部节点；
- 多可用区托管 MySQL、Redis、S3 兼容对象存储；
- 名为 `duckdock-repos-rwx` 的跨节点 RWX 持久卷，且存储本身具备冗余；
- 名为 `duckdock-runtime-secrets` 的 Secret，由 External Secrets、SOPS 或同等
  Secret Manager 注入，至少包含应用生产环境变量；
- 名为 `duckdock-ingress-tls` 的证书 Secret；
- Ingress Controller 所在 Namespace 标签 `duckdock.io/ingress=true`；
- Prometheus 所在 Namespace 标签 `duckdock.io/monitoring=true`；
- 将 `controlled-external-egress` 的 `0.0.0.0/0` 替换为批准的目标 CIDR，或用
  Cilium 等 FQDN egress policy 取代。未收紧不得通过 GA 门禁。

不要直接编辑并整体应用参考清单。使用目标包生成器把干净 Git commit、发布流水线
产生的两个不可变 `@sha256` 镜像、真实域名、外部 Secret、RWX StorageClass 与
批准的出口 CIDR 固化成内容寻址的三阶段部署包。输出目录必须位于仓库外且不能
预先存在：

```bash
backend/.venv/bin/python scripts/prepare-kubernetes-ha-target.py prepare \
  --output-dir /secure/release/duckdock-ha-target \
  --backend-image 'registry.company.cn/duckdock/backend@sha256:<64-hex-digest>' \
  --frontend-image 'registry.company.cn/duckdock/frontend@sha256:<64-hex-digest>' \
  --public-host duckdock.company.cn \
  --rwx-storage-class cephfs-rwx \
  --egress-cidr 10.20.0.0/16 \
  --egress-cidr 172.20.0.0/16

backend/.venv/bin/python scripts/prepare-kubernetes-ha-target.py verify \
  --bundle-dir /secure/release/duckdock-ha-target
```

生成器会拒绝脏工作树、可变/占位镜像、保留域名、`0.0.0.0/0` 等过宽出口、
小于 10Gi 的 RWX 卷、目录符号链接、额外文件或回执篡改。它生成：

1. `bootstrap.yaml`：Namespace、ServiceAccount、RWX PVC 和 NetworkPolicy；
2. `migration.yaml`：名称绑定 commit 的唯一 Alembic Job；
3. `applications.yaml`：四个 Deployment、内部 Service、TLS Ingress、PDB 和 HPA。

目标 Namespace 必须先由集群管理员建立，并带 `restricted` 的 enforce/audit/warn
Pod Security 标签；Runtime/TLS Secret 必须由 Secret Manager 预先注入。部署身份应是
变更窗口专用身份，只允许在该 Namespace 创建/patch 应用资源、读取两个精确 Secret
的元数据，并明确不能修改 Secret、RBAC、其他 Namespace 或删除工作负载/PVC。

先取得目标 `kube-system` UID 和 `kubectl auth whoami` principal，由变更单审阅者确认后
作为固定参数运行只读预检。回执目录必须预先使用 `0700` 权限建立，且不能位于仓库或
目标包目录内：

```bash
install -d -m 0700 /secure/evidence/duckdock-ha
backend/.venv/bin/python scripts/deploy-kubernetes-ha-target.py preflight \
  --bundle-dir /secure/release/duckdock-ha-target \
  --context company-prod \
  --expected-cluster-uid '<reviewed-kube-system-uid>' \
  --expected-principal '<reviewed-kubernetes-principal>' \
  --output /secure/evidence/duckdock-ha/preflight.json

backend/.venv/bin/python scripts/deploy-kubernetes-ha-target.py verify-preflight \
  --receipt /secure/evidence/duckdock-ha/preflight.json \
  --bundle-dir /secure/release/duckdock-ha-target
```

预检会重新验证 bundle，检查 Kubernetes 版本、三 zone、metrics-server、RWX
StorageClass、两个 Secret 的类型/键名、Ingress/monitoring Namespace 标签和固定
allow/deny RBAC 矩阵，并分别执行三份清单的真实 server-side dry-run。它不读取或记录
Secret 值，也不持久化任何 Kubernetes 资源。预检超过一小时后不得用于写入。

正式执行必须提供变更单和精确确认串：

```text
APPLY_DUCKDOCK_HA_TARGET:<context>:<namespace>:<bundle receipt_sha256>
```

```bash
backend/.venv/bin/python scripts/deploy-kubernetes-ha-target.py deploy \
  --bundle-dir /secure/release/duckdock-ha-target \
  --preflight-receipt /secure/evidence/duckdock-ha/preflight.json \
  --context company-prod \
  --expected-cluster-uid '<reviewed-kube-system-uid>' \
  --expected-principal '<reviewed-kubernetes-principal>' \
  --change-request-id CHG-20260807-001 \
  --confirm-target-mutation \
    'APPLY_DUCKDOCK_HA_TARGET:company-prod:duckdock:<bundle-receipt-sha256>' \
  --output /secure/evidence/duckdock-ha/deployment.json

backend/.venv/bin/python scripts/deploy-kubernetes-ha-target.py verify-deployment \
  --receipt /secure/evidence/duckdock-ha/deployment.json
```

执行器会在写入前重跑全部预检并拒绝任何 resourceVersion/身份/RBAC/拓扑漂移，随后
严格执行 bootstrap → PVC Bound → migration Complete → applications → 四个 rollout。
成功回执为 `TARGET_HA_DEPLOYMENT_COMPLETED_NOT_GA_AUTHORIZED`；一旦目标写入已开始，
任何失败都会写出 `TARGET_HA_DEPLOYMENT_INCOMPLETE_NOT_GA_AUTHORIZED`，列明已完成阶段，
不得自动重跑或删除，必须进入变更事故处置。执行器不会自动回滚数据库迁移。

`receipt.json` 的状态固定为 `PREPARED_NOT_AUTHORIZED`；即使本地验证成功，也仍把
发布镜像 provenance、server-side dry-run、Secret/TLS、状态服务、RWX 冗余和故障域
演练保留为外部待办。

部署成功回执仍不是 GA 证据：Secret 值/轮换、TLS 外部握手、NetworkPolicy 实际
enforcement、状态服务与 RWX 冗余、目标容量/增长、异地恢复/值班、故障域演练、独立
安全评估和四方签字都继续标记为 `PENDING_EXTERNAL`，由正式 campaign collectors
分别生成签名证据。

正式承诺必须用目标集群的真实报告证明：驱逐一个 backend/worker 节点和一个
zone 后 API 仍满足 SLO，Beat 在约定 RTO 内恢复，MySQL/Redis/S3/RWX 存储均未
丢数据。仓库模板本身不能替代这次演练。

先从真实集群外视角运行 `backend/scripts/collect_ga_target_network.py`，取得包含全
TCP 扫描、私有数据端口、CNI/NetworkPolicy 快照及 ingress/egress 正反向探针的
由发布机构策略约束且外部探测者签名的 `duckdock-ga-network-evidence-v3`。目标 HA 执行器为
`backend/scripts/collect_ga_target_ha.py`；它需要显式 target disruption 确认、精确
kube context、该 v3 网络报告，以及 provider 与独立 verifier 分别签署、最后由组织
Operations 身份确认的四类状态服务 v2 故障切换报告，并输出 v2 快照。完整参数、
探针前置条件和签名流程见
`docs/ga-production-authorization.zh-CN.md`。

跨区约束同时使用 `nodeTaintsPolicy: Honor` 和
`matchLabelKeys: [pod-template-hash]`。故障处置必须先给失效节点或失效区节点加
`duckdock.io/fault-domain-unavailable=true:NoSchedule` taint，再执行 drain；否则
调度器会把不可用区计入严格 skew，阻止副本在剩余故障域补齐。恢复时先移除
taint、uncordon，再滚动重启，门禁必须确认每个新 ReplicaSet 重新覆盖全部目标
故障域。

本机可用下列三工作节点演练验证清单和无状态故障恢复，但它不验证托管状态
服务、RWX、目标 CNI 或公网入口，因此不能用于正式授权：

```bash
bash scripts/rehearse-kubernetes-ha.sh
```

详见 `../ha-rehearsal/README.zh-CN.md`。
