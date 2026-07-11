# 10. Adapter 采集器技术可行性与开发任务

## 1. 结论

DuckDock 可以用相对通用的方式建设 OpenClaw 类产品采集器，但通用层不应该抽象成“万能爬虫”，而应该抽象成：

- 统一采集任务协议。
- 统一 Adapter 能力声明。
- 统一原始记录与证据留存。
- 统一标准化入库模型。
- 厂商差异留在 Adapter 实现内。

第一阶段建议以 OpenClaw 为参考实现，优先支持 backup/json 导入和运行时元数据采集，再接真实 API。ArkClaw、WorkBuddy、JVS 先接入同一 Adapter Contract，等拿到厂商 API 或企业后台导出能力后补具体实现。

## 2. 当前已落地的工程能力

本轮实现的基础能力：

- `adapter_cursors`：每个运行时、每条数据流的增量游标。
- `adapter_run_steps`：采集任务步骤状态，方便排障和审计。
- `raw_collection_records`：保留厂商原始记录和 hash，支持证据链与幂等。
- `adapter_errors`：采集错误、重试属性和上下文。
- `provider_principals`：厂商侧用户、机器人、服务账号到 DuckDock 用户的映射。
- `runtime_capability_snapshots`：运行时能力快照，记录某次探测结果。
- `BaseRuntimeAdapter`：标准 Adapter 接口。
- `OpenClawAdapter`：OpenClaw 参考实现骨架。
- OpenClaw backup/json 导入解析器。
- `run_adapter_collection_job` Celery 任务。
- 采集任务手动运行 API。

## 3. 标准数据流

```text
RuntimeInstance
  -> CollectionJob
  -> AdapterRunStep
  -> Adapter.collect()
  -> RawCollectionRecord
  -> NormalizedPrincipal / NormalizedAsset / NormalizedWorkTrace / NormalizedEvidence
  -> AIAsset / AssetOwnership / WorkTrace / EvidenceItem
  -> AdapterCursor / RuntimeCapabilitySnapshot
```

关键原则：

- Adapter 产出标准化对象，不直接决定交接策略。
- 原始记录必须先留存，后续分析和审计可以追溯。
- 每次采集必须可重放、可幂等、可分步骤定位失败。
- 浏览器自动化只作为管理员授权的兜底方式，不作为员工端监控。

## 4. Adapter Contract

每个厂商 Adapter 至少实现：

```python
class BaseRuntimeAdapter:
    async def test_connection(self) -> AdapterConnectionResult: ...
    async def list_capabilities(self) -> AdapterCapabilities: ...
    async def collect(self, job: CollectionJob) -> AdapterCollectionResult: ...
```

`AdapterCollectionResult` 标准输出：

- `raw_records`
- `principals`
- `assets`
- `work_traces`
- `evidence`
- `cursor_updates`
- `capabilities`

## 5. P0 开发任务

### T01. OpenClaw Backup Importer

目标：

- 支持 `.json` 和 `.zip` 导入。
- 识别 `manifest/assets/skills/agents/prompts/workflows/users/sessions/runs/evidence`。
- 标准化为 DuckDock 资产、用户映射、工作历程和证据。

验收：

- 上传一个 backup/json 后产生 `CollectionJob`。
- 任务状态为 `succeeded`。
- `raw_collection_records` 有原始记录。
- `ai_assets` 有资产。
- `provider_principals` 有厂商用户映射。

### T02. Adapter Job Runner

目标：

- 支持 API 手动运行采集任务。
- 支持 Celery 异步运行。
- 保存步骤、错误、游标和能力快照。

验收：

- `POST /api/v1/collection-jobs/{job_id}/run` 可运行任务。
- `GET /api/v1/collection-jobs/{job_id}/steps` 可查看步骤。
- 失败时 `adapter_errors` 有错误记录。

### T03. OpenClaw Live API Collector

目标：

- 接入真实 OpenClaw API 或私有化 Gateway。
- 支持 assets/principals/worktraces/artifacts 分流采集。
- 支持 cursor 增量同步。

依赖：

- OpenClaw API 文档或私有化项目接口。
- 凭据管理方案，优先使用 Vault/环境变量引用，不在数据库保存明文密钥。

验收：

- 连续两次同步不会重复创建资产。
- 增量游标可推进。
- API 限流和错误可记录。

### T04. JVS Adapter

目标：

- 以公开/企业 API 优先。
- 聚焦 Agent、Workflow、Knowledge、Task Run。

验收：

- 至少完成资产和运行记录采集。
- 能识别 owner/creator/maintainer。

### T05. ArkClaw / WorkBuddy Adapter Skeleton

目标：

- 先注册能力矩阵。
- 支持企业导出包或后台导出 CSV/JSON 导入。
- 浏览器兜底方案只读、低频、管理员授权。

验收：

- 能创建运行时。
- 能声明能力限制。
- 能导入管理员导出文件。

## 6. P1 开发任务

- Adapter Mock Server，用于回归测试。
- OpenClaw backup create 自动触发。
- WorkArtifact 对象存储落盘。
- 敏感字段脱敏策略。
- 采集任务重试、取消、租约和并发控制。
- 交接分析接入 LLM 证据评分。
- 浏览器兜底采集的可视化授权流程。

## 7. 风险评估

| 风险 | 影响 | 应对 |
| --- | --- | --- |
| 厂商 API 不稳定 | 采集覆盖不完整 | 能力快照 + fallback 导入 |
| 备份包格式变化 | 导入失败 | 保留 raw record，解析器版本化 |
| 员工隐私担忧 | 推广阻力 | 只采企业运行时和授权数据，不做终端监控 |
| 凭据泄露 | 高安全风险 | credential_ref 引用外部密钥，不保存明文 |
| 重复入库 | 数据污染 | external_id + content_hash + raw hash 幂等 |

## 8. 下一步建议

短期先完成三个闭环：

1. OpenClaw backup/json 导入闭环。
2. OpenClaw 真实 API 采集闭环。
3. 离职交接单基于采集结果自动生成待接管项。

这三个闭环成立后，DuckDock 就具备“控制平面 + 采集探针”的产品原型，而不是单纯的 Skill 仓库。
