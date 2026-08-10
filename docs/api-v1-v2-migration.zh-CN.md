# DuckDock API v1 → v2 迁移策略

## 支持承诺

DuckDock v1 在整个 2.x 系列保持可运行，不设置尚未批准的 Sunset 日期。v1 响应会返回：

```http
Deprecation: true
X-DuckDock-API-Compatibility: v1-supported-through-2.x
Link: </api/v2>; rel="successor-version"
```

这些响应头表示新集成应优先选择 v2，不表示每个 v1 endpoint 已有一对一替代，也不授权删除 v1 数据或停止旧 Reporter。

## 迁移原则

1. 新治理资源使用 `/api/v2`：AgentSession/Run、Artifact/Telemetry refs、Fleet、Eval Hub、Package Registry、Release Control、Handover evidence、workload identity、Operations。
2. v1 继续承载既有 IAM、控制面管理、Reporter enrollment/heartbeat/structured report 和历史交接执行接口。
3. Reporter 双轨期间，以 credential-derived Namespace/Runtime identity 和 transactional Outbox 对账为真值，不按 URL 版本猜测归属。
4. `EXPECTED_LEGACY_ONLY` 是允许状态；`MISMATCH` 是未解释差异并阻塞 GA。
5. v2 只保存治理索引和摘要。原始 prompt/output/trace 仍在 Runtime、Langfuse 或对象存储中，不因迁移复制到业务库。

## 常用路径映射

| 旧路径/职责 | v2 目标 | 迁移说明 |
|---|---|---|
| v1 Reporter heartbeat / structured report | v2 typed AgentSession/AgentRun + reconciliation | v1 保留；新执行生命周期优先走 v2，管理摘要通过 Outbox 对账。 |
| v1 Deployment management | `/api/v2/deployments` 与 Fleet | v2 使用不可变 revision、Namespace RBAC 和 Runtime capability。 |
| v1 handover case/item/execution | v2 snapshot/obligation/acceptance/signed package | v1 执行记录保留；v2 证据绑定既有 case。 |
| v1 release gate summary | v2 Eval binding + Release Policy/Promotion/Receipt | 新发布必须绑定精确 PackageVersion、Deployment revision 和 Runtime receipt。 |
| v1 IAM login/SSO | v2 SCIM lifecycle/workload identity | 人类认证保持 v1；机器身份与目录生命周期使用 v2。 |

## 验证

```bash
backend/.venv/bin/python backend/scripts/export_openapi_v2.py --check
backend/.venv/bin/python backend/scripts/verify_ga_candidate_dev.py
```

任何 contract drift、v1/v2 mismatch、FAILED/过期 Outbox、BREACHED SLO 或失败恢复演练都会使 GA Readiness 变为 `BLOCKED`。
