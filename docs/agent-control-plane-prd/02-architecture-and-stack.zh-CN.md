# 02. 架构与技术栈

## 1. 总体架构

```mermaid
flowchart LR
    subgraph FE["前端 Web Console"]
        WEB["React + Vite + TypeScript"]
        UI["Ant Design / Pro Components"]
    end

    subgraph API["DuckDock Backend"]
        FASTAPI["FastAPI REST API"]
        AUTH["Auth / IAM / RBAC"]
        ASSET["Asset Service"]
        HANDOVER["Handover Service"]
        ADAPTER["Adapter Orchestrator"]
        AUDIT["Audit / Evidence Service"]
    end

    subgraph WORKER["异步任务层"]
        CELERY["Celery Workers"]
        SCHED["Celery Beat"]
        PROBE["专有 OpenClaw 探针"]
    end

    subgraph DATA["数据与中间件"]
        MYSQL[("MySQL 8.0")]
        REDIS[("Redis")]
        MINIO[("MinIO / S3")]
        OPENSEARCH[("OpenSearch")]
        VAULT[("Vault / KMS")]
        LANGFUSE[("Langfuse")]
    end

    subgraph RUNTIME["外部 Agent 运行时"]
        OPENCLAW["OpenClaw"]
        ARKCLAW["ArkClaw"]
        WORKBUDDY["WorkBuddy"]
        JVS["Alibaba Cloud JVS"]
        CUSTOM["线下定制平台"]
    end

    WEB --> FASTAPI
    FASTAPI --> MYSQL
    FASTAPI --> REDIS
    FASTAPI --> MINIO
    FASTAPI --> OPENSEARCH
    FASTAPI --> VAULT
    FASTAPI --> LANGFUSE
    FASTAPI --> CELERY
    CELERY --> PROBE
    PROBE --> OPENCLAW
    PROBE --> ARKCLAW
    PROBE --> WORKBUDDY
    PROBE --> JVS
    PROBE --> CUSTOM
```

## 2. 强制技术栈

### 2.1 前端

- React 18+
- Vite
- TypeScript
- React Router
- TanStack Query
- Zustand
- Ant Design 5
- Ant Design Pro Components
- ECharts 或 Recharts
- Axios 或 OpenAPI 生成客户端

原则：

- 企业 B 端页面优先使用 Ant Design 的 Table、Form、Drawer、Modal、Steps、Tabs、Descriptions、Timeline、Tree、Transfer、Upload。
- 不手写复杂表格、分页、筛选、表单校验、权限菜单。
- 前端只负责展示、交互、轻量状态，不承载业务审批判断。

### 2.2 后端

- FastAPI
- Python 3.12+
- Pydantic v2
- SQLAlchemy 2 async
- Alembic
- MySQL 8.0
- Redis
- Celery
- httpx
- MinIO/S3 SDK
- OpenTelemetry
- Langfuse

MySQL 驱动建议：

- `asyncmy` 作为 SQLAlchemy async driver。
- 连接串：`mysql+asyncmy://user:password@mysql:3306/duckdock?charset=utf8mb4`

### 2.3 中间件

| 中间件 | 用途 | 是否 MVP 必须 |
|---|---|---|
| MySQL 8.0 | 主业务数据库 | 是 |
| Redis | 缓存、限流、Celery broker/result backend | 是 |
| Celery | 采集、分析、交接执行、Webhook 投递 | 是 |
| MinIO/S3 | 备份包、交接包、产物、证据快照 | 是 |
| OpenSearch | 全文检索、工作历程搜索、证据搜索 | P1 |
| Vault/KMS | 密钥、Token、备份包加密密钥 | P1，MVP 可先用 KMS 抽象接口 |
| Langfuse | LLM 判断链路观测 | P1 |
| Prometheus/Grafana | 监控告警 | P1 |

## 3. 现有 DuckDock 迁移判断

当前项目已有 FastAPI、React/Vite、Redis、Celery、MinIO、Langfuse、Git bare repo、IAM、Skill、治理、审计等基础能力。

需要按新 PRD 调整：

| 当前能力 | 保留方式 | 调整点 |
|---|---|---|
| Skill / SkillVersion | 作为 AIAsset 的一种资产类型保留 | 增加 RuntimeBinding、Owner、Evidence |
| Namespace | 保留 | 升级为组织/项目/客户隔离单元 |
| IAM / RBAC | 保留 | 增加交接、探针、敏感证据权限 |
| Audit | 保留 | 扩展为证据链和接管动作审计 |
| Scanner / Sandbox / ReleaseGate | 保留 | 从发布门禁扩展为资产健康检查 |
| Clinic | 保留 | 用于 Skill/Agent 质量评分 |
| PostgreSQL | 替换 | PRD 要求 MySQL，需制定迁移脚本 |
| pgvector | 替换 | 向量能力移到 OpenSearch/Qdrant/独立服务 |

## 4. 服务划分

### 4.1 API 服务

FastAPI 单体模块化即可，不建议 MVP 拆微服务。

模块：

- `auth`
- `iam`
- `organizations`
- `projects`
- `runtimes`
- `assets`
- `worktraces`
- `handover`
- `adapters`
- `evidence`
- `audit`
- `reports`
- `webhooks`

### 4.2 Worker 服务

Celery Worker 负责：

- Adapter 同步任务。
- OpenClaw backup 包解析。
- JVS API 文件下载和会话同步。
- ArkClaw 备份任务轮询。
- WorkBuddy 浏览器自动化采集。
- LLM 资产归属判断。
- 交接包生成。
- 审批通过后的执行动作。
- Webhook 投递和重试。

### 4.3 Probe 服务

专有 OpenClaw 探针建议作为独立进程运行，可以由 DuckDock Worker 调度。

部署形态：

- 客户内网部署。
- 与 DuckDock 后端通过 mTLS 或签名 Token 通信。
- 探针只上报结构化资产和证据，不直接暴露原始敏感内容。

## 5. 部署拓扑

### 5.1 单客户私有化部署

```text
客户内网
  DuckDock Web
  DuckDock API
  Worker
  MySQL
  Redis
  MinIO
  Probe
  外部/内部 Agent 平台连接
```

### 5.2 代理服务商多客户托管

```text
服务商中心 DuckDock
  tenant_id 隔离
  每客户独立 RuntimeConnection
  每客户独立对象存储前缀或 bucket
  每客户独立加密密钥
客户侧 Probe
  只连本客户平台
  出站连接 DuckDock
```

## 6. 安全边界

- 所有采集任务必须绑定授权来源：管理员授权、HR 离职事件、项目交接事件或定时盘点策略。
- 所有探针执行必须生成 `collection_job` 和 `audit_log`。
- 原始备份包必须加密保存。
- 凭证值不进入普通业务表。
- LLM 分析只读取脱敏内容或摘要，除非有明确审批。
- 跨租户查询在数据库层和服务层双重限制。

