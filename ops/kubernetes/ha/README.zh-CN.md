# DuckDock 跨故障域 HA 参考部署

该目录是 DuckDock 2.0 的 Kubernetes 高可用基线，不是可不经审阅直接上线的
通用 Helm chart。它明确实现了三个 backend、frontend、worker 副本，跨 zone
调度、PDB、HPA、滚动更新、默认拒绝 NetworkPolicy、非 root/只读根文件系统和
TLS Ingress。Celery Beat 保持一个逻辑实例并由 Deployment 自动重建，避免重复
调度；其恢复时间必须包含在目标环境的故障演练中。

目标环境必须提供：

- Kubernetes 1.27 或更高版本、至少两个故障域和可工作的 metrics-server；
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

必须按回执顺序逐阶段执行，不能把三份文件一次性 apply：

```bash
for phase in bootstrap migration applications; do
  kubectl apply --server-side --dry-run=server \
    -f "/secure/release/duckdock-ha-target/${phase}.yaml"
done
kubectl apply -f /secure/release/duckdock-ha-target/bootstrap.yaml
# 先独立确认 runtime/TLS Secret、Ingress/monitoring Namespace 标签及 RWX 已就绪。
kubectl apply -f /secure/release/duckdock-ha-target/migration.yaml
kubectl -n duckdock wait --for=condition=complete \
  job/duckdock-migrate-<commit前12位> --timeout=10m
kubectl apply -f /secure/release/duckdock-ha-target/applications.yaml
for deployment in backend frontend worker beat; do
  kubectl -n duckdock rollout status "deployment/${deployment}" --timeout=10m
done
```

`receipt.json` 的状态固定为 `PREPARED_NOT_AUTHORIZED`；即使本地验证成功，也仍把
发布镜像 provenance、server-side dry-run、Secret/TLS、状态服务、RWX 冗余和故障域
演练保留为外部待办。

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
