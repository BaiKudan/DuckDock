# 06. 探针与 Adapter 规范

## 1. 定位

专有 OpenClaw 探针不是员工监控工具，而是企业授权的 AI 资产采集、理解和执行探针。

它负责：

- 到各类 Agent 平台采集资产和工作历程。
- 调用平台原生备份、导出、API、审计日志能力。
- 在缺少 API 时使用管理账号浏览器自动化兜底。
- 生成结构化证据和交接建议。
- 在 DuckDock 审批通过后执行转移、归档、禁用、备份等动作。

## 2. 采集原则

优先级：

1. 官方 API。
2. 官方导出/备份包。
3. 企业管理后台。
4. 浏览器自动化兜底。

限制：

- 不采集员工个人浏览器。
- 不使用员工个人登录态。
- 不绕过平台权限。
- 不默认读取完整敏感会话内容。
- 不直接继承员工密钥，优先记录凭证引用并触发轮换。

## 3. 标准 Adapter 接口

每个 Adapter 必须实现：

```python
class BaseRuntimeAdapter:
    provider: str

    async def test_connection(self) -> AdapterConnectionResult:
        ...

    async def list_capabilities(self) -> AdapterCapabilities:
        ...

    async def collect_assets(self, scope: CollectionScope) -> list[AssetRecord]:
        ...

    async def collect_work_traces(self, scope: CollectionScope) -> list[WorkTraceRecord]:
        ...

    async def collect_artifacts(self, scope: CollectionScope) -> list[ArtifactRecord]:
        ...

    async def collect_evidence(self, scope: CollectionScope) -> list[EvidenceRecord]:
        ...

    async def create_backup(self, scope: BackupScope) -> BackupResult:
        ...

    async def execute_action(self, action: ExecutionAction) -> ExecutionResult:
        ...
```

能力声明：

```json
{
  "provider": "openclaw",
  "capabilities": {
    "asset_sync": true,
    "worktrace_sync": true,
    "artifact_sync": true,
    "backup_create": true,
    "restore": "manual",
    "owner_transfer": "provider_dependent",
    "credential_rotation": false,
    "browser_fallback": false
  }
}
```

## 4. 标准接管包

DuckDock 内部统一生成 `handover_package`，即使厂商没有原生备份包也要形成逻辑包。

目录结构：

```text
handover-package/
  manifest.json
  assets/
    assets.jsonl
    ownership.jsonl
    runtime_bindings.jsonl
  worktraces/
    traces.jsonl
    summaries.jsonl
  artifacts/
    index.jsonl
    files/
  evidence/
    evidence.jsonl
    snapshots/
  risks/
    risk_report.json
  actions/
    proposed_actions.jsonl
    executed_actions.jsonl
```

`manifest.json`：

```json
{
  "package_version": "1.0",
  "tenant_id": "01J...",
  "handover_case_id": "01J...",
  "created_at": "2026-05-18T10:00:00Z",
  "providers": ["openclaw", "jvs"],
  "scope": {
    "subject_user_id": "01J...",
    "lookback_days": 180
  },
  "hashes": {
    "assets/assets.jsonl": "sha256..."
  }
}
```

## 5. OpenClaw Adapter

### 5.1 可用能力

OpenClaw 适合作为第一优先级：

- Gateway API 可采集 Skills、Sessions、Artifacts、Nodes、Approvals、Logs。
- `openclaw backup create` 可作为接管快照能力。
- `openclaw migrate` 的 detect/plan/apply 思想可借鉴到 DuckDock 交接计划。

### 5.2 采集映射

| OpenClaw 数据 | DuckDock 模型 |
|---|---|
| Skill | AIAsset(type=skill) |
| Agent/Node | AIAsset(type=agent/tool) + RuntimeBinding |
| Session | WorkTrace(type=session) |
| Artifact | WorkArtifact |
| Approval | Evidence + Audit |
| Logs | Evidence |
| Backup package | HandoverPackage 原始证据 |

### 5.3 MVP 动作

- 测试 Gateway 连接。
- 拉取 Skill 状态和详情。
- 拉取 Session 列表和摘要。
- 拉取 Artifact 索引。
- 创建 backup 包并上传 MinIO。
- 解析 manifest。
- 生成交接建议。

## 6. JVS Adapter

### 6.1 可用能力

公开 JVS Crew API 具备较清晰的 API 颗粒度，适合做 P0 Adapter。

可用方向：

- 会话列表。
- 会话历史。
- 工作空间文件同步。
- 文件列表。
- 文件下载 URL。
- 用户环境变量元数据。
- 定时任务。
- 定时任务执行记录。
- 全用户定时任务执行记录。

### 6.2 采集映射

| JVS 数据 | DuckDock 模型 |
|---|---|
| Session | WorkTrace(type=session) |
| SessionHistory | WorkTrace 摘要和 Evidence |
| WorkspaceFile | WorkArtifact / AIAsset(type=workspace) |
| active_skills | AIAsset(type=skill) |
| ScheduledTask | AIAsset(type=scheduled_task) |
| ScheduledTaskRun | WorkTrace(type=automation_run) |
| EnvVar metadata | AIAsset(type=credential_ref) |

### 6.3 限制

- 没有发现类似 `backup create` 的一键完整备份。
- 环境变量 API 不应返回 Value，这对安全是合理的。
- DuckDock 需要自己生成逻辑接管包。

## 7. ArkClaw Adapter

### 7.1 调研结论

公开文档能确认 ArkClaw 有备份/恢复数据、迁移 OpenClaw 至 ArkClaw、工作区文件、安全日志、记忆管理等能力。

但需要进一步向火山确认：

- 是否有 API/CLI 触发备份。
- 备份包是否可下载。
- 备份包是否包含 Skills、会话、文件、记忆、定时任务、配置。
- 是否包含 secrets，如何加密。
- 是否有 manifest。
- 是否支持恢复到新实例。

### 7.2 MVP 策略

P0 不承诺 ArkClaw 完整自动化，只预留 Adapter 框架。

P1 实现：

- 管理后台/API 连接测试。
- 备份记录采集。
- 安全日志采集。
- 资产列表同步。
- 备份包导入 DuckDock。

## 8. WorkBuddy Adapter

### 8.1 调研结论

公开文档未发现等价 `openclaw backup create` 的一键完整备份/恢复能力。

可见能力偏：

- 任务。
- 对话。
- 工作空间。
- 产物。
- 已分享文件。
- 已归档任务。
- 自动化任务。
- 企业用量管理。

### 8.2 MVP 策略

P0 不承诺完整备份。

P1/P2 采用：

1. 企业 API 或后台权限优先。
2. 浏览器自动化采集任务、产物、归档和自动化配置。
3. 生成逻辑接管包。

## 9. LLM 判断规范

LLM 可用于：

- 归属推断。
- 项目关联推断。
- 风险摘要。
- 交接建议。
- 会话摘要。
- 产物分类。

LLM 不可直接：

- 修改资产归属。
- 删除或禁用资产。
- 查看未授权敏感内容。
- 输出不可追溯结论。

LLM 输出必须包含：

```json
{
  "recommendation": "transfer_owner",
  "target_asset_id": "01J...",
  "receiver_user_id": "01J...",
  "confidence": 0.92,
  "evidence_ids": ["01J...", "01J..."],
  "reason": "最近 90 天该资产主要用于 A 项目，接收人为项目技术负责人"
}
```

## 10. 探针部署安全

- 探针使用出站连接访问 DuckDock，减少客户网络暴露。
- 探针与 DuckDock 使用 mTLS 或短期签名 Token。
- 探针本地不长期保存敏感数据。
- 探针日志默认脱敏。
- 每个采集任务有唯一 job id。
- 每个执行动作有幂等键。

