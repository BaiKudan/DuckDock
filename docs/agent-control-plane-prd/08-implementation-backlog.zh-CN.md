# 08. 开发 Backlog

## 1. Epic 总览

| Epic | 名称 | 优先级 | 依赖 |
|---|---|---|---|
| E00 | MySQL 技术底座迁移 | P0 | 无 |
| E01 | 统一身份、组织、租户增强 | P0 | E00 |
| E02 | Runtime 运行时中心 | P0 | E00 |
| E03 | AI 资产目录 | P0 | E01, E02 |
| E04 | 工作历程与产物 | P0 | E02 |
| E05 | 证据链与审计增强 | P0 | E00 |
| E06 | OpenClaw Adapter | P0 | E02, E03, E04, E05 |
| E07 | JVS Adapter | P0 | E02, E03, E04, E05 |
| E08 | 离职/项目交接工作流 | P0 | E03, E04, E05 |
| E09 | 前端控制台 | P0 | E02-E08 |
| E10 | ArkClaw Adapter | P1 | 厂商接口确认 |
| E11 | WorkBuddy Adapter | P1 | 企业接口或浏览器采集方案 |
| E12 | 报表与资产图谱 | P1 | E03, E08 |

## 2. E00 MySQL 技术底座迁移

后端任务：

- 将数据库连接配置改为 MySQL。
- 引入 `asyncmy`。
- 替换 PostgreSQL 专属 SQL。
- 重新生成 Alembic baseline。
- 编写从旧库到 MySQL 的迁移脚本或一次性导入脚本。
- 更新 docker-compose MySQL 服务。
- 更新 `.env.example`。

验收：

- 空 MySQL 可完成 `alembic upgrade head`。
- 现有用户、Namespace、Skill 基础接口可跑通。
- 所有测试环境使用 MySQL。

## 3. E02 Runtime 运行时中心

后端任务：

- 新增 Runtime model/schema/service/api。
- 支持 provider：openclaw、jvs、arkclaw、workbuddy、custom。
- 支持凭证保存到 KMS/Vault 抽象。
- 支持连接测试。
- 支持能力声明。
- 支持手动同步任务创建。

前端任务：

- Runtime 列表。
- Runtime 新建 Wizard。
- Runtime 详情页。
- 测试连接交互。
- 同步任务列表。

验收：

- 管理员可配置 OpenClaw/JVS 连接。
- 凭证不明文返回前端。
- 测试连接结果展示 provider 版本和能力。

## 4. E03 AI 资产目录

后端任务：

- 新增 AIAsset、AssetOwnership、RuntimeBinding。
- 实现资产 upsert。
- 实现归属推断结果保存。
- 实现资产列表、详情、更新归属 API。
- 将现有 Skill 映射为 AIAsset。

前端任务：

- 资产列表 ProTable。
- 资产详情 Tabs。
- 批量加入交接单。
- 批量认领。

验收：

- Skill、OpenClaw 采集资产、JVS 采集资产可统一展示。
- 资产详情能看到来源平台、运行时绑定、负责人。

## 5. E04 工作历程与产物

后端任务：

- 新增 WorkTrace、WorkArtifact。
- 实现工作历程列表和详情 API。
- 实现产物对象上传和签名 URL。
- 实现敏感内容 reveal API。

前端任务：

- 工作历程列表。
- 工作历程详情。
- 产物列表。
- 敏感内容查看申请弹窗。

验收：

- 默认只展示摘要。
- 查看完整内容必须填写原因并记录审计。

## 6. E05 证据链与审计增强

后端任务：

- 新增 EvidenceItem。
- 扩展 AuditLog 的对象类型、request_id、reason、before/after。
- 对所有写操作加审计装饰器或服务层封装。
- 实现证据签名 URL。

前端任务：

- 证据列表。
- 证据详情抽屉。
- 审计日志筛选。

验收：

- 每个交接建议能追溯证据。
- 管理员查看敏感证据有审计记录。

## 7. E06 OpenClaw Adapter

后端任务：

- 实现 OpenClaw client。
- `test_connection`。
- `collect_assets`。
- `collect_work_traces`。
- `collect_artifacts`。
- `create_backup`。
- backup 包上传 MinIO。
- backup manifest 解析。

验收：

- 能从测试 OpenClaw 实例同步 Skill/Session/Artifact。
- 能生成 OpenClaw 原始接管快照。
- 采集失败可重试。

## 8. E07 JVS Adapter

后端任务：

- 实现 JVS client。
- 同步 Session。
- 同步 SessionHistory。
- 同步 WorkspaceFiles。
- 下载文件并生成 WorkArtifact。
- 同步 ScheduledTasks 和 Runs。
- 同步 EnvVar 元数据为 credential_ref 资产。

验收：

- 能生成 JVS 逻辑接管包。
- 不要求获取环境变量 Value。
- 文件下载失败不阻断整体任务，进入部分失败状态。

## 9. E08 交接工作流

后端任务：

- 新增 HandoverCase、HandoverItem、ApprovalTask、ExecutionAction。
- 实现状态机。
- 实现创建交接单。
- 实现采集、分析、提交审批、审批、执行、验收。
- 实现 LLM 交接建议服务。
- 实现交接包生成。

前端任务：

- 交接列表。
- 创建交接单 Wizard。
- 交接详情。
- 审批页面。
- 执行日志。

验收：

- 离职交接完整闭环。
- 审批前不可执行。
- 所有动作可审计。

## 10. E09 前端控制台

任务：

- 引入 Ant Design。
- 建立 Layout、菜单、权限路由。
- 建立 API client。
- 建立 QueryClient。
- 建立通用表格、状态标签、风险标签、证据抽屉。
- 完成首页、运行时、资产、工作历程、交接、风险、审计页面。

验收：

- `npm run build` 通过。
- P0 页面路由完整。
- 无权限按钮禁用或隐藏。
- 异步任务有轮询和错误展示。

## 11. 测试要求

后端：

- Service 单元测试。
- API 集成测试。
- Adapter mock 测试。
- 状态机测试。
- 权限越权测试。

前端：

- 关键页面渲染测试。
- 创建交接单流程 E2E。
- 审批流程 E2E。
- 权限隐藏测试。

