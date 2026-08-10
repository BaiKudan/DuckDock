# E09 / DuckDock 2.0 GA Candidate 技术验收证据

> 日期：2026-08-04
> 范围：GA-01～GA-08
> 环境：本机 Docker Compose、MySQL 8.4、MinIO、Redis、Prometheus、Langfuse v4、OTel、真实 Hermes

## 结论

E09 技术范围完成。DuckDock 2.0 的既有技术 Gate 已收敛为 14 项实时、只读、可解释检查；真实验证结果为 `14 PASS / 0 WARN / 0 BLOCK`，总体 `READY`。这代表技术候选就绪，不替代后续人工产品合理性讨论或生产变更审批。

## 冻结契约与兼容策略

- contract version `2.0.0-ga`；
- generated snapshot `contracts/openapi-v2.generated.json`；
- SHA-256 `60146a22536c8e9485cf20e1ed21584dbb3b6c6e02a0b75a8b58b8eebdb06357`；
- snapshot 仅包含 `/api/v2` paths，components 保持自包含；测试逐字节比较 live schema；
- v1 response：`Deprecation: true`、`X-DuckDock-API-Compatibility: v1-supported-through-2.x`、`Link: </api/v2>; rel="successor-version"`；不发送虚构 Sunset 日期。

## 真实 Hermes / SLO / GA 门禁

```bash
backend/.venv/bin/python backend/scripts/verify_ga_candidate_dev.py
```

脚本真实执行：

1. 检查本机 Hermes `/v1/models`；
2. 通过 API 签发短期 DEVICE workload identity；
3. 使用有效 token 提交 25 次 Reporter heartbeat；
4. 查询 25 次真实 AgentRun timeline；
5. 对现有 Candidate/PolicyVersion 发起 25 次相同幂等策略评估；
6. 吊销 workload identity；
7. 创建不可变 SLO evaluation，读取 reconciliation 与 GA readiness。

结果：

- SLO `oslo_0743432bd56d4f0d97e1533f6bb60891` = `HEALTHY`；
- requests `81`、5xx `0`；evidence ingest p95 `4.634ms`、timeline p95 `9.311ms`、policy p95 `49.421ms`；
- reconciliation：matched `0`、expected legacy-only `2`、mismatch/unexplained `0`；
- migration `20260804_0062`、contract、Runtime、Package、APPLIED receipt、signed Handover、directory offboarding、key rotation、reconciliation、Outbox、recovery、SLO、incidents、Prometheus 共 14 项全部 PASS；
- GA status `READY`。

## 质量结果

- focused E08/E09 backend：`6 passed`；
- full backend：`1005 passed, 19 skipped, 3 warnings`；
- frontend：`10` files / `53 passed`；
- ESLint：`0 errors`、`9` 个既有 warnings；
- production build PASS；
- Ruff clean；
- mypy ratchet `82 errors = 82 baseline`，E05～E09 新增错误已归零且未放宽基线；
- `alembic check`：`No new upgrade operations detected`；
- frozen OpenAPI drift check PASS。

## G6 技术判定

GA-01～GA-08 与 14 项实时门禁全部通过，DuckDock 2.0 达到 G6 技术候选状态。下一阶段是用户主导的人工产品验证与合理性讨论，不再是未完成的基础功能开发。
