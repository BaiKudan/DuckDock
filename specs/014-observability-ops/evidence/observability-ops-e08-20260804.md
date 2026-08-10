# E08 Observability / Operations 技术验收证据

> 日期：2026-08-04
> 范围：OPS-01～OPS-08
> 环境：Docker Compose、Prometheus 3.5.0、MySQL 8.4、MinIO、backend 8801、frontend 5174

## 结论

E08 已完成。Prometheus、Langfuse、OTel Collector 均保持可替换组件边界；DuckDock 只定义稳定的 content-safe 指标、SLO/incident API 和不可变恢复演练回执。运维控制台不依赖读取 Langfuse 内部数据库。

## 真实恢复演练

```bash
bash scripts/verify-operations-recovery.sh
```

真实结果：

- MySQL 临时库备份后删除、重建、恢复并独立校验 `3` 条 canary；
- MinIO object PUT、备份、删除、恢复、重新计算摘要并清理，校验 `1` 个 object；
- backup set digest `86e78e4482fc11f6e6cf8dd23f335de9b66973eae1bd59824a52b3ecc494fba9`；
- RecoveryDrill `odrl_94d377a74ef44833a3fd7944dc38e844` = `PASSED`；RPO `0s`，RTO `1s`；
- 初始 SLO 为 `DEGRADED`，唯一原因是当时 evidence ingest 与 policy decision 窗口没有流量；E09 真实流量将同一 profile 验证为 `HEALTHY`。

## Prometheus 与产品验证

- `http://127.0.0.1:9090/-/ready` = 200；`duckdock-api` target=`UP`；四条 alert rules 加载成功；
- revision `20260804_0062`；空库 upgrade、`alembic check`、空证据 `0062→0061→0062` 通过；有不可变运维证据的开发库 downgrade 按设计拒绝；
- Operations focused backend `4 passed`；frontend 运维页测试通过；最终 backend `1005 passed, 19 skipped, 3 warnings`，frontend `53 passed`；
- frontend lint `0 errors`、`9` 个既有 warnings；production build PASS；Ruff clean；
- 浏览器 `/operations` 显示 SLO、incident、Outbox、route metrics 与真实 recovery receipt，无 console error。

## 测试数据修复

经用户明确允许清理旧测试数据，将 `55` 个历史 `@e2e.duckdock.local` 测试邮箱精确迁移到 RFC-valid 的 `@e2e.duckdock.example.com`，并同步修正 seed 脚本。未删除业务关系；IAM users API 恢复 200，旧域名剩余 `0`。

## 判定

OPS-01～OPS-08 全部通过，E08 Operations lane 关闭。
