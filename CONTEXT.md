# DuckDock — Context（Ubiquitous Language）

DuckDock 是一个 **agent harness 管理平台 + Agent 成果物知识服务**。本文件是项目术语表，只收录本项目特有、且容易混淆的概念；实现细节不在此处（见 [docs/architecture.md](docs/architecture.md) 与 `specs/`）。

> 定位转向记录见 [ADR-0213](docs/adr/0213-collection-core-to-knowledge-service.md)：采集内核从「过程遥测上报」转向「成果物知识服务」。审查快照见 [docs/reviews/2026-08-10-project-review.md](docs/reviews/2026-08-10-project-review.md)。

## 产品两面

**Agent Harness 管理**：
对接、注册、治理外部 agent 运行框架的整套能力 —— fleet 握手/心跳、遥测（OTLP/Langfuse）、Agent Package 注册、发布门禁、评测中心。参照阿里云 agent loop + Langfuse 构建，是**保留**的既有价值面。
_Avoid_: 控制平面（旧称，已含混）；记录器（指被替换的采集内核）

**知识服务 / Knowledge Service**：
DuckDock 的**新核心**：归档用户在 harness 里产出的满意成果物，向量化后提供语义检索。
_Avoid_: RAG（实现手段，非产品名）；资料库

## 知识服务

**知识制品 / KnowledgeArtifact**：
用户显式标记为「满意」并投递进 DuckDock 归档的一份成果物。第一版限可提取文本/图文的文档（PDF/doc/md/pptx 及其内嵌图表）。知识服务的中心实体。
_Avoid_: 产出物、成果物（口语可用，实体名统一用「知识制品」）；Asset（那是采集来的 AI 资产，见下）

**满意标记 / Curation Mark**：
用户对一份成果物做出的「值得归档」的显式意向。入库的**唯一**触发 —— 无自动判定。
_Avoid_: 收藏、点赞

**投递 / Ingest**：
把成果物 + 满意标记送进 DuckDock 的动作。走三通道之一：MCP `archive_artifact`、CLI、REST。事件触发，非定时。
_Avoid_: 上报（那是被替换的定时采集语义）

**派生索引 / Derived Index**：
LanceDB 中的向量索引。定位是**可从原件随时重建的加速层，不是事实源**；原件（MinIO）+ 元数据（MySQL）才是事实。
_Avoid_: 向量库、向量主库（强调它不是事实存储）

**检索 / Retrieval**：
对知识制品的语义查询：embedding 召回 → rerank 重排 → 返回命中片段 + 原件下载链接。第一阶段给人（搜索页），第二阶段给 agent（MCP `search_knowledge`），共用同一 service。

## 隔离与治理

**Namespace**：
DuckDock 唯一的资源隔离与权限作用域单元。知识制品、Skill、Package 等均归属某个 Namespace。
_Avoid_: 租户/tenant（见下）；项目、空间

**Tenant**：
仅指部署级边界（一套自托管 DuckDock = 一个企业）。**不是**库内隔离维度 —— 数据库无 `tenant_id`，隔离靠 Namespace。是否引入为独立的库内多租户概念：未决（见审查报告开放项）。
_Avoid_: 用 tenant 指代 Namespace（001 spec 的历史遗留矛盾，勿沿用）

**可见性 / Visibility**：
知识制品的三层可见范围：个人私有（默认）→ Namespace 共享 → 全局。提升到共享/全局才触发审批；个人库投递即入库。

## 需与知识制品区分的既有实体

**AI Asset / Asset**：
旧采集内核从 harness 拉取、推断归属的 AI 资产（Skill/OpenClaw/JVS 等）。属**退场**的采集面，与知识制品无关。

**WorkTrace**：
一段 agent 工作历程记录（过程遥测），被替换的采集对象。与 AgentRun 分离（[ADR-0201](docs/adr/0201-worktrace-agent-run-separation.md)）。

**Experience Asset**：
评测中心里从**失败案例**（Bad Cases）提炼、人工撰写的经验文本条目，带版本化 + 四眼激活治理。语义是「失败复盘」，**与知识制品（成功成果物）方向相反**。知识制品**复用其治理骨架，但不共用实体**。

**交接 / Handover**：
将一个人沉淀的资产/知识移交给接收人的流程（FSM + 四眼审批）。转向后语义从「交接工作历程」**改造**为「交接知识制品库」。
