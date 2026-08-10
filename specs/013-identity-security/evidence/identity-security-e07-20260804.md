# E07 Identity / Security 技术验收证据

> 日期：2026-08-04
> 范围：IAM-01～IAM-09
> 环境：本机 Docker Compose、MySQL 8.4、真实 Hermes Runtime #9

## 结论

E07 已完成且与 Langfuse、具体 IdP 解耦。SCIM credential、目录 lifecycle、DEVICE/SERVICE workload identity、审计 Outbox 和 Ed25519 公钥轮换均由 DuckDock 自有的稳定接口与模型承载；上游 IdP 只负责提供标准生命周期事件。

## 可重复验证

```bash
docker compose exec -T -e PYTHONPATH=/app -e DEBUG=false \
  backend python scripts/verify_identity_security_dev.py \
  --hermes-models-url http://host.docker.internal:50070/v1/models
```

真实结果：

- Namespace `6` / Runtime `9`，Hermes 返回 `1` 个 OpenAI-compatible model identity；
- DEVICE workload identity `wid_0a9ad...`；停用前 heartbeat `200`；目录停用后用户登录 `401`、旧 credential heartbeat `401`；
- DirectoryLifecycleEvent `idevt_47840809033049f9aa55a1dfb7c1116e`；撤销 credential `1`、移除 membership `1`；
- 自动 HandoverCase `21`，receiver user `83`；
- successor signing key `pkey_72720...`，rotation sequence `2`，旧 key 保留验证链但已 revoke；
- 相同 external event replay 返回同一结果，冲突 payload fail closed。

## 实现与门禁

- revision `20260804_0061`；独立空库升级、`alembic check` 和空证据 downgrade/upgrade 通过；目录/轮换证据存在时 downgrade guard 拒绝；
- SCIM secret 与 workload token 仅返回一次，数据库只保存 hash；公钥 registry 从不接收私钥；
- Audit/Outbox 仅保存 public id、digest、typed count/reason，不保存姓名、邮箱、SCIM 原文或 token；
- Identity/Security focused lane `59 passed`；当时全量 backend `998 passed, 19 skipped, 3 warnings`；最终全量 `1005 passed, 19 skipped, 3 warnings`；
- `/iam` 真实浏览器展示 Identity Lifecycle、workload identities 与 key rotation，无 console error。

## 判定

IAM-01～IAM-09 和 HO-08 集成 seam 全部通过。E07 Identity/Security 技术 Gate 关闭。
