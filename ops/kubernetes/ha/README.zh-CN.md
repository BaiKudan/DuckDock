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

部署前把 `kustomization.yaml` 中两个镜像改为发布流水线产生的不可变
`@sha256` 摘要，并把 Ingress 域名改为真实域名。每个发布先单独运行并等待
`duckdock-migrate` Job 成功，再应用 Deployment。验证命令：

```bash
kubectl kustomize ops/kubernetes/ha > /tmp/duckdock-ha.yaml
kubectl apply --server-side --dry-run=server -f /tmp/duckdock-ha.yaml
kubectl -n duckdock wait --for=condition=complete job/duckdock-migrate --timeout=10m
kubectl -n duckdock rollout status deployment/backend --timeout=10m
```

正式承诺必须用目标集群的真实报告证明：驱逐一个 backend/worker 节点和一个
zone 后 API 仍满足 SLO，Beat 在约定 RTO 内恢复，MySQL/Redis/S3/RWX 存储均未
丢数据。仓库模板本身不能替代这次演练。

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
