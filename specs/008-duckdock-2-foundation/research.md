# Research: DuckDock 2.0 Agent Run Foundation

**Research date**: 2026-07-17

**Scope**: Foundation architecture and open-source building blocks; not a product benchmark or procurement decision.

## 1. Executive Finding

AgentLoop 类能力不是由某一家厂商凭空创造的单体技术，而是多个成熟方向的产品化组合：分布式 tracing、LLM/Agent semantic conventions、轨迹可视化、离线评测、artifact/replay、身份与租户治理、发布决策。DuckDock 的差异化价值不应是重新实现整套 trace backend，而应是把这些通用能力连接到企业资产、责任、证据、审批和发布门禁。

Foundation Implementation Phase 因此选择：

- OpenTelemetry Collector 作为遥测入口和信任边界；
- OTel GenAI conventions 与 OpenInference 作为外部语义来源；
- Langfuse 作为可替换 trace provider 的首选候选，而非核心依赖；
- ATIF 作为未来离线轨迹 artifact 格式；
- DeepEval 作为未来可替换评测引擎候选；
- DuckDock MySQL 只保存治理 metadata、不可变版本引用、Run 索引和决策证据。

## 2. Primary Sources

| Project/topic | Primary source | Relevant capability | License/maturity note |
|---|---|---|---|
| OpenTelemetry Collector | https://opentelemetry.io/docs/collector/ | vendor-neutral receive/process/export telemetry pipeline | CNCF/OpenTelemetry ecosystem; production-oriented collector architecture |
| Handling sensitive data in OTel | https://opentelemetry.io/docs/security/handling-sensitive-data/ | attribute removal, transformation and collection-side controls | Security guidance; implementation still requires project-specific allowlists |
| OTel GenAI semantic conventions | https://github.com/open-telemetry/semantic-conventions-genai | GenAI/Agent telemetry vocabulary | Apache-2.0; as of research date the standalone repo has no formal releases and notes schema evolution work |
| OpenInference specification | https://arize-ai.github.io/openinference/spec/ | LLM/Agent spans, messages, tools and evaluations over OTel | Treat as external versioned schema |
| OpenInference repository | https://github.com/Arize-ai/openinference | instrumentation and semantic convention implementations | Apache-2.0 |
| Langfuse native OTel integration | https://langfuse.com/integrations/native/opentelemetry | ingest/query/visualize LLM traces through OTel | Optional provider; integration must remain adapter-scoped |
| Langfuse repository | https://github.com/langfuse/langfuse | trace UI, sessions, scores, datasets, self-hosting | MIT for core, with separately licensed enterprise folders; verify deployed feature license |
| Langfuse ClickHouse infrastructure | https://langfuse.com/self-hosting/deployment/infrastructure/clickhouse | production trace storage architecture | Confirms trace workload belongs outside DuckDock MySQL |
| DeepEval repository | https://github.com/confident-ai/deepeval | open-source LLM/Agent evaluation library | Apache-2.0; Python engine suitable for replaceable worker adapter |
| DeepEval Agent evaluation docs | https://deepeval.com/docs/getting-started-agents | task completion and trajectory-oriented agent evaluation | Engine capability, not governance system of record |
| ATIF specification | https://www.harborframework.com/docs/agents/trajectory-format | portable agent trajectory JSON model | Best suited to artifact exchange/replay, not live transport |
| Harbor repository | https://github.com/harbor-framework/harbor | agent evaluation harness and ATIF ecosystem | Apache-2.0 |

Licenses and project maturity can change. Pin versions and re-check license boundaries before production packaging.

## 3. Findings by Capability

### 3.1 OpenTelemetry Collector

The Collector already provides the generic “ingest -> process -> export” pipeline DuckDock would otherwise have to rebuild. Its receiver/processor/exporter model is a good fit for OTLP authentication, trusted resource enrichment, attribute deletion, normalization, sampling and multi-sink routing.

**Decision**: Use Collector as the telemetry trust boundary in a later Sprint. Harness-provided `namespace_id`, `runtime_id`, environment and governance tags are untrusted until mapped from authenticated receiver credentials or a server-maintained registration.

**Not delegated to Collector**: tenant membership, asset ownership, approval, evidence lifecycle and release decisions remain DuckDock responsibilities.

### 3.2 OTel GenAI conventions and OpenInference

Both efforts aim to standardize Agent/LLM spans and attributes, but the vocabulary is still evolving and can contain highly sensitive content. OpenInference has broad instrumentation coverage; OTel GenAI conventions align with the wider OTel ecosystem.

**Decision**: Accept both through explicit, versioned normalizers. Store source schema/version and normalizer version on Run/artifacts. Map only a small stable subset into DuckDock index fields; preserve raw telemetry only in the external trace backend.

**Rejected**: Creating one MySQL column for each external semantic attribute. This would couple database migrations to unstable external schemas and invite sensitive data into the business store.

### 3.3 Langfuse

Langfuse provides much of the trace/session/score/dataset experience users associate with an AgentLoop observability product, including self-hosting and native OTel paths. Its production architecture uses a trace-oriented data stack rather than a conventional business MySQL schema.

**Decision**: Treat Langfuse as an optional first-party adapter candidate. DuckDock keeps a `TelemetrySink` configuration and `TraceBackendRef`, while users open raw traces in Langfuse. The core must start and function without Langfuse packages or services.

**Rejected**: Mirroring Langfuse trace tables into DuckDock MySQL or letting Langfuse project IDs become DuckDock tenant IDs. Both approaches make replacement and tenant governance unsafe.

### 3.4 DeepEval

DeepEval supplies reusable evaluation metrics and Agent evaluation workflows. It is suitable for execution inside Celery workers using DuckDock-owned dataset/evaluator definitions and immutable trajectory artifacts.

**Decision**: Defer implementation to a later Sprint behind `EvaluationEnginePort`. Persist evaluation identity, inputs, metric version, result and evidence in DuckDock; keep engine-specific runtime details in adapter metadata.

**Rejected**: Calling DeepEval synchronously in the Run completion request. Evaluation can be expensive, fail independently and require retries or human review.

### 3.5 ATIF and Harbor

ATIF provides a portable representation for complete Agent trajectories and Harbor provides an evaluation harness around such artifacts. It complements OTLP rather than replacing it: OTLP is optimized for live telemetry delivery, while ATIF is an artifact suitable for replay, export and reproducible evaluation.

**Decision**: “OTLP live, ATIF archive.” Foundation implementation adds `AgentRunArtifact` plus `TrajectoryCodecPort`; later work writes ATIF bytes to MinIO and records checksum/schema/completeness in MySQL.

**Rejected**: Using ATIF JSON as a live queue payload or storing it in a MySQL JSON column. Large trajectories create hot-path latency, retention and privacy problems.

## 4. What DuckDock Should Build vs Reuse

| Capability | Build in DuckDock | Reuse/adapter |
|---|---|---|
| Tenant, Runtime and Asset ownership | Yes; core differentiator | No external source of truth |
| Immutable Deployment/Run governance index | Yes | External IDs may be referenced |
| OTLP protocol and trace pipeline | No | OTel SDK/Collector |
| Agent semantic instrumentation | No | OpenInference and OTel GenAI conventions |
| Raw trace storage and waterfall UI | No | Langfuse or another backend |
| Portable trajectory schema | Adapter only | ATIF |
| Evaluation algorithms | Adapter only | DeepEval and alternatives |
| Dataset/evaluator policy and approvals | Yes, later | Engines execute policy |
| Evidence chain and release gate | Yes; core differentiator | Consume immutable results |

## 5. Data Placement Decision

| Data | Primary location | DuckDock MySQL representation |
|---|---|---|
| Raw spans/events | Trace backend | trace ID, provider ref, status only |
| Prompt/completion/tool payload | Trace backend or encrypted artifact policy | none in Foundation implementation |
| ATIF trajectory | MinIO | URI, checksum, schema, sensitivity, completeness |
| Runtime/deployment ownership | DuckDock MySQL | full governance record |
| Session/Run control state | DuckDock MySQL | metadata, status, timing, aggregate counts |
| Evaluation artifact | MinIO later | immutable result/evidence metadata later |
| Release decision | DuckDock MySQL | candidate-pinned decision and evidence refs later |

## 6. Trust and Privacy Conclusions

1. Instrumented applications can forge resource attributes; tenant/runtime identity must be injected or rewritten by authenticated infrastructure.
2. Bearer authentication proves possession of a credential, not cryptographic signing of each envelope. Record `trust_level=CHANNEL_AUTHENTICATED` and `trust_source=REPORTER`; only a successful verifier may produce `PRODUCER_ATTESTED`.
3. Semantic conventions can carry prompt/message/tool content. Attribute allowlists and redaction must run before export, and DuckDock's control envelope must reject these fields entirely in Foundation implementation.
4. A trace ID is only a correlation key; it is not proof that an external trace exists or belongs to the same tenant. `TraceBackendRef` confirmation is an asynchronous adapter operation.
5. Release decisions require immutable evidence pinned to the exact candidate Deployment revision. “Latest evaluation in Namespace” is not sufficient provenance.

## 7. Alternatives Considered

### A. Extend WorkTrace into a full execution trace

**Rejected**. WorkTrace is a human/governance summary and participates in current report/evidence workflows. Adding span trees, attempts and raw content would break semantics, retention and compatibility. An optional link from AgentRun is safer.

### B. Fork Langfuse as the DuckDock backend

**Rejected**. It accelerates trace UI but moves tenant, release and asset concepts into a vendor data model and creates operational coupling to its full stack. An adapter captures most benefits with less lock-in.

### C. Store all traces in MySQL first, migrate later

**Rejected**. This creates immediate scale, indexing, retention and privacy debt and conflicts with DuckDock's business-store boundary.

### D. Use Kafka as the first foundation component

**Deferred**. Foundation implementation needs atomic business events more urgently than a new infrastructure dependency. A MySQL transactional outbox can later publish to Kafka without changing domain transactions.

### E. Trust client-supplied Namespace/Runtime attributes

**Rejected**. It permits cross-tenant poisoning and false provenance. Identity must be derived from ReporterCredential for control envelopes and authenticated Collector mappings for OTLP.

### F. Enforce runtime evaluation in Release Gate immediately

**Rejected for Foundation implementation**. Without immutable Deployment, Run and evaluation provenance, enforcement would create false confidence. First establish candidate-pinned evidence primitives, then change Gate behavior with explicit tests and migration.

## 8. Sprint Sequencing Recommendation

- **Foundation implementation**: tenant ownership, immutable Deployment/Run index, trusted metadata envelope, provider Ports, artifacts index, transactional outbox.
- **S3-S4**: OTel Collector reference pipeline, schema normalizers, Langfuse adapter, Pack/ATIF compatibility and trace reconciliation.
- **S5-S6**: ATIF lifecycle extensions, DeepEval adapter, dataset/evaluation/experiment models and comparisons.
- **S7-S8**: candidate-pinned evaluation evidence, release gates, human review and Promotion/Canary/Rollback UI.

This sequence lets DuckDock gain AgentLoop-like depth while preserving its more valuable role: enterprise ownership and decision control across heterogeneous Agent harnesses.
