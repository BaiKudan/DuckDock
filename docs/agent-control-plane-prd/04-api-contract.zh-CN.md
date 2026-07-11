# 04. API 合约

## 1. API 原则

- REST API 前缀：`/api/v1`
- JSON 请求响应。
- 认证：JWT access token + refresh token，企业版支持 OIDC/LDAP。
- 所有写操作必须记录审计日志。
- 所有异步任务返回 `job_id`。
- 所有跨厂商执行动作必须支持幂等键。
- OpenAPI 文档必须由 FastAPI 自动生成，并作为前端客户端生成源。

## 2. 通用响应

```json
{
  "data": {},
  "request_id": "req_01J...",
  "meta": {}
}
```

分页响应：

```json
{
  "items": [],
  "page": 1,
  "page_size": 20,
  "total": 128
}
```

错误响应：

```json
{
  "error": {
    "code": "HANDOVER_APPROVAL_REQUIRED",
    "message": "The handover case requires approval before execution.",
    "details": {}
  },
  "request_id": "req_01J..."
}
```

## 3. 权限模型

| 权限 | 说明 |
|---|---|
| `runtime:read` | 查看运行时实例 |
| `runtime:write` | 新增/修改运行时连接 |
| `asset:read` | 查看资产目录 |
| `asset:write` | 修改资产元数据 |
| `asset:sensitive_read` | 查看敏感资产详情 |
| `worktrace:read` | 查看工作历程摘要 |
| `worktrace:content_read` | 查看完整会话/产物内容 |
| `handover:read` | 查看交接单 |
| `handover:create` | 创建交接单 |
| `handover:approve` | 审批交接 |
| `handover:execute` | 执行交接动作 |
| `probe:run` | 触发探针采集 |
| `evidence:read` | 查看证据摘要 |
| `evidence:sensitive_read` | 查看敏感证据 |
| `audit:read` | 查看审计日志 |

## 4. Runtime API

### 4.1 创建运行时

`POST /api/v1/runtimes`

```json
{
  "provider": "openclaw",
  "name": "客户A OpenClaw 私有实例",
  "base_url": "https://openclaw.customer-a.local",
  "deploy_type": "private",
  "credential": {
    "type": "api_token",
    "token": "******"
  }
}
```

响应：

```json
{
  "id": "01J...",
  "provider": "openclaw",
  "name": "客户A OpenClaw 私有实例",
  "status": "active",
  "last_sync_at": null
}
```

### 4.2 测试连接

`POST /api/v1/runtimes/{runtime_id}/test`

响应：

```json
{
  "status": "ok",
  "provider_version": "openclaw-1.x",
  "capabilities": ["skills", "sessions", "artifacts", "backup"]
}
```

### 4.3 触发同步

`POST /api/v1/runtimes/{runtime_id}/sync`

```json
{
  "scope": {
    "users": ["user@example.com"],
    "asset_types": ["skill", "agent", "workflow", "scheduled_task"],
    "include_work_traces": true,
    "include_artifacts": true
  },
  "reason": "employee_offboarding"
}
```

响应：

```json
{
  "job_id": "01J...",
  "status": "pending"
}
```

## 5. Asset API

### 5.1 资产列表

`GET /api/v1/assets?asset_type=skill&provider=openclaw&owner_user_id=...&status=active`

响应字段：

```json
{
  "items": [
    {
      "id": "01J...",
      "asset_type": "skill",
      "name": "合同审查 Skill",
      "source_provider": "openclaw",
      "status": "active",
      "criticality": "high",
      "primary_owner": {
        "user_id": "01J...",
        "display_name": "张三"
      },
      "runtime_count": 2,
      "last_seen_at": "2026-05-18T10:00:00Z"
    }
  ],
  "page": 1,
  "page_size": 20,
  "total": 1
}
```

### 5.2 资产详情

`GET /api/v1/assets/{asset_id}`

必须返回：

- 基础信息。
- 归属信息。
- 运行时绑定。
- 最近工作历程。
- 风险项。
- 证据摘要。
- 交接历史。

### 5.3 更新归属

`POST /api/v1/assets/{asset_id}/ownership`

```json
{
  "owner_type": "maintainer",
  "user_id": "01J...",
  "is_primary": true,
  "reason": "项目交接确认"
}
```

## 6. WorkTrace API

### 6.1 工作历程列表

`GET /api/v1/worktraces?asset_id=...&actor_user_id=...&from=2026-01-01&to=2026-05-18`

### 6.2 工作历程详情

`GET /api/v1/worktraces/{trace_id}`

默认返回摘要和产物索引。完整 transcript 需要 `worktrace:content_read` 权限。

### 6.3 查看完整内容

`POST /api/v1/worktraces/{trace_id}/reveal`

```json
{
  "reason": "离职交接审批已通过，需要确认项目上下文",
  "approval_task_id": "01J..."
}
```

## 7. Handover API

### 7.1 创建交接单

`POST /api/v1/handovers`

```json
{
  "case_type": "employee_offboarding",
  "title": "张三离职 AI 资产交接",
  "subject_user_id": "01J...",
  "receiver_user_id": "01J...",
  "due_at": "2026-05-31T18:00:00+08:00",
  "runtime_ids": ["01J..."],
  "collection_scope": {
    "include_work_traces": true,
    "include_artifacts": true,
    "lookback_days": 180
  }
}
```

### 7.2 启动采集

`POST /api/v1/handovers/{case_id}/collect`

响应：

```json
{
  "job_id": "01J...",
  "status": "pending"
}
```

### 7.3 生成交接建议

`POST /api/v1/handovers/{case_id}/analyze`

响应：

```json
{
  "job_id": "01J...",
  "status": "pending"
}
```

### 7.4 提交审批

`POST /api/v1/handovers/{case_id}/submit`

```json
{
  "approval_chain": [
    {"approval_type": "manager", "approver_user_id": "01J..."},
    {"approval_type": "receiver", "approver_user_id": "01J..."},
    {"approval_type": "security", "approver_user_id": "01J..."}
  ]
}
```

### 7.5 审批

`POST /api/v1/handovers/{case_id}/approvals/{approval_id}/decide`

```json
{
  "decision": "approved",
  "comment": "确认交接给李四"
}
```

### 7.6 执行交接

`POST /api/v1/handovers/{case_id}/execute`

```json
{
  "idempotency_key": "handover-01J-execute-v1",
  "selected_item_ids": ["01J..."]
}
```

响应：

```json
{
  "job_id": "01J...",
  "status": "pending"
}
```

## 8. Evidence API

### 8.1 证据列表

`GET /api/v1/evidence?case_id=...&asset_id=...`

### 8.2 证据详情

`GET /api/v1/evidence/{evidence_id}`

敏感证据只返回摘要。原始对象下载必须使用短期签名 URL。

### 8.3 获取证据下载链接

`POST /api/v1/evidence/{evidence_id}/signed-url`

```json
{
  "reason": "安全管理员复核",
  "ttl_seconds": 300
}
```

## 9. Adapter API

### 9.1 Adapter 能力查询

`GET /api/v1/adapters/capabilities`

响应：

```json
{
  "openclaw": {
    "asset_sync": true,
    "worktrace_sync": true,
    "backup_create": true,
    "restore": "manual_or_provider_dependent"
  },
  "jvs": {
    "asset_sync": true,
    "worktrace_sync": true,
    "backup_create": false,
    "logical_package": true
  },
  "arkclaw": {
    "asset_sync": "provider_confirmation_required",
    "backup_create": "provider_confirmation_required"
  },
  "workbuddy": {
    "asset_sync": "browser_or_private_api",
    "backup_create": false
  }
}
```

## 10. 报表 API

- `GET /api/v1/reports/asset-overview`
- `GET /api/v1/reports/offboarding-risk`
- `GET /api/v1/reports/provider-distribution`
- `GET /api/v1/reports/orphan-assets`
- `GET /api/v1/reports/handover-sla`

