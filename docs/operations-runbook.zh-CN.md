# DuckDock 运维手册

## 1. 部署目标

当前部署模型按“单台/少量服务器 + Docker Compose”设计。生产入口以 [`docs/production-deployment.md`](./production-deployment.md) 和 `scripts/prod.sh` 为准；本文覆盖本地/内网运维、组件管理、排障和备份恢复原则。

核心服务：

- MySQL 8：业务数据库。
- Redis：Celery 和短期队列状态。
- MinIO：Registry 包、Reporter 上传包、分析结果制品。
- FastAPI backend：业务 API。
- Celery worker：扫描、发布、后台任务。
- Celery beat：周期任务、lease/reaper 和 GC 调度；生产/开发都应只有一个 beat。
- Frontend：React/Vite 前端。

可选组件：

- Analysis Worker：专属 OpenClaw / Worker，用于异步分析 Reporter 上传包。
- Langfuse：Clinic / LLM trace 观测组件，默认不作为核心能力强制开放。
- Prometheus：抓取 DuckDock content-safe route-template metrics，并执行发布 SLO 告警规则。开发环境按需启用，生产 Compose 默认部署。

## 2. 环境变量

本地开发/验证复制模板：

```powershell
Copy-Item .env.example .env
```

生产不要使用裸 `.env` 作为事实来源。生产按下面流程生成 `.env.prod.enc`：

```bash
cp .env.prod.example .env.prod
$EDITOR .env.prod
SOPS_AGE_KEY_FILE=/etc/duckdock/duckdock-prod-age.txt sops --encrypt .env.prod > .env.prod.enc
rm -f .env.prod
bash scripts/prod.sh preflight
```

本地或生产都必须修改的关键项：

```env
SECRET_KEY=<openssl rand -hex 32>
MYSQL_PASSWORD=<strong password>
MYSQL_ROOT_PASSWORD=<strong password>
MINIO_ROOT_PASSWORD=<strong password>
MINIO_SECRET_KEY=<strong password>
BACKEND_BASE_URL=https://duckdock.example.com
FRONTEND_BASE_URL=https://duckdock.example.com
MINIO_PUBLIC_ENDPOINT=minio.duckdock.example.com
CORS_ORIGINS=["https://duckdock.example.com"]
```

关键说明：

- `DATABASE_URL` 是后端实际使用的 MySQL 连接串。
- `MINIO_ENDPOINT` 是后端访问对象存储的地址。
- `MINIO_PUBLIC_ENDPOINT` 是签名 URL 中暴露给 Reporter 和 Analysis Worker 的地址，必须被它们访问到。
- `BACKEND_BASE_URL` 用于生成 registry/SOP 中的 API 地址。
- `FRONTEND_BASE_URL` 用于前端跳转和 SSO 回调。

本地自定义域名示例：

```text
127.0.0.1 duckdock.test
BACKEND_BASE_URL=http://duckdock.test:8990
FRONTEND_BASE_URL=http://duckdock.test:5175
MINIO_PUBLIC_ENDPOINT=duckdock.test:9000
```

如果本机开启系统代理，需要把 `duckdock.test`、`127.0.0.1`、`localhost` 加入代理绕过列表。

## 3. 启动核心服务

本地/dev 核心服务启动：

```powershell
docker compose up -d mysql redis minio backend worker beat frontend
```

生产核心栈启动：

```bash
export SOPS_AGE_KEY_FILE=/etc/duckdock/duckdock-prod-age.txt
bash scripts/prod.sh up
```

查看状态：

```powershell
docker compose ps
```

健康检查：

```powershell
curl http://127.0.0.1:8801/health
curl http://127.0.0.1:5174
curl http://127.0.0.1:9000/minio/health/live
```

完整 DuckDock 2.0 dev 组件：

```bash
docker compose --profile observability --profile operations up -d
curl http://127.0.0.1:9090/-/ready
```

本地开发也可以使用脚本：

```powershell
.\scripts\dev-start.ps1
.\scripts\dev-status.ps1
.\scripts\dev-stop.ps1
```

## 4. 组件管理

### 4.0 Prometheus 与 Release Readiness

```bash
docker compose --profile operations up -d prometheus
curl http://127.0.0.1:8801/metrics
curl http://127.0.0.1:9090/-/ready
```

管理员登录后在 `/operations` 查看 SLO、incident、恢复演练和 Release Readiness。门禁只读取现有不可变证据；不会创建发布、修改业务状态或读取 prompt/output。完整本地复验：

```bash
bash scripts/verify-operations-recovery.sh
backend/.venv/bin/python backend/scripts/export_openapi_v2.py --check
backend/.venv/bin/python backend/scripts/verify_ga_candidate_dev.py
```

`READY_WITH_GAPS` 表示只有可见 warning；`BLOCKED` 表示至少一个迁移、契约、身份、Outbox、恢复、SLO、incident 或 Prometheus 硬门禁失败。禁止通过直接改 MySQL evidence/status 绕过门禁。

### 4.1 Langfuse

Langfuse 是可选观测组件。管理员可以在 DuckDock 控制台“组件管理”中启动或停止。

命令等价于：

```powershell
docker compose --profile observability up -d postgres clickhouse langfuse-db-init langfuse-minio-init langfuse-worker langfuse-web
docker compose --profile observability stop langfuse-web langfuse-worker clickhouse postgres
```

组件说明：

- `postgres`：Langfuse 自己的数据存储，不是 DuckDock 业务库。
- `clickhouse`：Langfuse 事件和 trace 分析库。
- `langfuse-db-init`：一次性初始化 Langfuse database。
- `langfuse-minio-init`：一次性初始化 Langfuse bucket。
- `langfuse-worker`：Langfuse 后台处理。
- `langfuse-web`：Langfuse Web 控制台。

这组服务与 DuckDock 核心业务解耦。核心功能不依赖 Langfuse。

Web/Worker、Python SDK 与 ClickHouse 使用经过真实验收的精确版本。升级前必须
执行 `bash scripts/verify-langfuse-upgrade.sh`；备份、候选环境、v5 新适配器
和回滚要求见
[`langfuse-upgrade-runbook.zh-CN.md`](./langfuse-upgrade-runbook.zh-CN.md)。

#### Annotation Queue 派发与恢复

Eval Hub 只绑定 Langfuse 中已经存在且至少配置一个 Score Config 的 Annotation
Queue。绑定会冻结 Queue ref 和 Score Config ID 快照；不要通过直接改 DuckDock
MySQL 的方式“跟随”远程配置变更。配置确需变更时，应新建 Queue、完成候选版本
兼容门禁，再建立新的 DuckDock binding。

操作路径为 `/eval-hub` → `Trace2Dataset` → `Langfuse 人工标注队列`：

1. 选择 Provider Queue 并绑定。
2. 选择一个不可变策展批次并派发。DuckDock 先提交本地 intent，页面初始显示
   `PENDING` 属正常状态。
3. Beat 默认每 10 秒扫描，Worker 成功后显示 `SYNCED n/n`。失败达到上限后显示
   `FAILED`，先修复 Langfuse/网络/Score Config 漂移，再使用页面“重试”。
4. 标注人员在 Langfuse 完成后使用“对账 Langfuse 状态”。`COMPLETED` 只更新
   进度；仍必须在 DuckDock 策展审核队列中独立批准或拒绝。

关键参数为 `ANNOTATION_QUEUE_LEASE_SECONDS`、
`ANNOTATION_QUEUE_MAX_ATTEMPTS`、`ANNOTATION_QUEUE_BASE_RETRY_SECONDS`、
`ANNOTATION_QUEUE_MAX_RETRY_SECONDS`、
`ANNOTATION_QUEUE_DISPATCH_SCHEDULE_SECONDS` 和
`ANNOTATION_QUEUE_DISPATCH_BATCH_SIZE`。排障时只记录 dispatch/Queue/Observation/
provider item ref、状态、计数和 error code，不得复制 annotation value、correction、
score detail 或 Observation IO 到工单和 DuckDock 日志。

#### Promotion 推荐与故障处理

操作路径为 `/eval-hub` → `Trace2Dataset` → `标注驱动 Promotion 策略`：

1. 创建策略身份；为新版本选择一个已冻结的 Annotation Queue binding 和其中
   一个 Score Config，配置 NUMERIC/BOOLEAN/CATEGORICAL 质量条件。
2. 可选设置 `OBSERVATION_NAME`、`OBSERVATION_TYPE` 或 `ENVIRONMENT` 的最低
   多样性桶数。版本一经创建不可修改；规则变化必须新增版本。
3. 只选择 `SYNCED` 且 `completed_count=item_count` 的派发运行评估。结果为
   `RECOMMENDED` 或 `BLOCKED`，原因码会区分缺分、质量未达标、缺少多样性
   metadata 和多样性不足。
4. `RECOMMENDED` 只是待人工处理的建议。仍须回到策展批次完成独立
   APPROVED/REJECTED 审核，批准后才可物化 DatasetVersion。

评估返回 409/503 时按以下顺序排查：派发是否 100% 完成、绑定 Queue 的 Score
Config IDs 是否漂移、Score 是否为 `ANNOTATION` source 且同时绑定正确
Queue/Config/Trace/Observation、评分 data type 是否匹配、所选多样性 metadata
是否存在。禁止通过修改 MySQL outcome/count/digest 绕过失败；修复 provider
数据或配置后，在同一不可变版本上使用新的 Idempotency-Key 重新评估，或为
新规则创建新版本。

Promotion 表、Audit 和 Outbox 只允许写入布尔结果、原因码、计数和 SHA-256
摘要。排障不得把原始 score value、comment、correction 或 Observation IO
复制进 DuckDock 数据库、日志、工单和截图。

#### 跨批次 Golden / Bad Case 路由

操作路径为 `/eval-hub` → `Trace2Dataset` → `跨批次 Golden / Bad Case 路由`：

1. 选择一个启用了 metadata-only 多样性维度的 Promotion 版本，创建路由策略
   与不可变版本。`NONE` 版本不能作为聚类来源。
2. 多选一至二十个来自该精确 Promotion 版本的运行。来源 Observation 必须互不
   重叠；混用版本或重复来源会 fail closed。
3. 指定两个不同且已同步 Langfuse 的 Dataset，分别作为 Golden 与 Bad Case
   目标，并执行 `CLUSTER_ROUND_ROBIN`。
4. 质量通过项进入 Golden 候选，已有评分但质量未通过项进入 Bad Case 候选；
   缺评分或缺多样性摘要只记录为 `EXCLUDED`。
5. 任一侧低于版本最低数量时结果为 `BLOCKED`，两个批次都不创建。`ROUTED`
   会创建两个独立 `PENDING_REVIEW` 批次，必须分别审批；任何一侧都不会自动
   APPROVED 或物化。

排障先核对 policy version pin、Promotion run 的版本与 Observation 去重、两个
Dataset 的 Namespace/provider 同步状态，以及 Golden/Bad Case minimum 是否能
满足。路由只读取 DuckDock 已保存的 quality/diversity 布尔与 digest，不请求
Langfuse 原始 score；不要通过修改 run item、count 或 digest 绕过 `BLOCKED`。
Audit/Outbox 仅允许 exact pins、lane/selected、引用、计数、原因码和 SHA-256
摘要，不得加入 score value、annotation comment/correction 或 Observation IO。

#### 失败分类与 Experience 候选

操作路径为 `/eval-hub` → `Trace2Dataset` → `失败分类与 Experience 候选`：

在需要按真实失败语义而非历史 metadata 桶归类时，先完成同页的
`真实语义失败聚类`：

1. 配置独立 embedding 端点并显式开启 `SEMANTIC_EMBEDDING_ENABLED=true`。
   自托管 Hermes/llama.cpp 可使用 `openai-compatible-local` profile；生产密钥
   必须由 secret manager 注入。
2. 创建策略和不可变版本，精确绑定 Case Routing 版本，并冻结模型引用、维度、
   cosine 阈值、最小簇大小与输入上限。模型、维度或阈值变化必须新建版本。
3. 选择该版本的 `ROUTED` 运行。系统只对 selected Bad Case 的精确
   Trace/Observation 来源做瞬时 Langfuse IO 读取和 embedding，DuckDock 仅保存
   digest、相似度和簇成员。
4. 创建 Failure Taxonomy 版本时可选中该语义版本；之后 Experience 提取必须同时
   选择同一路由运行对应的精确语义运行。留空则继续使用 metadata 兼容路径。

排障先核对 Langfuse project key/Observation source、embedding endpoint/model、
返回维度和冻结版本。Provider 不可用或响应畸形会 fail closed 且不创建 run；
不得把 Observation IO、embedding vector、API key 复制到日志、工单、Audit 或
Outbox。紧急降级可关闭 `SEMANTIC_EMBEDDING_ENABLED`，既有摘要仍可查询，新的
metadata-only Taxonomy 版本仍可运行。

#### 语义簇质量与漂移门禁

操作路径为 `/eval-hub` → `Trace2Dataset` → `语义簇质量与漂移门禁`：

1. 创建策略身份并冻结不可变阈值版本：最低 pairwise assignment agreement、
   最大簇数量变化、最大合格簇比例下降和最大 centroid similarity 下降。
2. baseline 与 candidate 必须来自同一 Case Routing run，并拥有完全相同的
   source refs/content digests。门禁阶段只读取 DuckDock 已冻结摘要，不调用
   Langfuse、Hermes 或 embedding endpoint。
3. `PASS` 表示四项指标均未越界；`DRIFTED` 表示候选不可聚类或至少一项越界；
   baseline 不可用、来源或内容不一致时为 `INCONCLUSIVE`，不得作为放行依据。
4. 模型、维度、聚类阈值或质量阈值变化都要创建新版本；禁止改写历史运行或
   comparison。比较结果、原因码和 reproducibility digest 都是不可变证据。

排障先核对两个聚类运行的 Case Routing run、source count/source refs、content
digests 和 baseline 状态。不要通过修改成员归属、相似度或 digest 消除漂移；应
修复 embedding/provider 配置后生成新的候选运行。API、Audit 与 Outbox 只允许
exact pins、计数、比例、相似度和 digest，不得加入原文或向量。

1. 选择一个精确 Case Routing 版本，创建失败分类策略及不可变版本。设置 cluster
   重复条数、跨 Promotion run 数量、候选上限；孤立失败默认不纳入。
2. 选择该精确版本下一个 `ROUTED` 运行。系统只聚合 `selected=true` 且
   `lane=BAD_CASE` 的冻结 cluster digest，并分类为 `CROSS_RUN_RECURRING`、
   `SINGLE_RUN_RECURRING` 或 `ISOLATED`。
3. 无符合规则的 cluster 时运行记录为 `BLOCKED`；不得放宽已存在版本，应新建
   规则版本。成功时所有候选初始为 `PENDING_REVIEW`。
4. 审核人员通过精确 Trace/Observation 引用到 Langfuse 查看来源，再批准或拒绝
   候选。拒绝必须写明至少五个字符的原因；最终决定不可覆盖。
5. `APPROVED` 只确认该失败模式值得后续编写 Experience，不会自动生成正文、
   修改 Prompt/Skill/Memory、部署 Agent 或绕过发布门禁。

排障先核对 taxonomy version pin、来源 Case Routing outcome/version、Bad Case
selected 数量和 cluster digest。Extraction、Audit 与 Outbox 只允许策略 pin、
分类、来源引用、计数、原因码及 SHA-256 摘要；不得复制 Observation IO、score
value、annotation comment/correction。若候选已被最终评审，重复评审应返回
`409`，禁止直接修改 status/review 表。

#### Experience 版本草拟与独立激活

操作路径为 `/eval-hub` → `Trace2Dataset` → `Experience 资产与独立激活`：

1. 只能选择 `APPROVED` 且尚未被认领的候选创建 Experience 资产；一个候选只
   对应一个资产身份，跨 Namespace 或重复认领均 fail closed。
2. 在目标资产下人工填写正文、适用边界与可选变更摘要。每次保存创建新的不可变
   版本和 content digest；不要直接修改历史版本行。
3. 只有 `DRAFT` 可提交一次激活申请。申请后状态为 `PENDING_ACTIVATION`，作者
   或申请人不能完成最终审批，必须切换到另一位具备 Namespace 写权限的成员。
4. 拒绝至少需要五个字符的原因。批准会把该精确版本标记为 `ACTIVE`，并在同一
   事务中把该资产之前的 `ACTIVE` 版本标记为 `RETIRED`；最终决定不可覆盖。
5. `ACTIVE` 是 DuckDock 控制面标签，不代表已写入 Hermes、Langfuse、Prompt、
   Skill、Memory 或 Deployment。运行时下发必须由未来独立 adapter 和回执闭环完成。

排障先核对候选最终状态、候选是否已被资产认领、版本是否仍为 `DRAFT`、请求是否
已有 review，以及审批人 ID 是否与 `created_by_user_id` / `requested_by_user_id`
不同。同人审批应返回 `409`。Audit/Outbox 只能包含资产/版本/请求/审批 ID、状态和
SHA-256 digest，不得出现正文、适用边界、申请说明或审批评语。

### 4.2 OpenClaw OTLP 持久队列

启用 Langfuse exporter 的 edge Collector：

```bash
docker compose --profile telemetry-langfuse up -d otel-collector-langfuse
curl -fsS http://127.0.0.1:14133/
```

`otel-langfuse-queue-init` 只负责把
`otel_langfuse_queue_data` 卷初始化为 pinned Collector UID 可写；Collector
把已经过 metadata-only redaction 的 batch 写入该卷。不得把该卷作为
普通日志目录浏览、复制或上传。

事故排查顺序：

1. 检查 Collector health 和 exporter queue size/capacity。
2. 检查 enqueue/send failed spans，区分“未进入 WAL”和“仍在重试”。
3. 检查下游 Langfuse 与网络恢复后队列是否下降。
4. 用 TraceBackendRef confirmation 判断 provider 是否真正可查询；WAL ack
   不能替代 provider confirmation。
5. 发生磁盘耗尽、损坏恢复或人工删除卷时，必须登记 transport loss，
   把受影响证据标为不完整，不能把它写成正常采样。

开发环境可从 loopback 查看队列指标：

```bash
curl -fsS http://127.0.0.1:18888/metrics |
  grep otelcol_exporter_queue
```

重启恢复演练：

```bash
ops/otel-collector/durable-replay-smoke.sh
```

该命令会清理并重建**测试用** Collector 队列卷、强制 KILL 测试
Collector，并验证无需第二次 producer 请求即可重放。不要在持有生产
积压的主机上运行。

### 4.3 Generic OTLP 双出口与 quarantine

Generic bridge 仍是一 Runtime 一 Collector。启用前必须登记同一
Runtime/Namespace 下的 `execution.write` ReporterCredential 与 ACTIVE
TelemetrySink，并通过 secret manager 设置全部 `DUCKDOCK_GENERIC_*`：

```bash
docker compose --profile telemetry-generic up -d \
  otel-generic-queue-init otel-collector-generic
curl -fsS http://127.0.0.1:14135/
curl -fsS http://127.0.0.1:18889/metrics |
  grep otelcol_exporter_queue
```

provider 和 DuckDock projection 使用独立 persistent queue。provider
不可用时，DuckDock 可先形成 Run/TraceRef；provider queue 必须保持 pending
并在恢复后重放。不要重复提交 producer 请求来“修复”队列，也不要把
Collector queue ack 当成 provider confirmation。

排查 `GenericTraceProjection` 时只使用 `status`、`reason_code`、trace/run
ID 与时间/count 摘要。`QUARANTINED` 表示多 root、复用 trace 或 mapping
信号冲突；不得人工把 Run 改到另一个 Runtime/Namespace。先复现实验：

```bash
ops/otel-collector/generic-otlp-smoke.sh
```

该 smoke 不使用真实 provider credential，覆盖 receiver 401、治理身份覆盖、
双出口、metadata-only/Secret Canary 和 provider outage WAL 恢复。

### 4.4 Pack/ATIF 导入与隔离

Pack 导入固定使用 `execution.write` ReporterCredential，顺序为：

1. `POST /api/v2/reporter/pack-imports` 严格校验 manifest 并取得签名 PUT；
2. Reporter 把同一 checksum 的 ZIP 上传到返回的 content-addressed MinIO key；
3. `POST .../{public_id}/finalize` 执行全包/逐 payload 校验并关联既有 Run；
4. 轮询 `GET .../{public_id}`，只把 `IMPORTED`/`IMPORTED_PARTIAL` 视为已登记。

`PENDING_VALIDATION` 表示对象尚未到达或存储暂不可用，可安全重试同一
Pack ID/同一摘要；同一 Pack ID 改摘要会进入 `QUARANTINED`。过期上传、
checksum/size 错误、非法 ZIP、未知 ATIF 为 `REJECTED`；Run/trace 冲突、
未授权内容或 Secret Canary 为 `QUARANTINED`。不要直接修改状态或移动
Artifact 到另一个 Runtime。

排查只查看 `last_error_code`、checksum、size/count 与 correlation ID，禁止
把 ZIP、ATIF 内容、entry 名称或异常正文写入工单/日志。默认安全边界：

```env
PACK_IMPORT_MAX_COMPRESSED_BYTES=67108864
PACK_IMPORT_MAX_UNCOMPRESSED_BYTES=268435456
PACK_IMPORT_MAX_ENTRY_BYTES=67108864
PACK_IMPORT_MAX_PAYLOADS=64
PACK_IMPORT_MAX_COMPRESSION_RATIO=100
PACK_IMPORT_MAX_MANIFEST_BYTES=131072
PACK_IMPORT_PENDING_TTL_SECONDS=86400
PACK_BATCH_REPLAY_WINDOW_SECONDS=604800
```

Foundation 仅支持 metadata-only ATIF v1.0–v1.7。即使 manifest 自报 policy/
redaction receipt，在 Namespace 级内容授权模型落地前也不能导入 message、
reasoning、Tool I/O 或 observation。0038 同时支持兼容的单对象 PUT 和
可恢复 multipart：只信任 MinIO `list_parts` 返回的 part/ETag/size，完成时
要求编号连续、分片大小精确，再执行整包 SHA-256 与原有 preflight。

批量入口 `/api/v2/reporter/pack-import-batches` 每项独立产生 durable receipt；
乱序收据可以先保存，但 `ack_cursor` 只跨过从 1 开始的连续收据。Adapter
必须先把响应写入本地 WAL 再推进本地 cursor。`retryable` 项没有收据、不会
推进 cursor；`rejected` 永久收据可推进。重放窗口默认 7 天。

ATIF export 只读取已登记且重新通过 size/SHA/ATIF/content-policy 校验的
trajectory Artifact。Evaluation replay 只发送低敏 Artifact 引用 Outbox
事件，并在发送前重新校验对象 size/SHA/Secret Canary；它不等同于重新运行
Evaluator，后者仍需由语义评估能力提供。

真实 MinIO 验证：

```bash
cd backend
RUN_MINIO_INTEGRATION=1 python -m pytest -q \
  tests/foundation/test_pack_atif_import.py -k real_minio
```

### 4.5 Adapter 握手与 Fleet

所有 profile 先调用 `POST /api/v2/reporter/handshakes`，能力只能来自当前
依赖探测结果。相同 nonce + 相同 descriptor 是 replay；相同 nonce 改内容
为 `409 IDEMPOTENCY_CONFLICT`。过期、credential 轮换、boot/config/capability
漂移后必须重新握手。

Fleet 管理入口为 `/fleet`，API 为
`GET /api/v2/fleet/runtimes?namespace_id=...`。重点检查：

- 未握手 Runtime 必须是 `NONE/NEVER`，不得显示认证级别或 accepted capability；
- Generic 没有 active TelemetrySink 时只能是 DD-C0/degraded；
- Hermes pilot 必须显示 `hermes-reporter` / `hermes-reporter-pilot`，只声明
  `session_control`、`run_control` 并认证为 DD-C1；
- Generic heartbeat 必须带 Collector status/version，非 healthy 状态降级；
- Pack 客户端只声明 `artifact_import`/`partial_loss` 时保持
  `DD-C1+ARTIFACT`；只有同时协商 `atif_export`、`resumable_upload`、
  `durable_batch_ack`、`evaluation_replay` 与 `otel_trace_correlation`
  的完整闭环才认证 DD-C3；
- `heartbeat_history` 最多返回最新 20 条 metadata-only 记录。

关键窗口：

```env
ADAPTER_HANDSHAKE_TTL_SECONDS=86400
ADAPTER_HANDSHAKE_REPLAY_WINDOW_SECONDS=604800
ADAPTER_MAX_CLOCK_SKEW_SECONDS=300
FLEET_HEARTBEAT_STALE_SECONDS=300
```

排查时只记录 opaque `rt_`/`hs_` ID、版本、状态和 reason code。不得把
Reporter token、内部数值 Namespace/Runtime ID 或 endpoint credential 放入
工单。历史动态舰队验收记录见
`specs/008-duckdock-2-foundation/evidence/s4-rt08-dynamic-fleet-g2-20260730.md`。

### 4.6 Analysis Worker

Analysis Worker 是异步分析层，可以单个或多个并行部署。

启动前先在管理员控制台创建 Worker token，然后写入：

```env
DUCKDOCK_ANALYSIS_API_BASE=http://backend:8801/api/v1
DUCKDOCK_ANALYSIS_WORKER_TOKEN=dkr_worker_xxx_xxx
DUCKDOCK_ANALYSIS_WORKER_NAME=DuckDock Compose Analysis Worker
```

本地/dev 启动：

```powershell
docker compose --profile analysis-worker up -d analysis-worker
```

生产启动：

```bash
export SOPS_AGE_KEY_FILE=/etc/duckdock/duckdock-prod-age.txt
bash scripts/prod.sh worker
```

扩容：

```powershell
docker compose --profile analysis-worker up -d --scale analysis-worker=3 analysis-worker
```

生产直接扩容时必须显式带 `--profile analysis-worker` 和解密后的临时 env；详见 `production-deployment.md` 的 Analysis Worker 章节。

`--scale` 复用同一个 token 时,吞吐会扩容,但控制台会显示为一个逻辑 Worker。若需要逐副本禁用、审计或区分模型配置,请在 `/analysis` 为每个副本创建独立 token,并用不同 env/service name 启动。

队列饱和观测：

```powershell
curl -H "Authorization: Bearer <admin-jwt>" http://localhost:8990/api/v1/analysis/queue/metrics
```

关键字段：

- `backlog_count`: `pending` + 可重试的过期租约。
- `online_worker_count`: 最近心跳仍在线的逻辑 Worker token 数。
- `expired_lease_count`: 租约已过期但尚未被下一次 lease/reaper 回收的任务。
- `queued_per_online_worker`: 每个在线逻辑 Worker 的积压量。
- `saturation_level`: `healthy` / `watch` / `saturated`。

Worker 不持有 MinIO 密钥，只通过 DuckDock API 领取任务和一次性下载/上传 URL。当前默认继续使用 DuckDock 自有 lease 协议和 Celery beat reaper；只有当分析流程演进成多步骤、长事务、需要步骤级补偿恢复时,才评估 Temporal/Prefect。

## 5. Reporter 上传链路运维

标准链路：

```text
WorkBuddy / OpenClaw / ArkClaw
  -> POST /reports/upload-sessions
  -> PUT presigned MinIO URL
  -> POST /reports/{report_id}/finalize
  -> ReportAnalysisJob pending
  -> Analysis Worker lease/download/analyze/upload/finalize
  -> MySQL materialized indexes
```

排查顺序：

1. `report_upload_sessions`
   - 是否有记录。
   - `status` 是否从 `pending` 到 `uploaded` 或 `succeeded`。
   - `actual_sha256` 和 `actual_size_bytes` 是否存在。

2. MinIO
   - `object_key` 是否存在。
   - 对象大小是否和 DB 一致。

3. `report_analysis_jobs`
   - 是否创建 pending job。
   - 是否被 Worker lease。
   - 是否 succeeded / failed / cancelled。

4. `analysis_result_artifacts`
   - 是否有五类结果文件。

5. 结构化表
   - `ai_assets`
   - `asset_ownerships`
   - `runtime_bindings`
   - `work_traces`
   - `memory_candidates`

### 5.1 Agent Session/Run rollout 与 Outbox

生产首次启用前显式配置：

```env
AGENT_EXECUTION_INGESTION_ENABLED=false
AGENT_EXECUTION_RUNTIME_ALLOWLIST=[101,102]
```

先通过 tenant contract preflight，再以 Runtime allowlist 小流量启用，最后
按需要清空 allowlist 表示允许所有已授权 Runtime。新 Session/Run start 在
全局关闭时返回 503 + `Retry-After: 30`，allowlist 不匹配返回 403；completion
始终保持可用以结束已经接收的执行。

Outbox 只保存白名单低敏摘要，单事件上限 16 KiB。系统管理员可查看：

```text
GET /api/v2/outbox/health
POST /api/v2/outbox/{event_id}/retry
```

回滚顺序：

1. 关闭 ingestion 或收紧 Runtime allowlist。
2. 停止唯一 Beat 实例，阻止新的 Outbox dispatch；不要清 Redis/Outbox。
3. 回退应用路由/版本，保留 additive schema。
4. 确认 `agent_sessions`、`agent_runs`、`agent_run_artifacts` 和
   `outbox_events` 计数未减少。
5. 修复后先恢复 worker，再恢复 Beat；只对已核对幂等语义的 FAILED 事件
   执行管理员 retry。

不要在日常应用回滚中 downgrade/drop 已部署的 Foundation 及后续 additive 表；准确迁移范围先用 `alembic current` 与 `alembic heads` 核对。不要改写终态事实、
删除 reconciliation receipt/projection、Generic OTLP quarantine 或 Pack
import quarantine。
可复验演练记录见
[`Foundation Evidence Alpha 历史技术验收包`](../specs/008-duckdock-2-foundation/evidence/g1-evidence-alpha-20260728.md)。

## 6. 备份与恢复

### 6.1 必备备份

必须备份：

- MySQL 数据卷：`mysql_data`
- MinIO 数据卷：`minio_data`
- Git repo volume：`repos_data`
- `.env`，但必须放在安全密钥库或加密备份中

可选备份：

- Redis：通常可以重建，但若队列中有关键任务，应在维护窗口停写后备份。
- Langfuse 相关卷：仅当启用观测组件并需要保留 trace 时备份。

### 6.2 恢复顺序

1. 恢复 `.env`。
2. 恢复 MySQL。
3. 恢复 MinIO。
4. 恢复 Git repo volume。
5. 启动核心服务。
6. 检查 `/health`。
7. 抽查 Registry、Reporter 上传包和分析结果下载链接。

## 7. 安全运维

- Reporter Credential 按 runtime/user/device 发放，测试完成后撤销；legacy report token 仅作运维兜底。
- Analysis Worker token 按 worker 发放，泄露后禁用 Worker。
- 不在文档、截图、Git、自动化 prompt 中保留长期 token。
- MinIO root 密钥不要下发给 Reporter 或 Analysis Worker。
- Reporter 上传包默认不包含完整会话原文。
- Analysis Worker 只处理 DuckDock 分配的对象，不直接扫描员工机器。

## 8. 常见故障

### Reporter 上传 URL 访问失败

检查：

- `MINIO_PUBLIC_ENDPOINT` 是否能被 Reporter 所在机器访问。
- 域名是否被代理劫持。
- 本地验证时是否需要 `NO_PROXY` 或代理绕过。

### finalize 失败

检查：

- 上传对象是否真的存在。
- `size_bytes` 是否一致。
- `sha256` 是否一致。
- Reporter Credential 或 legacy report token 是否属于该 runtime，且未被撤销/过期。

### 上传成功但员工看不到资产

检查：

- 分析任务是否 succeeded。
- `asset_ownerships` 是否生成。
- 分析结果里是否包含 `owner`、`owner_username`、`owner_email`、`created_by`、`actor_username` 或 `actor_email`。
- 员工账号邮箱和上报包中的 email 是否一致。

### Analysis Worker 不处理任务

检查：

- Worker token 是否有效。
- Worker 是否能访问 DuckDock API。
- Worker 是否能访问 `MINIO_PUBLIC_ENDPOINT` 生成的一次性下载 URL。
- `report_analysis_jobs` 是否处于 `pending`。

## 9. 发布前检查

```powershell
cd backend
python -m pytest

cd ..\frontend
npm run build
```

Compose 配置检查：

```powershell
docker compose config --services
docker compose --profile analysis-worker config --services
docker compose --profile observability config --services
```
