# Adapter Conformance Contract: DuckDock 2.0

**Status**: Ready for G0 review; implementation certification starts at G1/G2

**Version**: 0.2

**Prepared**: 2026-07-17

**Applies to**: OpenClaw adapter, Generic OTLP bridge, Pack/ATIF importer

## 1. Purpose

本文定义 DuckDock 2.0 Agent 运行接入的统一兼容标准。OpenClaw、Generic OTLP 和 Pack/ATIF 使用不同协议和到达方式，但进入 DuckDock 后必须产生相同、可审计的治理结果：

- Runtime 和 Namespace 由可信服务端映射确定；
- Deployment、Session、Run 和 Trace 的关联可重复验证；
- DuckDock 控制平面只接收 metadata-only 数据；
- 重试、离线补传和重复导入不产生重复事实；
- 冲突、丢失、截断和信任降级不可静默发生；
- 原始 spans 和 trajectory 内容留在 telemetry backend 或 MinIO，不进入 MySQL。

本文是 Foundation phase 的 adapter conformance contract，不是某个具体 SDK 的实现说明。任何 Adapter 只有通过相应能力等级的全部必选测试，才能声明兼容。

## 2. Terminology

| Term | Meaning |
|---|---|
| Adapter | 把某个 Harness、协议或离线格式映射到 DuckDock 控制平面的组件 |
| Profile | 一条具体兼容路径：OpenClaw、Generic OTLP 或 Pack/ATIF |
| Control envelope | 低容量、严格 schema、metadata-only 的 Session/Run 生命周期请求 |
| Telemetry | 原始或半原始 spans/events，通常通过 OTLP 到 Collector/trace backend |
| Artifact | MinIO 中的不可变 Pack/ATIF 或其他内容对象；MySQL 仅保存索引 |
| Trusted mapping | 由 ReporterCredential、Collector receiver credential 或一次性 upload grant 解析出的 Runtime/Namespace 映射 |
| External ID | 来源系统分配的 session/run/deployment ID，仅用于相关性，不用于授权 |
| Replay | 因超时、离线恢复或任务重启而再次发送相同逻辑操作 |
| Attestation | 对来源、载荷或运行环境的可验证证明；checksum 本身不是 attestation |

## 3. Normative Language

本文中的 **MUST**、**MUST NOT**、**SHOULD**、**SHOULD NOT** 和 **MAY** 为规范性要求。

- **MUST/MUST NOT**：conformance 硬门禁。
- **SHOULD/SHOULD NOT**：除非 Adapter 文档给出可审计理由，否则视为不兼容。
- **MAY**：可选能力，必须通过协商后才能使用。

## 4. Compatibility Profiles

### 4.1 OpenClaw profile (`openclaw-reporter`)

适用于能够运行 DuckDock Reporter/adapter 的 OpenClaw 实例。

Data paths:

```text
OpenClaw
  |-- ReporterCredential --> DuckDock Session/Run control envelopes
  |-- optional OTel SDK ---> OTel Collector ---> trace backend
  +-- optional Pack/ATIF --> upload path ------> MinIO
```

Requirements:

- MUST 通过 ReporterCredential 握手和写入控制 envelope。
- MUST 使用 OpenClaw 稳定实例/Session/Run 标识；若来源没有稳定 ID，Adapter 必须持久化生成的 ID。
- MAY 同时发送 OTLP；两个路径必须使用同一个 canonical external run ID 或显式 correlation key。
- MUST NOT 在 control envelope 中复制 prompt、messages、tool arguments/results 或 span events。

### 4.2 Generic OTLP profile (`generic-otlp-bridge`)

适用于任何可以发 OTLP 但不能直接调用 DuckDock Reporter API 的 Harness。

Data paths:

```text
Harness OTel SDK --> authenticated OTel Collector
                      |-- raw telemetry --> trace backend
                      +-- normalized metadata projection --> DuckDock control plane
```

Requirements:

- MUST 在 Collector receiver 使用可验证 credential，并由服务器配置映射到一个 Runtime。
- MUST 覆盖或删除客户端提供的 `namespace_id`、`runtime_id` 和治理属性；不得信任 resource attributes 自报身份。
- MUST 将 raw spans 路由到 trace backend，而不是逐 span 写入 DuckDock API/MySQL。
- 若 bridge 创建 AgentRun，MUST 从一个明确 root span/run boundary 生成且使用稳定的 external run ID。
- 若无法可靠确定 run boundary，MUST 只登记 telemetry reference/未匹配状态，不得伪造完整 Run。

### 4.3 Pack/ATIF profile (`pack-atif-import`)

适用于离线、隔离网络或批量迁移场景。Pack 是带 manifest、checksum 和一个或多个 payload 的传输容器；payload MAY 使用 ATIF。Foundation phase 只要求 manifest/artifact 索引兼容，不要求完整 ATIF 解析。

Data paths:

```text
Harness/exporter --> immutable Pack/ATIF --> authenticated upload
                                           |-- bytes --> MinIO
                                           +-- manifest/run index --> MySQL
```

Requirements:

- MUST 先校验 manifest、大小、checksum、路径和租户绑定，再登记 artifact。
- MUST 把 Pack/ATIF bytes 写入 MinIO；MUST NOT 把完整 JSON/archive 写入 MySQL。
- MUST 区分 checksum 完整性与身份 attestation。只有 checksum 的导入不得标记为 `PRODUCER_ATTESTED`。
- MUST 支持关联既有 Run；创建新 Run 时必须满足与在线路径相同的 tenant、idempotency 和状态约束。
- MUST 防御 path traversal、压缩炸弹、重复 entry、超额解压和不支持的 schema/version。

### 4.4 AgentLoop connector (`agentloop-mapping`, implementation deferred)

AgentLoop connector 仅作为后续可选 Provider mapping。G0 冻结可表达性边界，但不实现或认证私有 API connector：

- 不定义 AgentLoop 私有 API 为 DuckDock 标准；
- 不以 AgentLoop tenant/project/run ID 作为治理事实源；
- 不要求任何基础 Adapter 安装 AgentLoop SDK；
- 未来 connector 必须映射到本文相同的 handshake、identity、Run、artifact、idempotency 和 error contract；
- 只有通过所声明等级的通用 conformance suite，才能标记为 DuckDock-compatible。

G0 concept mapping：

| AgentLoop concept | DuckDock 2.0 mapping | Authority / boundary | Implementation milestone |
|---|---|---|---|
| AgentSpace / project | Namespace-scoped `TelemetrySink.project_ref` | 外部 ID 只作 provider reference，不能创建或授权 Namespace | S3-S4 connector feasibility |
| Application / Agent | 已登记 AIAsset / AgentPackageVersion 的显式 binding | 禁止按名称自动合并资产 | S4 mapping |
| Session | `AgentSession.external_session_id` | 必须位于 credential-derived Runtime/Namespace | S2 core, connector later |
| Trace / Trajectory | `AgentRun` + `TraceBackendRef`；可选 `AgentRunArtifact` | 原始 spans/trajectory 留在 AgentLoop 或 MinIO，不进入 MySQL | S3-S4 |
| Agent/Model/Tool spans | provider trace + DuckDock aggregate counts/reference | 不创建 MySQL Span/ModelCall/ToolCall 明细表 | S3-S4 |
| Dataset / Pipeline | future DatasetVersion + materialization job reference | AgentLoop 可做执行 Provider，DuckDock 保留版本、审批与 lineage | S5-S6 |
| Evaluation task/result | future EvalRun/EvaluationResult provider reference | Provider 分数不是最终 Release 决策 | S5-S6 |
| Experiment | future Experiment/Variant/Comparison | 必须固定 Package/Dataset/Evaluator versions | S6 |
| Prompt/Skill/Memory asset | AgentPackage component or governed AIAsset reference | 只有显式 digest/version mapping 才能成为正式证据 | S7+ |
| Audit/risk event | future SafetyFinding/EvidenceItem projection | eBPF 与私域内容采集不在本 Foundation 范围 | post-2.0 review |

结论：核心对象均可通过 provider-neutral ID、reference、artifact 和 future evaluation Ports 表达，没有必须把 AgentLoop 私有 ORM/schema 引入 DuckDock 核心的对象。Connector 实现和私有 API fixture 仍延期。

### 4.5 WorkBuddy / other harness feasibility mapping

G0 不冻结 WorkBuddy 私有 API。任何 WorkBuddy 或其他 Harness 按其可用出口选择现有 profile：可安装 Reporter 时使用 `openclaw-reporter` 等价控制面；仅能输出 OTLP 时使用 `generic-otlp-bridge`；只能离线导出时使用 `pack-atif-import`。产品名不产生第四套身份、Run 或幂等模型。

## 5. Shared Capability Levels

等级描述 Adapter 组合最终提供的治理结果，而不是指定使用哪种传输。高等级 MUST 满足所有低等级硬门禁。Generic OTLP 可通过 Collector bridge 达成等级；不能仅以“已发送 spans”声明兼容。

### DD-C0: Registered and Negotiated

Minimum outcome:

- Adapter 身份、profile、协议版本和能力可协商；
- credential 被解析到一个已注册 Runtime 和 Namespace；
- 客户端自报租户信息不参与授权；
- 协商结果和限制可以被缓存并安全刷新。

DD-C0 不代表已经产生 Run 或 Trace。

### DD-C1: Governed Run Index

Includes DD-C0 plus:

- metadata-only Session/Run start/complete；
- 不可变 Deployment revision 的绑定策略明确：非生产可暂存 unresolved 关联；生产 Run 必须固定 AgentPackageVersion、Deployment revision 和组件 digest；
- 严格幂等、终态状态机和重放；
- WorkTrace 与 AgentRun 语义分离；
- 领域变更和 Outbox 原子提交。

这是 OpenClaw Reporter 的 Foundation phase 最低目标等级。

生产 Run 缺少上述版本 provenance 时仍可作为诊断索引接收，但 MUST 标记为 `evidence_completeness=INCOMPLETE`，MUST NOT 作为正式 Release、Promotion 或 Handover Gate 的充分证据。

### DD-C2: Trace Correlated

Includes DD-C1 plus:

- canonical Run 与 `otel_trace_id`/root span 的稳定映射；
- raw telemetry 进入受支持 backend，不进入 MySQL；
- `TraceBackendRef` 可以 pending、confirmed、error 或 stale；
- trace 晚到、重复和 provider 不可用不会改写终态 Run；
- Collector 的身份重写、内容策略和路由经过验证。

这是 Generic OTLP bridge 声明“DuckDock Run compatible”所需等级。仅能转发 OTLP、不能形成可信 Run 的实现最多声明 `DD-C0 + telemetry-only extension`，不得声明 DD-C1/DD-C2。

### DD-C3: Durable Trajectory and Offline Recovery

Includes DD-C2 plus:

- 本地/Collector durable buffer 或 Pack/ATIF 离线导出；
- 可恢复 ack cursor 和有界、显式的 loss semantics；
- artifact checksum、schema/version、sensitivity 和 completeness 可验证；
- 重复导入不会复制 Run/artifact/event；
- 内容对象进入 MinIO并遵守访问/保留策略。

Pack/ATIF importer 可先声明 `DD-C1 + artifact extension`；只有同时完成 trace correlation 和在线/离线恢复闭环时才声明完整 DD-C3。

### DD-C4: Evaluation-Ready Evidence (deferred)

Includes DD-C3 plus:

- 可重现 normalizer/evaluator/version provenance；
- 轨迹完整性和 attestation 满足策略；
- Evaluation evidence 固定到确切 Deployment revision/Run/artifact；
- 结果可被 future Release Gate 消费而不读取“Namespace 最新评测”。

Foundation phase 不认证 DD-C4。本文仅保留等级定义，防止低等级 trace 被误当成发布证据。

## 6. Capability Advertisement and Handshake

### 6.1 Adapter descriptor

所有 profile MUST 能产生等价的 descriptor。在线 profile 在 handshake 请求中提交；Pack/ATIF 写入 manifest；Generic OTLP bridge 可由管理员注册配置生成。

```json
{
  "adapter_id": "openclaw-reporter",
  "adapter_version": "1.2.0",
  "profile": "openclaw-reporter",
  "protocol": "duckdock-adapter",
  "protocol_version": "1.0",
  "source_schema": "openclaw-run",
  "source_schema_version": "2026-07",
  "capabilities": [
    "session_control",
    "run_control",
    "otel_trace_correlation",
    "durable_replay"
  ],
  "content_capture_modes": ["metadata_only"],
  "instance_id": "adapter-local-stable-id",
  "boot_id": "per-process-random-id",
  "client_nonce": "128-bit-or-stronger-random-base64url",
  "client_time": "2026-07-17T08:00:00Z"
}
```

Normative rules:

- `adapter_id`, `adapter_version`, `profile`, `protocol_version` MUST be present.
- `instance_id` MUST remain stable across process restarts for one installed Adapter; `boot_id` MUST change on process restart.
- `client_nonce` MUST contain at least 128 bits of cryptographic randomness. The server retains its hash per credential + Adapter instance for at least the advertised handshake replay window.
- Same nonce + same canonical descriptor is an idempotent handshake replay; same nonce + different descriptor is `IDEMPOTENCY_CONFLICT`. An expired/reused nonce cannot establish a new handshake.
- Descriptor MUST NOT include token, secret, Namespace ID, raw prompt or endpoint credentials.
- Unknown capability names MAY be ignored but MUST be reported in `rejected_capabilities`.
- Capability use before acceptance MUST fail with a stable unsupported-capability error.

### 6.2 Server handshake response

Representative response:

```json
{
  "handshake_id": "hs_public_id",
  "protocol_version": "1.0",
  "runtime_public_id": "rt_public_id",
  "accepted_capabilities": [
    "session_control",
    "run_control",
    "otel_trace_correlation",
    "durable_replay"
  ],
  "rejected_capabilities": [],
  "content_capture_mode": "metadata_only",
  "attestation_challenge": null,
  "limits": {
    "max_envelope_bytes": 65536,
    "max_metadata_keys": 32,
    "max_metadata_depth": 1,
    "max_metadata_value_bytes": 2000,
    "max_clock_skew_seconds": 300
  },
  "idempotency": {
    "canonicalizer_version": "1",
    "minimum_replay_window_seconds": 604800
  },
  "server_time": "2026-07-17T08:00:01Z",
  "expires_at": "2026-07-18T08:00:01Z"
}
```

Rules:

- Response MUST NOT expose internal numeric Runtime/Namespace IDs.
- Runtime mapping MUST come from authenticated server-side registration.
- Adapter MUST re-handshake after expiry, credential rotation, protocol rejection or capability configuration change.
- Clock skew beyond the advertised limit MUST produce a warning/degraded signal or hard rejection according to operation; server receive time remains authoritative.
- Handshake success MUST NOT elevate trust to attested.
- `attestation_challenge` is null for the base bearer/mTLS handshake. A non-null, expiring challenge is used only when a profile advertises cryptographic/workload attestation and is verified by `AttestationVerifierPort`.

### 6.3 Profile-specific handshake

| Profile | Authentication | Handshake carrier | Server-side identity source |
|---|---|---|---|
| OpenClaw | ReporterCredential bearer token | DuckDock handshake endpoint | ReporterCredential -> Runtime -> Namespace |
| Generic OTLP | mTLS/API key or supported receiver auth | Collector config/registration plus health probe | receiver credential mapping -> Runtime -> Namespace |
| Pack/ATIF | ReporterCredential or scoped one-time upload grant | manifest preflight/upload-init | upload grant/credential -> Runtime -> Namespace |
| AgentLoop | Deferred connector credential | future adapter handshake | explicit connector registration; never source project attrs |

## 7. Identity and Trust Contract

### 7.1 Authoritative identity

The following values are authoritative only when resolved server-side:

- `namespace_id`
- `runtime_id`
- allowed Deployment set/environment
- credential scopes
- allowed TelemetrySink/upload bucket

An Adapter MAY send `runtime_public_id` as a consistency assertion. The server MUST reject a mismatch and MUST NOT use it to change the credential mapping.

### 7.2 Untrusted external identity

These are correlation inputs, not authorization:

- external session/run/deployment IDs
- OTel resource/service attributes
- trace/span IDs
- Pack producer/project/tenant fields
- AgentLoop project/space identifiers

They MUST be stored only after validation and under the authenticated Namespace.

### 7.3 Trust strength and source

`trust_level` describes assurance strength; `trust_source` describes ingress. They are orthogonal and MUST both be recorded.

| `trust_level` | Evidence required |
|---|---|
| `CHANNEL_AUTHENTICATED` | Valid ReporterCredential, Collector receiver credential or upload grant, plus accepted envelope/artifact binding |
| `PRODUCER_ATTESTED` | Channel-authenticated input plus successful `AttestationVerifierPort` proof binding producer/workload and payload |
| `UNVERIFIED` | Legacy/admin workflow or input without an accepted trusted channel; restricted policy only |

| `trust_source` | Permitted ingress |
|---|---|
| `REPORTER` | OpenClaw or compatible Reporter API |
| `COLLECTOR` | Authenticated Generic OTLP receiver and trusted projection |
| `IMPORT` | Pack/ATIF upload/import |
| `ADMIN` | Explicit legacy/remediation workflow |

Rules:

- Checksum proves byte integrity, not producer identity. A valid credential + checksum import is `CHANNEL_AUTHENTICATED` + `IMPORT`, not producer-attested.
- OTLP receiver authentication proves the sender channel and maps to `CHANNEL_AUTHENTICATED` + `COLLECTOR`; it MUST NOT assume producer attestation.
- Any missing/invalid proof MUST downgrade or reject according to policy and emit `AgentRunTrustDegraded`; it MUST NOT silently keep a stronger level.
- Trust upgrades are append-only audited decisions; terminal Run facts are not overwritten without a separate evidence record.

## 8. Canonical Session, Run and Trace Mapping

### 8.1 Canonical keys

DuckDock uses these tenant-scoped logical keys:

```text
Session: (namespace, runtime, external_session_id)
Run:     (namespace, runtime, external_run_id)
Trace:   (namespace, telemetry_sink, external_trace_id)
Deploy:  (namespace, runtime, external_deployment_id, revision)
```

Adapters MUST preserve stable external IDs across replay. Generated IDs MUST be persisted before the first network attempt.

### 8.2 Mapping precedence

When more than one correlation signal is present, apply this order:

1. Existing exact Run key under the authenticated Runtime/Namespace.
2. Explicit accepted `run_public_id` previously returned by DuckDock, checked against the same Runtime/Namespace.
3. Exact `(external_run_id, deployment revision)` mapping from the validated control envelope or artifact manifest.
4. Exact `otel_trace_id` mapping if unique within the Namespace and not conflicting with 1-3.
5. Root span heuristic only inside a registered Generic OTLP normalizer and only if it yields one unambiguous run boundary.

If signals disagree, the Adapter MUST return/record a mapping conflict. It MUST NOT merge Runs, move a Run across Runtime/Namespace, or choose the first match.

### 8.3 OpenClaw mapping

- OpenClaw session ID maps to `external_session_id`.
- One logical harness execution/turn maps to `external_run_id`; retry attempts MAY use an explicit `attempt` while retaining a stable parent correlation policy.
- If OpenClaw also emits OTel, the adapter MUST inject the same external run correlation attribute into the root span.
- OpenClaw lifecycle events MUST NOT be interpreted as WorkTrace; WorkTrace linking is explicit and optional.

### 8.4 Generic OTLP mapping

- A configured normalizer MUST identify supported root span kinds/names and source schema version.
- `trace_id` MUST be 32 lowercase hexadecimal characters after normalization; `span_id` MUST be 16.
- The bridge MUST ignore/rewrite client `duckdock.namespace_id` and `duckdock.runtime_id` attributes.
- If an external run ID is present in an allowlisted attribute, use it after validation. Otherwise a deterministic bridge ID MAY be derived from authenticated Runtime + trace ID + documented normalizer version.
- Multiple candidate root spans, conflicting run IDs or reused trace IDs MUST enter mapping-conflict quarantine.
- Late spans MAY update external telemetry completeness/reference status but MUST NOT reopen or change a terminal AgentRun.

### 8.5 Pack/ATIF mapping

Manifest minimum fields:

```json
{
  "manifest_schema": "duckdock-pack",
  "manifest_version": "1.0",
  "pack_id": "producer-stable-id",
  "producer": {
    "adapter_id": "openclaw-pack-exporter",
    "adapter_version": "1.2.0",
    "instance_id": "stable-instance-id"
  },
  "source_schema": "atif",
  "source_schema_version": "<declared-version>",
  "external_session_id": "session-001",
  "external_run_id": "run-001",
  "deployment": {
    "external_deployment_id": "agent-prod",
    "revision": "2026.07.17-1"
  },
  "otel_trace_id": "0123456789abcdef0123456789abcdef",
  "created_at": "2026-07-17T08:10:00Z",
  "payloads": [
    {
      "path": "trajectory.atif.json",
      "media_type": "application/json",
      "size_bytes": 12345,
      "sha256": "<64 lowercase hex characters>",
      "completeness": "complete",
      "sensitivity": "restricted"
    }
  ]
}
```

Rules:

- `pack_id` plus authenticated Runtime scopes import idempotency.
- Manifest MUST NOT inline trajectory, prompt or tool content.
- Artifact paths MUST be relative, normalized, unique and unable to escape the extraction root.
- Payload count, compressed bytes, uncompressed bytes and compression ratio MUST be limited.
- Unknown ATIF version MAY be stored as opaque artifact only if policy permits; it MUST NOT be marked normalized/evaluation-ready.
- Partial packs MUST set completeness and a loss reason; they MUST NOT claim complete trajectory evidence.

## 9. Metadata-Only Contract

### 9.1 Allowed control metadata

Foundation phase control envelopes MAY contain only validated fields in these categories:

- schema/adapter versions and idempotency keys;
- external Session/Run/Deployment IDs;
- timestamps, duration, attempt and terminal status;
- OTel trace/root span correlation IDs;
- aggregate step/model/tool call counts and token counts;
- bounded error category/code, never raw stack/body;
- allowlisted low-sensitive tags such as environment or harness version;
- artifact manifest references, checksum, size, schema, sensitivity and completeness.

### 9.2 Forbidden control content

The following MUST be rejected, not silently dropped, when sent to DuckDock control APIs or manifest metadata:

- `prompt`, `system_prompt`, `messages`, `conversation`;
- `completion`, `response_text`, model reasoning or chain-of-thought;
- `tool_arguments`, `tool_result`, shell command output;
- raw span/event arrays, logs or stack traces;
- file bodies, screenshots, audio, binary/base64 payloads;
- credentials, Authorization headers, cookies or provider secret values.

Strict unknown-field rejection is required. A key alias, casing change or nested placement MUST NOT bypass the denylist.

### 9.3 Telemetry and artifact content

- Default policy across Reporter, Generic OTLP and Pack/ATIF is `metadata_only`. A path MUST NOT send prompt/response, retrieval text, Tool I/O or file content to any trace backend or object store without an explicit, versioned Namespace authorization naming the allowed sink/bucket, data classes, purpose, retention and redaction policy.
- Hidden chain-of-thought, credentials, Secrets and private personal workspace data are prohibited even when content capture is authorized.
- Redaction and secret scanning MUST happen at the producing edge or controlled Collector/import preflight before content reaches Langfuse, another trace backend, the final MinIO object, DuckDock logs or Outbox.
- Generic OTLP MAY carry policy-authorized, pre-redacted content to the configured trace backend. The DuckDock projection MUST remain metadata-only.
- Pack/ATIF MAY contain policy-authorized, pre-redacted trajectory content inside encrypted/controlled MinIO objects. Manifest, MySQL index and Outbox payload remain metadata-only. Import preflight MUST reject or quarantine content without the required policy reference and redaction receipt before final object registration.
- `content_capture_mode` for Foundation phase DuckDock control records MUST equal `metadata_only` even if an external backend is separately authorized for richer content.
- A shared Secret Canary corpus MUST prove prohibited markers are absent from accepted control records, trace backend payloads, final MinIO objects, logs and Outbox; detection MUST produce explicit rejection/quarantine and an audit event without echoing the marker.

## 10. Idempotency and Replay

### 10.1 Canonicalization

Before hashing, Adapter input MUST be parsed by a strict schema and normalized using an advertised canonicalizer version:

1. reject unknown/content fields;
2. normalize enum casing and timestamps to UTC;
3. define absent vs null behavior;
4. enforce numeric range and representation;
5. serialize UTF-8 JSON with sorted keys and fixed separators;
6. calculate SHA-256.

Never hash unvalidated raw JSON as the sole conflict check.

### 10.2 Operation keys

| Operation | Idempotency scope | Same key/same hash | Same key/different hash |
|---|---|---|---|
| Handshake | credential + adapter instance + handshake nonce | Return accepted negotiation | Reject conflict/new nonce required |
| Session start | Namespace + Runtime + operation + key | Return existing Session | HTTP 409 |
| Run start | Namespace + Runtime + operation + key | Return existing Run | HTTP 409 |
| Run complete | Namespace + Runtime + Run + operation + key | Return existing terminal result | HTTP 409 |
| Pack import | Namespace + Runtime + pack ID | Return existing import/artifact status | HTTP 409/quarantine |
| Trace reference | Namespace + Sink + trace ID | Upsert identical reference state | Mapping conflict/quarantine |

An idempotent replay MUST NOT add another Outbox event. Domain event idempotency keys MUST be derived from the logical transition, not request arrival time.

### 10.3 Ordered batch replay

- Each locally buffered item MUST have a stable queue sequence and idempotency key.
- Adapter SHOULD send in order per Runtime/Session but MUST tolerate server-side duplicates and out-of-order arrival.
- Run completion arriving before start MUST return a retryable dependency error or enter a bounded pending-import state; it MUST NOT synthesize an untrusted start silently.
- Batch acknowledgement MUST identify each accepted/replayed/retryable/rejected item. A single bad item MUST NOT cause already committed items to be forgotten.
- Adapter MUST advance its durable ack cursor only after storing the server acknowledgement.

### 10.4 Replay window

- Server MUST advertise a minimum idempotency retention window.
- Adapter MUST retain unacknowledged items at least until success, explicit permanent rejection or operator-approved discard.
- If a replay is older than the server window, the server MUST return a stable `IDEMPOTENCY_WINDOW_EXPIRED` error or reconcile by external key; it MUST NOT create a duplicate without warning.

### 10.5 At-least-once delivery layers

DuckDock does not claim end-to-end exactly-once delivery. The four replay layers have distinct contracts:

1. **Request replay**: the same operation key + canonical hash returns the existing resource and MUST NOT insert another Outbox row.
2. **Publisher replay**: one immutable Outbox row MAY be delivered more than once after timeout/crash; every attempt carries the same `event_id`.
3. **Consumer replay**: each consumer MUST persist dedupe state by `event_id` before treating processing as complete.
4. **External side effect replay**: provider calls MUST use a stable idempotency key and store an immutable receipt; a consumer dedupe check alone is insufficient after an ambiguous remote timeout.

Tests and dashboards MUST report these layers separately. “No duplicate domain fact” MUST NOT be documented as “the transport delivers exactly once.”

## 11. Offline Buffering and Loss Semantics

### 11.1 Common requirements

Any profile claiming `durable_replay` or DD-C3 MUST:

- persist an envelope/artifact before considering it queued;
- encrypt sensitive local buffers according to deployment policy;
- never persist bearer tokens inside queued payloads;
- use bounded storage with high-water and hard-limit signals;
- expose oldest pending age, item/byte count, retry count and last error;
- resume from a durable ack cursor after crash/restart;
- require explicit policy/operator action before discarding unacknowledged data;
- emit a loss marker with range/count/reason and downgrade completeness/trust after any discard.

Silent best-effort drop is a conformance failure.

### 11.2 OpenClaw buffering

- Persist Session/Run control envelopes before first send when offline support is enabled.
- Preserve generated external IDs and idempotency keys across restart.
- Coalesce only provably identical heartbeats/status updates; MUST NOT coalesce distinct Run transitions.
- Credential rotation MUST re-authenticate queued sends without rewriting their tenant identity.

### 11.3 Generic OTLP buffering

- Use Collector-supported persistent queue/WAL for a DD-C3 claim.
- Memory-only retry can support DD-C2 availability but not DD-C3 durability.
- Raw telemetry queue and DuckDock metadata projection queue require a documented correlation/ack strategy.
- If telemetry is dropped by sampling, that is policy sampling, not transport loss; sampling policy/version MUST be recorded. Unexpected queue loss MUST set completeness/loss state.

### 11.4 Pack/ATIF buffering

- Pack creation is complete only after manifest and payload checksums are fsynced/atomically finalized by the exporter.
- Upload SHOULD use resumable multipart semantics with checksum verification.
- Server MUST not register a COMPLETE artifact until all declared payload bytes verify.
- Interrupted uploads remain pending and expire by policy; cleanup MUST not delete a previously verified artifact.

## 12. Error Semantics

### 12.1 Canonical error envelope

DuckDock-facing HTTP errors MUST use an equivalent stable shape:

```json
{
  "type": "about:blank",
  "title": "Idempotency conflict",
  "status": 409,
  "code": "IDEMPOTENCY_CONFLICT",
  "detail": "The idempotency key was already used with different validated content.",
  "request_id": "req_public_id",
  "retryable": false,
  "details": {
    "operation": "run_start"
  }
}
```

HTTP APIs use `application/problem+json` with the top-level shape above. `detail` and `details` MUST NOT include token, raw request content, prompt, tool output, provider response body, internal SQL or cross-tenant resource existence.

### 12.2 HTTP mapping

| HTTP | Canonical codes | Retry | Required behavior |
|---:|---|---|---|
| 400 | `MALFORMED_REQUEST`, `INVALID_TRACE_ID` | no | Fix producer; do not replay unchanged |
| 401 | `CREDENTIAL_INVALID`, `CREDENTIAL_EXPIRED` | after credential refresh | Pause send; never log credential |
| 403 | `SCOPE_DENIED`, `PROFILE_NOT_ALLOWED` | no until config change | Audit; do not probe other IDs |
| 404 | `RESOURCE_NOT_FOUND` | conditional | Same external response for foreign/missing resource |
| 409 | `IDEMPOTENCY_CONFLICT`, `STATE_CONFLICT`, `MAPPING_CONFLICT` | no unchanged | Quarantine/manual reconcile |
| 413 | `ENVELOPE_TOO_LARGE`, `PACK_TOO_LARGE` | no unchanged | Split only where protocol permits |
| 415 | `MEDIA_TYPE_UNSUPPORTED` | no | Upgrade/export supported format |
| 422 | `SCHEMA_UNSUPPORTED`, `CONTENT_FIELD_FORBIDDEN`, `VALIDATION_FAILED` | no unchanged | Correct adapter/schema |
| 429 | `RATE_LIMITED`, `BUFFER_PRESSURE` | yes | Honor `Retry-After`, jittered backoff |
| 503 | `TEMPORARILY_UNAVAILABLE`, `DEPENDENCY_PENDING` | yes | Exponential backoff and replay |

### 12.3 OTLP mapping

Generic OTLP bridge MUST map transport errors without losing retry classification:

| OTLP/gRPC status | Adapter classification | Behavior |
|---|---|---|
| `UNAUTHENTICATED` | credential invalid | Stop/reload credential |
| `PERMISSION_DENIED` | mapping/scope denied | Permanent until configuration changes |
| `INVALID_ARGUMENT` | schema/content invalid | Permanent for unchanged item |
| `RESOURCE_EXHAUSTED` | rate/buffer pressure | Retry with backoff |
| `UNAVAILABLE` | temporary unavailable | Retry from persistent queue |
| partial success | per-item accepted/rejected | Record rejection count/reason; do not report full success |

The bridge MUST NOT convert a permanent schema/identity error into an infinite retry loop.

### 12.4 Import processing status

Pack/ATIF upload MAY return `202 Accepted` after bytes are stored. The import resource MUST then expose one of:

- `PENDING_VALIDATION`
- `VALIDATING`
- `IMPORTED`
- `IMPORTED_PARTIAL`
- `QUARANTINED`
- `REJECTED`

Only `IMPORTED`/`IMPORTED_PARTIAL` may create/link artifact metadata. `IMPORTED_PARTIAL` MUST retain completeness/loss reasons and cannot satisfy complete-evidence policy.

## 13. Required Fixture Corpus

Implementations SHOULD place fixtures under a repository test-data convention such as:

```text
backend/tests/fixtures/adapter_conformance/
  common/
  openclaw/
  generic_otlp/
  pack_atif/
  expected/
```

The exact path may follow repository conventions, but fixture IDs and expected canonical hashes MUST be stable.

### 13.1 Common fixtures

| Fixture ID | Content | Expected result |
|---|---|---|
| `COM-001` | Valid handshake, minimal capabilities | Accepted DD-C0 negotiation |
| `COM-002` | Claimed foreign Namespace/Runtime | Rejected and audited; no mapping change |
| `COM-003` | Same JSON semantics with reordered keys | Same canonical hash |
| `COM-004` | Same idempotency key, changed run ID | 409 conflict |
| `COM-005` | Prompt/tool content at top-level and nested aliases | 422 forbidden content |
| `COM-006` | Oversized/deep metadata | 413 or 422 stable error |
| `COM-007` | Clock within/outside negotiated skew | Accept/warn or stable rejection |
| `COM-008` | Cross-tenant public ID probe | Non-enumerable 404/denial |
| `COM-009` | Outbox insert failure | Domain transaction rolls back |
| `COM-010` | Same accepted replay after response loss | Existing result, no new Outbox event |
| `COM-011` | Reused handshake nonce with changed descriptor | 409 conflict and audit; no new handshake |
| `COM-012` | Production Run without fixed Package/Deployment/component digests | Indexed as INCOMPLETE; ineligible for formal Gate evidence |
| `COM-013` | Secret Canary in direct/nested/aliased metadata | Rejected without echo; absent from DB, logs and Outbox |

### 13.2 OpenClaw fixtures

| Fixture ID | Content | Expected result |
|---|---|---|
| `OCL-001` | Session start, Run start, Run success | One Session, one terminal Run |
| `OCL-002` | Process restart with stable instance/run/idempotency IDs | Idempotent replay |
| `OCL-003` | Reporter token mapped to another Runtime than request assertion | Rejected and audited |
| `OCL-004` | Same external Run with optional OTel trace correlation | One Run and one pending/confirmed ref |
| `OCL-005` | Complete before queued start due offline reorder | Retryable dependency handling |
| `OCL-006` | Buffer hard limit and operator discard | Explicit loss marker/trust downgrade |
| `OCL-007` | Secret Canary in control envelope | 422; marker absent from every sink and audit body |

### 13.3 Generic OTLP fixtures

Fixtures SHOULD use canonical OTLP protobuf or JSON representations and document semantic schema version.

| Fixture ID | Content | Expected result |
|---|---|---|
| `OTL-001` | Authenticated single root Agent span with external run ID | One correlated Run/reference |
| `OTL-002` | Forged client tenant/runtime attributes | Collector rewrites/removes them |
| `OTL-003` | Two candidate root spans | Quarantine mapping conflict |
| `OTL-004` | Late child spans after terminal Run | Reference/completeness update only |
| `OTL-005` | Duplicate export after Collector restart | No duplicate Run/reference |
| `OTL-006` | Raw prompt/tool attributes | Not present in DuckDock projection; Collector policy tested |
| `OTL-007` | Trace backend unavailable | Persistent retry; Run remains committed |
| `OTL-008` | Partial-success export | Rejected count/reason surfaced |
| `OTL-009` | Content attributes under default metadata-only policy | Dropped/rejected before trace backend and DuckDock projection |
| `OTL-010` | Authorized content with redaction receipt plus Secret Canary | Canary rejected; only authorized redacted fields reach configured backend |

### 13.4 Pack/ATIF fixtures

| Fixture ID | Content | Expected result |
|---|---|---|
| `PAT-001` | Valid manifest and one checksum-valid ATIF payload | Imported artifact metadata |
| `PAT-002` | Same Pack uploaded twice | Same import/artifact, no duplicate event |
| `PAT-003` | Same Pack ID with changed checksum | 409/quarantine |
| `PAT-004` | Unknown ATIF version | Opaque artifact or policy rejection; never normalized |
| `PAT-005` | Bad checksum/truncated upload | Rejected/pending, no COMPLETE artifact |
| `PAT-006` | `../` path, absolute path or duplicate archive entry | Permanent security rejection |
| `PAT-007` | Excessive compression ratio/unpacked bytes | Pack-too-large/security rejection |
| `PAT-008` | Partial trajectory with declared loss | IMPORTED_PARTIAL and non-complete evidence |
| `PAT-009` | Valid checksum without attestation | Trust is CHANNEL_AUTHENTICATED + IMPORT, not PRODUCER_ATTESTED |
| `PAT-010` | Manifest Run ID conflicts with trace mapping | Quarantine; no merge/move |
| `PAT-011` | Content-bearing Pack without Namespace policy/redaction receipt | Rejected or quarantined before final object registration |
| `PAT-012` | Pack payload containing Secret Canary | Rejected/quarantined; marker absent from final MinIO object, logs and Outbox |

### 13.5 Expected result fixtures

For every accepted fixture, store or derive:

- canonical validated envelope JSON;
- canonicalizer version and SHA-256;
- expected AgentSession/AgentRun/Artifact/TraceBackendRef state;
- expected Outbox event types and idempotency keys;
- expected audit category without sensitive body;
- expected trust/completeness level;
- expected retry/permanent classification.

Golden fixtures MUST NOT contain live credentials or production content.

## 14. Conformance Test Matrix

Legend:

- **R**: required for the profile's stated Foundation phase level
- **C**: required only when the capability is advertised
- **N/A**: not applicable to that path
- **D**: deferred; must not be claimed in Foundation phase

| Test area | OpenClaw | Generic OTLP | Pack/ATIF | AgentLoop |
|---|:---:|:---:|:---:|:---:|
| DD-C0 descriptor/version negotiation | R | R | R | D |
| Credential -> Runtime -> Namespace mapping | R | R | R | D |
| Reject forged tenant/runtime claims | R | R | R | D |
| Credential rotation/re-handshake | R | R | C | D |
| Handshake nonce replay/conflict | R | R | R | D |
| Strict metadata-only control projection | R | R | R | D |
| Forbidden content alias/nesting rejection | R | R | R | D |
| Secret Canary absent from every configured sink | R | R | R | D |
| Session start idempotency | R | C | C | D |
| Run start/complete state machine | R | R for DD-C1+ | C | D |
| Same-key/same-hash replay | R | R | R | D |
| Same-key/different-hash conflict | R | R | R | D |
| Outbox atomicity/no duplicate events | R | R when Run projected | R when Run/artifact linked | D |
| Stable Deployment revision mapping | R | R for DD-C1+ | R when declared | D |
| Production provenance incomplete blocks formal evidence | R | R for DD-C1+ | R when Run declared | D |
| OTel trace/root span validation | C | R | C | D |
| Ambiguous trace/run quarantine | C | R | R when trace declared | D |
| Late telemetry behavior | C | R | N/A | D |
| External trace backend failure isolation | C | R | N/A | D |
| Durable queue/crash resume | C for DD-C3 | R for DD-C3 | upload resume R for DD-C3 | D |
| Ack cursor and ordered replay | C | C | R for batch import | D |
| Explicit buffer loss marker | C | C | R for partial pack | D |
| Manifest/checksum/size validation | C | N/A | R | D |
| Archive traversal/bomb defense | N/A | N/A | R | D |
| Unknown schema/version behavior | R | R | R | D |
| Attestation success/failure/downgrade | C | C | C | D |
| Cross-tenant list/detail isolation | R | R | R | D |
| No vendor SDK required by core | R | R | R | D |
| Candidate-pinned evaluation evidence | D | D | D | D |

## 15. Conformance Test IDs by Capability

### DD-C0 suite

- `CONF-C0-001`: valid descriptor negotiation
- `CONF-C0-002`: unsupported protocol/capability response
- `CONF-C0-003`: credential-to-Runtime mapping
- `CONF-C0-004`: forged identity rejection
- `CONF-C0-005`: expiry and re-handshake
- `CONF-C0-006`: response contains no internal Namespace/secret
- `CONF-C0-007`: nonce replay is idempotent only for the same canonical descriptor; changed descriptor conflicts

### DD-C1 suite

- `CONF-C1-001`: Session idempotency
- `CONF-C1-002`: Run start/complete lifecycle
- `CONF-C1-003`: canonical hash equivalence
- `CONF-C1-004`: idempotency conflict
- `CONF-C1-005`: concurrent duplicate start/complete
- `CONF-C1-006`: content field rejection
- `CONF-C1-007`: Deployment/tenant consistency
- `CONF-C1-008`: WorkTrace semantic independence
- `CONF-C1-009`: Outbox atomicity and replay dedupe
- `CONF-C1-010`: cross-tenant non-enumeration
- `CONF-C1-011`: Secret Canary and forbidden aliases are absent from DB, logs and Outbox
- `CONF-C1-012`: production Run without fixed provenance is incomplete and cannot satisfy formal evidence policy
- `CONF-C1-013`: request, publisher, consumer and external side-effect replay are independently exercised

### DD-C2 suite

- `CONF-C2-001`: canonical Run/trace mapping
- `CONF-C2-002`: client governance attributes overwritten
- `CONF-C2-003`: ambiguous root/mapping quarantine
- `CONF-C2-004`: duplicate and late span behavior
- `CONF-C2-005`: provider outage isolation/recovery
- `CONF-C2-006`: no raw telemetry in MySQL projection
- `CONF-C2-007`: partial success/error classification
- `CONF-C2-008`: default metadata-only blocks content before the trace backend; authorized content is pre-redacted
- `CONF-C2-009`: Secret Canary is absent from trace backend, DuckDock projection and logs

### DD-C3 suite

- `CONF-C3-001`: crash-safe local persistence
- `CONF-C3-002`: resume from durable ack cursor
- `CONF-C3-003`: retry after credential/provider recovery
- `CONF-C3-004`: bounded buffer pressure signals
- `CONF-C3-005`: explicit loss marker/completeness downgrade
- `CONF-C3-006`: Pack checksum and resumable upload
- `CONF-C3-007`: duplicate Pack import
- `CONF-C3-008`: archive traversal/bomb rejection
- `CONF-C3-009`: partial artifact policy
- `CONF-C3-010`: content stored in MinIO, index only in MySQL
- `CONF-C3-011`: content policy/redaction receipt preflight before final object registration
- `CONF-C3-012`: Secret Canary is absent from final MinIO objects, DuckDock indexes and logs

## 16. Certification Rules

An Adapter conformance report MUST include:

- adapter/profile/version and source schema versions;
- claimed DuckDock capability level and optional extensions;
- DuckDock server/normalizer/canonicalizer versions;
- exact fixture corpus revision;
- test results with skipped tests and reasons;
- MySQL/Collector/object-store versions used for integration tests;
- known loss, sampling, content and trust policies;
- signature/checksum of the report artifact.

Certification rules:

1. All required tests for the claimed level and profile MUST pass.
2. Identity isolation, forbidden-content, idempotency-conflict, transaction atomicity and archive-security tests are hard gates and cannot be waived.
3. Capability-dependent tests may be skipped only when the capability is not advertised; the Adapter MUST reject use of that capability at runtime.
4. A profile-specific extension does not imply a higher shared level. For example, storing an ATIF artifact alone does not imply DD-C3.
5. A telemetry-only Generic OTLP exporter cannot claim governed Run compatibility without the trusted bridge and DD-C1 tests.
6. Conformance applies to a specific adapter + configuration + protocol/schema version combination, not to a product name globally.
7. Any material change to identity mapping, canonicalization, normalizer, buffer persistence or error classification requires re-running the relevant suites.

## 17. G0 Contract Exit Criteria

G0 is a design freeze, not an implementation certification. It passes when:

- [x] Shared capability levels, descriptor, identity, trust, canonicalization, replay, loss and error semantics are versioned in this contract without vendor-owned core types.
- [x] OpenClaw, Generic OTLP and Pack/ATIF field/boundary mappings and fixture IDs have expected results defined.
- [x] AgentLoop concepts have a provider-neutral feasibility mapping; WorkBuddy/other Harnesses resolve to one of the three common profiles.
- [x] Metadata-only, explicit content authorization, redaction-before-storage, hidden-CoT prohibition and cross-sink Secret Canary tests are specified.
- [x] Request replay, Outbox publisher replay, consumer dedupe and external receipt semantics are separated.
- [x] DD-C0 through DD-C3 certification suites, required environments and non-waivable security tests are specified.
- [ ] Architecture Owner approves version 0.2 together with OpenAPI v2 and the four foundation schemas.

Implementation acceptance is deliberately later: S1-S2/G1 must pass the shared Reporter/Run/Outbox tests on real MySQL; S3-S4/G2 must pass the OpenClaw, Generic OTLP and Pack/ATIF integration profiles. No code or product UI may claim a capability before its required suite passes.

## 18. Deferred Extensions

- AgentLoop private schema/API mapper and its fixture corpus
- Full OTel GenAI/OpenInference normalizer catalog
- Complete ATIF codec and semantic validation
- EvaluationEngine conformance and DeepEval adapter
- Candidate-pinned evaluation evidence and Release Gate enforcement
- Cross-provider trace migration and multi-backend fan-out certification

Each deferred extension must reuse this contract rather than introduce a second identity, Run or idempotency model.
