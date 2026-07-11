# 09. 厂商能力调研与待确认问题

## 1. 调研结论

截至 2026-05-18，基于公开可访问资料：

| 平台 | 类似 `openclaw backup create` 能力 | DuckDock 接入判断 |
|---|---|---|
| OpenClaw | 有 backup/migrate/Gateway API | P0 优先接入，可做原生接管快照 |
| ArkClaw | 公开文档显示有备份/恢复数据能力 | P1 重点推进，需要厂商确认 API/包格式 |
| JVS | 未发现一键备份，但 Crew API 足够拼逻辑包 | P0 优先接入，可快速落地 |
| WorkBuddy | 未发现一键完整备份/恢复 | P1/P2，先企业后台/API，再浏览器自动化兜底 |

## 2. OpenClaw

可利用能力：

- Gateway API。
- Skills、Sessions、Artifacts、Nodes、Approvals、Logs。
- `openclaw backup create`。
- `openclaw migrate` 的 detect/plan/apply 迁移思想。

DuckDock 用法：

- 采集资产。
- 采集工作历程。
- 生成接管快照。
- 解析备份包。
- 生成交接建议。

风险：

- backup 包可能包含 credentials、sessions、state。
- 必须加密保存，并限制查看。

## 3. ArkClaw

公开可见方向：

- 备份/恢复 ArkClaw 数据。
- 迁移 OpenClaw 至 ArkClaw。
- 工作区文件管理。
- 安全日志。
- 记忆管理。
- 终端使用 ArkClaw。

待确认问题：

- 是否有 API/CLI 触发备份。
- 备份包是否可下载。
- 备份包包含哪些对象。
- 是否包含 secrets。
- 是否有 manifest、hash、版本信息。
- 是否支持恢复到新实例。
- 是否可以按员工、项目、工作区过滤备份范围。

产品策略：

- PRD 中预留 Adapter。
- 商务/交付阶段向火山确认专有接口。
- 若没有 API，使用管理后台自动化兜底。

## 4. JVS

公开 API 可支持：

- Session 列表。
- Session 历史。
- Workspace 文件同步。
- 文件列表。
- 文件下载 URL。
- 用户环境变量元数据。
- 定时任务。
- 定时任务执行记录。
- 全用户定时任务执行记录。

DuckDock 用法：

- 由 Adapter 调用 API。
- 将会话转 WorkTrace。
- 将文件转 WorkArtifact。
- 将 active_skills 转 AIAsset。
- 将环境变量元数据转 CredentialRef 资产。
- 将定时任务转 ScheduledTask 资产。
- 生成 DuckDock 逻辑接管包。

限制：

- 没有一键备份。
- 没有统一 restore/import 语义。
- 环境变量 Value 不应被采集。

## 5. WorkBuddy

公开文档可见方向：

- 任务。
- 对话。
- 工作空间。
- 产物。
- 已分享文件。
- 已归档任务。
- 自动化任务。
- 企业用量管理。

未发现：

- 完整任务空间导出。
- 完整会话历史导出。
- Skill/连接器/MCP 配置导出。
- 凭证引用导出。
- 恢复到另一个账号/实例。

DuckDock 策略：

- 不在 P0 承诺完整接管。
- 优先争取企业版 API 或管理后台接口。
- 浏览器自动化只使用管理账号和授权范围。
- 输出逻辑接管包，而不是原生备份包。

## 6. 采购/合作方需要问的问题

对所有厂商统一提问：

- 是否支持企业级资产 API。
- 是否支持按用户/项目/空间导出。
- 是否支持会话和产物导出。
- 是否支持备份包下载。
- 是否支持恢复到新用户或新实例。
- 是否有审计日志 API。
- 是否能区分个人资产和企业资产。
- 是否支持凭证引用列表，不暴露凭证值。
- 是否支持管理员代管和离职交接。
- 是否支持 Webhook。

