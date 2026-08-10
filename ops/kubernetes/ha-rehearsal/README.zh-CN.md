# DuckDock Kubernetes HA 本地演练

这里是 `../ha` 生产参考清单的可执行本地 overlay。它创建一个 control-plane、
三个 worker 的一次性 kind 集群；三个 worker 分别模拟 zone-a、zone-b、zone-c。

运行：

```bash
bash scripts/rehearse-kubernetes-ha.sh
```

可用参数：

```text
--output PATH   指定证据 JSON
--keep-cluster  演练结束后保留集群用于诊断
```

默认总会删除 kind 集群，且使用独立临时 `KUBECONFIG`，不会切换用户现有 kubectl
上下文。脚本会构建当前 backend/frontend 生产镜像、部署受限安全上下文、运行
Alembic 迁移和集群内健康/API 代理探针，然后执行以下序列：

1. 确认 backend/frontend/worker 各 3 副本分布在三个模拟 zone，Beat 为 1；
2. 给承载 Beat 的 worker 添加故障 taint 并 drain；
3. 每秒持续验证 frontend health 与未经认证 API 的预期 401；
4. 确认三类无状态副本在剩余两个 zone 补齐，Beat 换节点恢复；
5. 移除 taint、uncordon 并滚动重启，确认每个新 ReplicaSet 恢复三 zone 覆盖；
6. 写出 `duckdock-kubernetes-ha-failover-v1` 证据。

资源要求：Docker、kind、kubectl、openssl、Python 3，以及建议至少 6 GiB 可用的
Docker 内存。本机 Docker Desktop 总内存约 8 GiB 时，不要让完整
DuckDock/Langfuse dev 栈与四节点演练同时运行，否则 control-plane/CoreDNS 也
可能被宿主 OOM，结果没有诊断价值。

## 授权边界

本 overlay 为了可移植性使用单节点 MySQL/Redis/MinIO 和 `emptyDir` 仓库卷，
kindnet 也不作为目标 CNI enforcement 证明。因此报告固定包含：

- `scope: local-rehearsal`；
- `network_policy.enforcement_exercised: false`；
- 四项 `state_services` HA 值均为 false；
- `authorization_scope` 明确仅覆盖无状态 Kubernetes 调度/故障恢复。

GA 门禁会解析报告内容，拒绝把该 PASS 当作 `target-production`。目标报告还必须
绑定最终 target ID、commit 和不可变镜像摘要，通过真实 HTTPS 连续探测，执行
目标 CNI 隔离，并验证托管 MySQL/Redis/S3、RWX 存储的故障切换与数据完整性。

## Kubernetes nginx 差异

生产镜像内默认 nginx 配置使用 Docker 内嵌 DNS `127.0.0.11`。Kubernetes 不提供
该 resolver，因此本 overlay 挂载 `frontend-kubernetes.conf`，直接代理稳定的
`backend.duckdock.svc.cluster.local` Service。目标 Kubernetes overlay 也必须
采用 Kubernetes DNS 配置，不能原样复用 Docker resolver。
