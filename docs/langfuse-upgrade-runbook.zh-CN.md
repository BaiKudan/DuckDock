# Langfuse 升级与回滚 Runbook

DuckDock 的治理数据模型与 Langfuse 数据库解耦，但 Dataset 同步、Experiment
执行、Trace 查询和 OTLP 导出依赖已注册的 Langfuse 协议。任何 Langfuse
升级都必须先通过本 Runbook，不能直接修改浮动镜像标签。

## 1. 当前接受基线

| 边界 | 接受值 |
|---|---|
| DuckDock compatibility profile | `langfuse-v4` |
| Langfuse Web / Worker | `4.1.0`，两个组件必须同版本 |
| Python SDK | `4.14.2` |
| ClickHouse | `25.12.11.4` |
| PostgreSQL / pgvector image | PostgreSQL `16.14` / pgvector image `0.8.6-pg16` |
| Redis | `7.4.9-alpine` |
| MinIO / mc | `RELEASE.2025-09-07T16-13-09Z` / `RELEASE.2025-08-13T08-35-41Z` |
| OTLP ingestion header | `x-langfuse-ingestion-version: 4` |
| Trace 查询 | Observations API v2，确认 `core,basic,metadata`；候选 `core,basic,time`；物化 `core,basic,time,io` |
| Experiment 查询 | Experiments API，`core,metadata,scores` |
| DuckDock result manifest | `langfuse-experiment-result / sdk-v4` |
| Annotation Queue | NEXT-010 独立 adapter；绑定现有 Queue/Score Config 快照；Observation item 先对账再创建；不进入采样事务 |
| Promotion evidence | NEXT-011 独立 adapter；Scores API v3 精确 `ANNOTATION`/Queue/Config/subject 过滤；质量值仅瞬时计算，多样性只读 Observation metadata |
| Golden/Bad Case routing | NEXT-012 provider-independent service；只消费 DuckDock Promotion 布尔/digest，不新增 Langfuse API 或数据库耦合 |
| Failure taxonomy / Experience candidates | NEXT-013 provider-independent service；只消费 DuckDock Case Routing lane/selected/digest/ref，不调用 Langfuse API，候选人工评审与 Provider 升级解耦 |
| Experience assets / activation | NEXT-014 DuckDock-only service；人工正文、不可变版本与四眼激活完全不调用 Langfuse API，升级/回滚 Langfuse 不改变资产或激活状态 |
| Semantic failure clustering | NEXT-015 独立 provider port；Langfuse Observation v2 `core,basic,time,io` 仅瞬时读取，OpenAI-compatible embedding endpoint 独立配置；DuckDock 只保存引用、相似度和 digest |
| Semantic cluster regression | NEXT-016 DuckDock-only 离线门禁；只比较同一 Case Routing 来源集的两个冻结聚类运行，不调用 Langfuse 或 embedding endpoint |

版本定义集中在
`backend/app/services/langfuse_compatibility.py`。Compose 和
`backend/requirements.txt` 使用精确版本；生产镜像还应在部署清单中记录
目标平台对应的 registry digest。

## 2. 升级前不可跳过的准备

1. 选择维护窗口，确认没有 `PENDING` 或 `RUNNING` 的 Langfuse Evaluation。
2. 分别备份 Langfuse PostgreSQL 数据库、ClickHouse 数据卷和 MinIO
   `langfuse` bucket。DuckDock MySQL 备份不能替代这三份备份。
3. 记录当前 Web、Worker、SDK、ClickHouse 版本及 Web/Worker image digest。
4. 在独立主机或独立 Compose project 上恢复备份副本。候选环境不得直接挂载
   生产 PostgreSQL、ClickHouse 或 MinIO volume。
5. 保留旧镜像和旧数据快照，直到升级后的保留期、查询和评估都完成验收。

版本取证命令：

```bash
curl -fsS http://127.0.0.1:3200/api/public/health
docker image inspect \
  "$(docker inspect --format '{{.Image}}' "$(docker compose ps -q langfuse-web)")" \
  --format '{{json .RepoDigests}} {{index .Config.Labels "org.opencontainers.image.version"}}'
docker image inspect \
  "$(docker inspect --format '{{.Image}}' "$(docker compose ps -q langfuse-worker)")" \
  --format '{{json .RepoDigests}} {{index .Config.Labels "org.opencontainers.image.version"}}'
docker compose exec -T backend python -c \
  'import importlib.metadata as m; print(m.version("langfuse"))'
```

备份必须通过部署环境现有的备份工具完成，并至少做一次隔离恢复演练。对
Docker named volume 直接复制之前必须停止 Langfuse Web/Worker 和对应存储，
避免获得跨 PostgreSQL、ClickHouse、MinIO 时间点不一致的快照。

## 3. v4 候选版本验收

在升级分支中同时修改以下位置：

- `LANGFUSE_IMAGE_TAG`
- `LANGFUSE_CLICKHOUSE_TAG`（仅在兼容矩阵要求时）
- `backend/requirements.txt`
- `backend/app/services/langfuse_compatibility.py`
- 本文“当前接受基线”

然后重建 backend/worker，并在候选环境执行：

```bash
bash scripts/dev.sh build backend
LANGFUSE_CANDIDATE_SERVER_VERSION=<候选版本> \
  bash scripts/verify-langfuse-upgrade.sh
```

门禁会执行以下检查：

1. 精确依赖和 compatibility profile 静态契约。
2. Clinic SDK span/generation、flush、Trace URL，以及 Observations API v2
   不含 IO 的 `core,basic,time` 候选过滤和有界
   `core,basic,time,io` 原始字符串契约。
3. 固定 Annotation Queue/Score Config fixture、Observation 幂等派发、
   `PENDING→COMPLETED` 对账和精确测试 item 清理。
4. 固定 Numeric Score Config/Promotion Queue fixture，写入一条
   `ANNOTATION` score，并通过生产 adapter 的 Scores API v3 精确过滤、类型化
   质量门槛和 metadata-only 多样性摘要回读；精确清理测试 queue item。
5. 固定两条样本 Dataset 的 create/upsert、Trace/Observation source link
   与 version pin。
6. DuckDock 真实 `LangfuseExperimentRunner` 和 exact-match score。
7. Experiments API 的 item/score materialization。
8. DuckDock result manifest schema、完整性和 SHA-256 digest。
9. 直接 OTLP v4 写入及 Observations API v2 回读。
10. Trace2Dataset adapter、metadata-only 候选查询、Annotation Queue
   binding/dispatch/reconcile、不可变批次审批/物化、
   Promotion policy/run、跨批次 Case Routing、语义失败聚类、Evaluation
   comparison、语义簇质量/漂移门禁、人工审核和 Release Gate 回归测试。

门禁创建固定的 `duckdock.compatibility.langfuse-v4` Dataset，item ID
可重复覆盖，不会不断增加样本。每次运行会保留带
`duckdock.compatibility_profile` 标记的 Experiment/Trace，作为升级审计证据。

任何检查失败，都不能更新接受基线或生产镜像。

## 4. v5 或其他大版本

不得把 `LANGFUSE_COMPATIBILITY_PROFILE` 直接改为 `langfuse-v5`。正确流程是：

1. 新增独立的 v5 adapter/factory 分支，不修改 v4 adapter 的结果语义。
2. 为 v5 定义新的 SDK、API、OTLP 和 result manifest profile。
3. 在隔离环境用相同 Dataset fixture 做双跑，并比较 DuckDock 有界摘要和
   Release Gate 结论。
4. 只有新 profile 的自动化与真实门禁全部通过后，才允许切换 Worker。
5. v4 adapter 至少保留一个回滚窗口。

DuckDock MySQL 中已经固化的 comparison、review 和 release decision 不需要
因 Langfuse 大版本升级而迁移；Langfuse 中的 Trace/Dataset/score 内容仍必须
依赖 Langfuse 自己的数据迁移和备份恢复。

生产采样、Promotion 策略版本/运行快照和待审批批次由 DuckDock MySQL 持有，
不依赖 Langfuse 内部数据库结构。NEXT-010 compatibility lane 覆盖 queue/Score
Config 读取、item reconcile-before-create、幂等 replay 和完成状态对账；
NEXT-011 lane 另行覆盖 Scores API v3 的 Queue/Config/source/subject 精确过滤、
NUMERIC 类型和值门槛以及 metadata-only 多样性摘要。provider outage 仍由
DuckDock 独立 dispatch intent、lease 和 backoff 恢复。远程队列写入不得与
DuckDock 采样事务伪装成一个原子操作；Langfuse `COMPLETED` 或 DuckDock
`RECOMMENDED` 都不得自动生成 APPROVED review。

NEXT-012 不增加远程兼容面：Case Routing 只消费已落库的 Promotion item
booleans/digests，并在 DuckDock 内生成两个现有 curation batch。Langfuse 升级
无需迁移 routing policy/run；升级门禁仍必须回归验证两个批次保持独立
`PENDING_REVIEW`，且 provider 数据不可被回填进 Audit/Outbox。

NEXT-015 新增一条显式、可关闭的远程兼容面。默认 adapter 通过公开
Observations API v2 精确读取已冻结 Bad Case 的 `io`，并把有界文本直接发送到
独立 OpenAI-compatible `/embeddings` 端点。两次远程调用之间只保留内存对象；
MySQL、Audit、Outbox 和 HTTP 响应禁止出现原文或向量。Langfuse 升级候选必须
回归测试 provider failure 的脱敏 503、精确 source/version pin、幂等重放以及
content/vector-free 持久化；embedding 模型或维度变化必须创建新策略版本，不能
原地修改旧版本。禁用 `SEMANTIC_EMBEDDING_ENABLED` 只阻止新运行，不影响已保存
的聚类摘要和 metadata-only Failure Taxonomy 兼容路径。

NEXT-016 不增加远程兼容面。离线回归门禁要求 baseline/candidate 精确绑定同一
Case Routing run、拥有完全相同的 source refs 和 content digests，并只比较冻结的
成员归属、簇数量、合格簇比例与 centroid similarity 摘要。任何 source/content
漂移、baseline 非 `CLUSTERED` 或候选不可用都 fail closed；阈值变化必须建立新的
不可变策略版本。Langfuse 或 embedding 服务升级后，应先生成新的候选聚类运行，
再执行该门禁；门禁本身不会重读 Provider IO 或重新生成向量。

## 5. 上线与回滚

上线顺序：

1. 停止接收新的 Langfuse Evaluation。
2. 完成最终一致性备份。
3. 先升级 Langfuse Worker，再升级 Web，确认两者版本完全一致。
4. 重建并启动固定 SDK 的 DuckDock backend/worker。
5. 执行完整兼容门禁。
6. 恢复 Evaluation 调度并观察失败率、partial result 和 OTLP exporter 指标。

以下任一情况立即回滚：

- health/version 不一致；
- Dataset pin 或 Experiment item 数发生变化；
- score/manifest 变为 partial；
- Observations/Experiments API 返回不兼容结构；
- Annotation Queue/Score Config 快照漂移、重复 item 或完成状态无法对账；
- Scores API v3 无法按 `ANNOTATION`/Queue/Config/subject 精确回读，或评分类型、
  metadata 字段语义变化；
- Semantic clustering 的 Observation IO 结构、embedding 维度或模型身份与冻结
  版本不一致，或任何原文/向量进入 DuckDock 持久化与传播路径；
- OTLP 持续重试或 Release Gate 结论变化。

回滚时先停止新写入，恢复旧 Web/Worker/SDK 版本，并恢复与旧版本对应的
PostgreSQL、ClickHouse、MinIO 同一时间点快照。不能让旧二进制直接连接已经
完成不可逆迁移的新数据卷。
