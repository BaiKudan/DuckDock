# ADR-0213: 采集内核从过程遥测转向成果物知识服务

**Status**: Proposed
**Date**: 2026-08-10

## Context

DuckDock 原采集内核依赖外部 agent harness 通过定时任务（cron）上报本地工作历程（WorkTrace）与对话过程，即「过程遥测」。这条链存在三个结构性问题：

- **与宪法张力**：内容默认关闭（[ADR-0209](0209-metadata-first-content-policy.md)）要求不采集 CoT/对话，而过程遥测的价值恰在过程内容，两者互相拉扯。
- **信噪比低**：采集全量过程，真正有复用价值的是少数成果物。
- **链路空转**：交接发起的 `CollectionJob` 无消费者，永远停在 `PENDING`（见审查报告）。

同时，平台按阿里云 agent loop + Langfuse 新增的 **agent harness 管理**能力（fleet/遥测/Agent Package 注册/发布门禁/评测中心）是有效、要保留的价值面。

## Decision

**替换采集内核，保留 harness 管理面。**

1. **新核心 = 成果物知识服务**。用户对满意成果物做**显式标记**（唯一入库触发，无自动判定），经 MCP / CLI / REST 三通道之一**事件触发投递**。
2. **存储分层遵循 [ADR-0202](0202-business-telemetry-storage-boundary.md)**：原件存 MinIO、元数据存 MySQL，二者为事实源；**LanceDB 向量索引定位为可从原件重建的派生索引，非事实源**。
3. **向量栈**：新引入 qwen 多模态 embedding + rerank，仅用于知识服务；评测中心既有 semantic clustering 不动。范围含图文文档（含内嵌图表），纯视频/代码仓库暂不纳入。
4. **检索**：embedding 召回 → rerank → 返回片段 + 原件链接。第一阶段人用搜索页，第二阶段 agent 用 MCP `search_knowledge`，共用同一 service。
5. **可见性与治理**：知识制品三层可见性（个人私有默认 / Namespace 共享 / 全局）；个人库投递即入库，提升到共享/全局才审批。**复用 Experience Asset 的治理骨架（版本化 + 溯源 + 审计 + 激活审批），但新建独立 `KnowledgeArtifact` 实体，不共用**。
6. **退场**：v1 定时采集（reporter/WorkTrace/collection-job/报告分析）及其纯展示面（个人工作台、agent-overview、资产中心）退场。
7. **改造**：交接语义从「交接工作历程」改为「交接知识制品库」，复用其 FSM + 四眼审批骨架。
8. **保留但冻结**：GA 生产授权（spec 016）与 demo 阶段目标不符，冻结不再投入。

## Considered Options

- **并存三支柱**（采集 + 知识服务 + harness 管理）：否决 —— 已有三代并存拖垮维护（本次「好久没回顾」即其症状），再加只会三者俱废。
- **LanceDB 作事实源**：否决 —— 违反 ADR-0202 存储边界，且与现有「向量算完即弃、只存 digest」的既定实现（`SemanticEmbeddingEvidence`）哲学冲突；派生索引可重建、可换 provider（[ADR-0205](0205-langfuse-provider-port.md)）。
- **自动判定满意度**：否决 —— RAG 质量 = 入库质量，自动判定不可靠，且违反内容默认关闭（ADR-0209）；显式标记同时满足隐私原则。

## Consequences

- 一批 v1 功能退场，仓库瘦身；退场模块中已知逻辑缺陷（交接 FSM、跨租户读等）按「记录不修」处理，除非落在改造/保留边界内。
- 知识服务是新建工作：成果物文件一等化、内容提取（PDF→text）、ingest 向量化、持久化向量索引与检索 —— 现有 `artifact_service`/reporter 鉴权/作业队列骨架可复用。
- Skills 注册表 v1 与 Agent Package Registry v2 的两代重叠去留仍未决（见审查报告开放项），其发布门禁绕过缺陷的修复优先级取决于该决策。
- 本 ADR 为方向决策，尚未派生 spec；正式实施前须按宪法 Governance 补 spec/plan/tasks/data-model/contracts。

## Verification

- 落地前新建 `specs/<NNN>-knowledge-service/`，补 data-model（KnowledgeArtifact / 可见性 / 派生索引重建）与 contracts（投递 API、检索 API、MCP tool schema）。
- **派生索引可重建性**：删除 LanceDB 后能从 MinIO 原件全量重建的集成测试。
- **可见性边界**：agent 以用户身份检索不得越 Namespace / 私有边界的授权测试。
- 遵循既有质量门（真 MySQL 集成、Schema/Golden、先红后绿）。
