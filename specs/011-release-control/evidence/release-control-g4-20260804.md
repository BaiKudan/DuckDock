# E05 Release Control / G4 技术验收证据

> 日期：2026-08-04
>
> 范围：RC-01～RC-08、S7/S8、M4/G4
>
> 环境：本机 Docker Compose、MySQL 8.4、真实 Hermes Runtime #9、DuckDock frontend 5174 / backend 8801

## 结论

E05 八个验收切片全部完成。DuckDock 已具备从不可变 PackageVersion、Deployment revision 和 Eval/Package 证据，经版本化策略、审批/例外、分环境推广，到 Runtime 身份回执、Canary 判定和精确回滚的可审计闭环。

控制面不会把“已派发”误报为“已发布”。只有持有独立 `release.receipt` scope 的目标 Runtime 凭据返回精确匹配的 PackageVersion、revision 和 configuration digest 后，Environment 才会激活。Canary 失败会以之前成功的 EnvironmentRelease 为精确目标生成 rollback dispatch，并在收到匹配回执后恢复 ACTIVE 指针。

## RC-01～RC-08

| Slice | 已验证结果 |
|---|---|
| RC-01 | Namespace-scoped Environment、不可变 Candidate、精确 PackageVersion/Deployment/目标环境绑定；候选创建冻结 REGISTERED Deployment。 |
| RC-02 | ReleasePolicy 与 append-only PolicyVersion；SHADOW/WARN/ENFORCE、目标环境和 typed rules 均进入 canonical digest。 |
| RC-03 | PolicyDecision 与有序 rule results 精确记录 Package、签名、Eval、风险、工具/能力、SBOM 和 rollback evidence；缺失证据 fail closed。 |
| RC-04 | shadow/warn/enforce 语义、幂等 replay/conflict、低敏 Audit/Transactional Outbox 均由服务测试覆盖。 |
| RC-05 | 不可变审批、四眼约束、限时例外和独立复核均通过；过期例外不生效。 |
| RC-06 | 显式 Environment promotion order、精确 PolicyDecision/approval snapshot、幂等 dispatch；派发状态不宣称成功。 |
| RC-07 | Runtime 身份从 credential 服务端派生；APPLIED/MISMATCH/FAILED 回执精确校验；mismatch 不激活环境。 |
| RC-08 | metadata-only AgentRun 聚合、Canary PASS/FAIL/INCONCLUSIVE、失败自动 rollback、rollback APPLIED 回执和 UI 历史均完成。 |

主要实现：

- migration `20260804_0058`：Environment、Candidate、Policy/Version、Decision/RuleResult；
- migration `20260804_0059`：Approval、Exception/Review、Promotion、Receipt、EnvironmentRelease、CanaryEvaluation、Rollback；
- 管理 API：Environment、Policy、Candidate、Decision、Approval、Exception、Promotion、Canary、Rollback、Receipt 历史；
- Runtime API：`POST /api/v2/reporter/promotion-receipts`；
- 最小权限凭据：签发/列出/吊销独立 `release.receipt` credential，明文 token 只返回一次；
- `/release-control`：策略规则解释、审批/例外、promotion、canary、rollback、EnvironmentRelease 和 Runtime receipt 运营台。

## 真实 Hermes 闭环

可重复脚本：

```bash
docker compose exec -T -e PYTHONPATH=/app -e DEBUG=false \
  backend python scripts/verify_release_control_dev.py
```

Hermes `http://host.docker.internal:50070/v1/models` 返回 1 个 OpenAI-compatible model identity；验证脚本只读取模型元数据并计算配置 digest，不读取或持久化对话正文。

普通发布：

- Namespace `6` / Runtime `9`；
- PackageVersion `pkgv_35c6af5165f54f69aad2c6d8acb59175`；
- Deployment `dep_90aba0014d13453db4c452ca7e13c081` / revision `hermes-e05-f69015b5daa2`；
- Environment `renv_9286f730d15942869c27aa45f6bf2b3c`；
- Candidate `rcand_ad0e809f2dfe476dafea4acc9d38f79e`；
- PolicyDecision `rpdec_94a6508c5d024bdba41d0d556eea9bf6` = `ENFORCE/PASS/ALLOW`；
- Promotion `rprom_dcf78d2fb3fb47169360055232894cd4` = `SUCCEEDED`；
- Runtime receipt `rrcpt_d4dde49c47e74d6da24a63f1661d6328` = `APPLIED`。

Canary 失败与自动回滚：

- Canary Deployment `dep_2f9e3ef10c104cf7b1a77580809cdcd8`；
- Candidate `rcand_f1c0b4c634bc41dabf45840d215e68b5`；
- Promotion `rprom_2dca580133fa464a9a9cf84206a08424` = `ROLLED_BACK`；
- 精确 Deployment 上 1 条 metadata-only failed AgentRun；
- CanaryEvaluation `rcan_2f1e4b3234794fc48b398e16a12b0cc6` = `FAIL/failure_rate_exceeded`；
- Canary receipt `rrcpt_526911eded7a456ca0e45e09c7c0ba04` = `APPLIED`；
- Rollback `rrbk_25ec1a2926584fb981e4af86379b0ce0` = `SUCCEEDED/canary_policy_failed`；
- Rollback receipt `rrcpt_bfc51fe55f9445959cdc70860b33ec16` = `ROLLBACK/APPLIED`；
- 旧成功候选重新成为唯一 ACTIVE release，Canary Deployment 被 RETIRED。

脚本可从 REGISTERED、DISPATCHED、OBSERVING 或 ROLLBACK_REQUESTED 中间态继续；validation credential 均在 `finally` 中吊销。真实运行还捕获并修复了机器凭据签发后立即使用的事务可见性窗口：`release.receipt` 签发 API 现在在返回一次性明文 token 前显式提交并刷新凭据。

## 浏览器验收

真实管理员登录后打开 `http://127.0.0.1:5174/release-control`：

- 精确看到 `ENFORCE → ALLOW`、普通 Promotion `SUCCEEDED`、Runtime receipt `APPLIED` 和 ACTIVE release；
- 精确看到 Canary `FAIL/failure_rate_exceeded`；
- 精确看到 Promotion `ROLLED_BACK`、Rollback `SUCCEEDED/canary_policy_failed` 和 `ROLLBACK` receipt；
- refresh 后各记录保持一致；
- 浏览器 console error 为 `0`；
- 中等视口指标卡改为两列，避免中文被挤成竖排；非 namespace admin 无法列 credential metadata 时，其他 Release Control 只读证据仍可用。

## 迁移、质量与安全门禁

- 开发库：`20260804_0059 (head)`，`alembic check` = `No new upgrade operations detected`；
- 隔离 MySQL 8.4 空库：base → `20260804_0059` 成功；
- guarded downgrade：`0059 → 0058` 按设计拒绝，原因是 approval/receipt/activation/rollback evidence 不可变；拒绝后仍为 `0059 head` 且 check clean；临时授权和临时库已撤销/删除；
- backend：`986 passed, 19 skipped, 3 warnings`；
- frontend：`9` files / `52 passed`；production build PASS；ESLint `0 errors`、`9` 个既有 warnings；
- E05 focused：`5 passed`；Ruff clean；
- credential scope isolation：`execution.write` 不能提交 release receipt，`release.receipt` 不能写 execution；
- Audit/Outbox 不记录审批评论、例外理由、Manifest/SBOM 正文、Runtime secret、prompt/message/tool 内容。

## G4 判定

RC-01～RC-08、真实 MySQL、全量回归、真实 Hermes receipt/canary/rollback、浏览器和安全边界证据齐备。M4/G4 技术验收通过，主线进入 E06 Handover 2.0。
