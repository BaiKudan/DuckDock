# 11. DuckDock Report Upload Session

## 1. 设计目标

OpenClaw 或其衍生实例不直接持有 MinIO/S3 密钥。实例只持有 DuckDock 颁发的运行时上报 Token，用它向 DuckDock 创建一次性上传会话。DuckDock 返回短期 presigned PUT URL，实例把 `duckdock-pack-v1.zip` 直传到 MinIO/S3，随后调用 finalize。DuckDock 校验对象、异步解析入库，并把结果落到控制平面资产、工作历程和证据链表。

## 2. 标准链路

```text
管理员
  -> 创建 Runtime Report Token

OpenClaw 定时任务
  -> POST /api/v1/reports/upload-sessions
  -> PUT duckdock-pack-v1.zip 到 upload_url
  -> POST /api/v1/reports/{report_id}/finalize

DuckDock
  -> 校验对象大小与 sha256
  -> Celery 解析 zip
  -> 写入 CollectionJob / AIAsset / WorkTrace / EvidenceItem / RawCollectionRecord
```

## 3. 管理员颁发 Token

```http
POST /api/v1/runtimes/{runtime_id}/report-tokens
Authorization: Bearer <admin_jwt>
Content-Type: application/json

{
  "name": "weekly-openclaw-reporter",
  "expires_at": null
}
```

返回的 `token` 只出现一次，应写入 OpenClaw 实例的安全配置：

```text
DUCKDOCK_API_BASE=https://duckdock.example.com/api/v1
DUCKDOCK_RUNTIME_ID=12
DUCKDOCK_REPORT_TOKEN=dkr_report_xxxx_xxxxxxxxx
```

## 4. 创建上传会话

```http
POST /api/v1/reports/upload-sessions
Authorization: Bearer <DUCKDOCK_REPORT_TOKEN>
Content-Type: application/json

{
  "runtime_id": 12,
  "schema_version": "duckdock-pack-v1",
  "report_type": "weekly",
  "period_start": "2026-05-15T00:00:00+08:00",
  "period_end": "2026-05-22T00:00:00+08:00",
  "filename": "duckdock-pack-v1.zip",
  "content_type": "application/zip",
  "expected_sha256": "optional-64-char-sha256",
  "expected_size_bytes": 123456,
  "idempotency_key": "openclaw-prod-001-2026w21"
}
```

返回：

```json
{
  "report_id": "rpt_20260521090000_ab12cd34ef56",
  "collection_job_id": 101,
  "status": "pending",
  "upload_url": "https://minio.example.com/duckdock/...",
  "object_key": "reports/runtime-12/2026/05/21/rpt_.../duckdock-pack-v1.zip",
  "upload_expires_at": "2026-05-21T09:15:00Z",
  "max_size_mb": 512
}
```

## 5. 直传 MinIO/S3

```bash
curl -X PUT "$UPLOAD_URL" \
  -H "Content-Type: application/zip" \
  --data-binary @duckdock-pack-v1.zip
```

上传 URL 是短期一次性能力，不需要也不应该暴露 MinIO AccessKey/SecretKey。

## 6. Finalize

```http
POST /api/v1/reports/{report_id}/finalize
Authorization: Bearer <DUCKDOCK_REPORT_TOKEN>
Content-Type: application/json

{
  "sha256": "actual-64-char-sha256",
  "size_bytes": 123456,
  "manifest": {
    "schema_version": "duckdock-pack-v1",
    "runtime_id": "openclaw-prod-001"
  }
}
```

DuckDock 会重新从 MinIO/S3 读取对象头和 sha256，不能只信任实例提交的值。校验成功后状态进入 `uploaded`，随后 worker 解析包并更新为 `succeeded` 或 `failed`。

## 7. Pack 内容要求

最小有效包：

```text
duckdock-pack-v1.zip
├── manifest.json
├── runtime.json
├── inventory/
│   ├── skills.ndjson
│   ├── sessions.ndjson
│   └── artifacts.ndjson
└── summaries/
    └── weekly-report.md
```

`manifest.json` 必须包含：

```json
{
  "schema_version": "duckdock-pack-v1",
  "generated_at": "2026-05-21T16:00:00+08:00",
  "runtime_id": "openclaw-prod-001",
  "report_type": "weekly"
}
```

## 8. 状态机

```text
pending -> uploaded -> ingesting -> succeeded
                         \-> failed
pending -> expired
```

第一阶段支持单文件 zip。后续如果包超过 512MB，再扩展 multipart upload。
