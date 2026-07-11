# 07. 开发路线与验收标准

## 1. 总体路线

建议按 4 个阶段开发：

```text
Phase 0：技术底座调整
Phase 1：资产控制平面 MVP
Phase 2：交接闭环和探针
Phase 3：多厂商增强和商业化报表
```

## 2. Phase 0：技术底座调整

目标：满足强制技术约束，为后续模块打基础。

任务：

- 后端保持 FastAPI。
- 数据库从 PostgreSQL 迁移到 MySQL 8.0。
- SQLAlchemy async driver 改为 `asyncmy`。
- Alembic 在 MySQL 下重新验证。
- Redis、Celery、MinIO 保留。
- 前端引入 Ant Design、TanStack Query、Pro Components。
- OpenAPI 类型生成接入前端。

验收：

- `docker compose up` 可启动 MySQL、Redis、MinIO、Backend、Worker、Frontend。
- Alembic 可在空 MySQL 实例完成建表。
- 登录、IAM、Namespace、Skill 基础流程不回归。
- 前端构建通过。

## 3. Phase 1：资产控制平面 MVP

目标：建立 AI 资产目录和运行时连接。

后端任务：

- 新增 `runtime_instance`、`collection_job`。
- 新增 `ai_asset`、`asset_ownership`、`runtime_binding`。
- 新增 `work_trace`、`work_artifact`。
- 新增 `evidence_item`。
- 新增 Runtime API。
- 新增 Asset API。
- 新增 WorkTrace API。
- 新增 Evidence API。

前端任务：

- 首页仪表盘。
- 运行时中心。
- 资产中心。
- 工作历程列表。
- 证据摘要页面。

验收：

- 管理员可创建 OpenClaw/JVS 运行时连接。
- 管理员可触发同步任务。
- 同步结果能进入资产目录。
- 资产详情能看到归属、运行时绑定、工作历程、证据。
- 所有写操作进入审计。

## 4. Phase 2：交接闭环和探针

目标：完成离职交接从采集到审批再到执行的闭环。

后端任务：

- 新增 `handover_case`、`handover_item`、`approval_task`、`execution_action`。
- 实现交接状态机。
- 实现交接包生成。
- 实现审批流。
- 实现执行动作框架。
- 实现 OpenClaw Adapter。
- 实现 JVS Adapter。
- 实现 LLM 交接建议。

前端任务：

- 交接中心。
- 创建交接单 Wizard。
- 交接单详情。
- 审批任务。
- 执行日志 Timeline。
- 风险中心 MVP。

验收：

- 可创建员工离职交接单。
- 可选择员工、接收人、运行时范围、采集范围。
- 系统可生成交接项和风险建议。
- 审批通过前不能执行交接动作。
- 审批通过后可执行归属转移、归档、备份包生成等动作。
- 交接包上传 MinIO，并保存 manifest 和 sha256。
- 所有证据可追溯到采集任务。

## 5. Phase 3：多厂商增强

目标：增强 ArkClaw、WorkBuddy、报表和商业化能力。

任务：

- ArkClaw 备份/恢复能力接入。
- ArkClaw 安全日志和记忆管理采集。
- WorkBuddy 企业后台/API 采集。
- 浏览器自动化兜底采集。
- 凭证引用识别和轮换工单。
- 报表中心。
- 资产图谱。
- Webhook 事件增强。
- 多租户隔离增强。

验收：

- ArkClaw 可完成至少资产同步和备份记录入库。
- WorkBuddy 可完成任务、产物、归档、自动化配置采集。
- 报表可按租户、平台、部门、项目过滤。
- 凭证风险可生成工单。

## 6. P0 用户故事

### US-001 创建运行时连接

作为平台管理员，我希望配置 OpenClaw/JVS 连接，使 DuckDock 可以采集企业 AI 资产。

验收：

- 支持新增、编辑、禁用运行时。
- 支持测试连接。
- 支持查看 Adapter 能力。
- 凭证不明文落库。

### US-002 同步资产

作为平台管理员，我希望手动触发同步，查看同步进度和结果。

验收：

- 同步任务有 pending/running/succeeded/failed 状态。
- 失败可查看错误原因。
- 成功后资产进入资产中心。

### US-003 创建离职交接单

作为 HR 或管理员，我希望为离职员工创建交接单。

验收：

- 可选择离职员工、接收人、运行时范围。
- 可配置回溯天数和是否包含产物。
- 创建后进入 collecting 状态。

### US-004 生成交接建议

作为项目主管，我希望系统基于证据生成交接建议。

验收：

- 每条建议包含资产、动作、接收人、风险原因、证据、置信度。
- 建议可以修改、跳过、标记人工复核。

### US-005 审批交接

作为主管或安全负责人，我希望审批交接动作。

验收：

- 审批前可查看影响范围。
- 审批意见进入审计日志。
- 拒绝后不能执行。

### US-006 执行交接

作为平台管理员，我希望审批通过后执行交接动作。

验收：

- 支持幂等执行。
- 每个执行动作有状态和结果。
- 失败动作可重试。
- 执行结果进入证据链和审计。

## 7. 质量要求

### 7.1 后端

- 单元测试覆盖核心服务。
- 状态机有测试。
- Adapter 使用 mock server 测试。
- 所有 API 有 OpenAPI schema。
- 所有写操作有审计。
- 采集任务可重试且幂等。

### 7.2 前端

- 核心流程有端到端测试。
- 表单校验完整。
- 权限隐藏和禁用逻辑可测。
- 大表格分页和筛选可用。
- 异步任务状态实时刷新或轮询。

### 7.3 安全

- 敏感内容默认不展示。
- 凭证加密存储。
- 签名 URL 有过期时间。
- 审计日志不可由普通管理员删除。
- 探针任务必须有授权来源。

## 8. 风险清单

| 风险 | 影响 | 应对 |
|---|---|---|
| 厂商 API 不完整 | 部分资产无法自动采集 | Adapter 能力声明 + 浏览器自动化兜底 + 人工补录 |
| 备份包包含 secrets | 泄露风险 | 加密存储 + 权限隔离 + 默认不展示 |
| LLM 判断错误 | 交接错误 | 证据 + 置信度 + 人工审批 |
| 员工反感 | 推广受阻 | 产品定位为资产交接，员工可见自己名下资产 |
| PostgreSQL 转 MySQL 成本 | 现有代码调整 | Phase 0 单独处理，先保证基础功能回归 |
| 多租户隔离缺陷 | 严重安全事故 | 所有查询强制 tenant_id，测试覆盖越权场景 |

## 9. 发布标准

MVP 可发布条件：

- OpenClaw 和 JVS 至少一个真实环境同步通过。
- 离职交接流程完整闭环。
- MySQL 迁移完成。
- 审计日志覆盖所有写操作。
- 备份包和交接包可下载、可校验 sha256。
- 权限不足无法查看敏感证据。
- 部署文档和管理员手册齐全。

## 10. 参考资料

- OpenClaw backup / migrate / Gateway API 文档。
- 火山引擎 ArkClaw 文档。
- 腾讯 CodeBuddy WorkBuddy 文档。
- 阿里云 JVS Crew API 文档。

