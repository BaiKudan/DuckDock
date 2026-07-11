# DuckDock 控制平面 · 管理员手册(部署与运维)

> 面向部署/运维管理员。覆盖权限收口后的角色绑定、凭证加密密钥、上报(Push)接入、
> 离职交接生命周期、敏感内容查看。对应 specs/001 + specs/003 + 宪法原则 IV/V。

## 0. ⚠️ 部署后第一件事:授予控制平面权限(否则全是 403)

2026-06-12 起,控制平面 43 个端点已**权限收口**(specs/003 方案 A):
- **系统 admin**(第一个注册的用户,见 README §3.6)不受影响,自动放行;
- **其他运维/平台管理员**没有任何控制平面权限 → 访问运行时/资产/交接列表会收到 **403**。
  这是**预期行为**,不是系统故障。

**解决**:用系统 admin 账号,在 IAM 里给运维同事绑定 `enterprise-admin` 角色(含
`runtime.* / asset.* / handover.* / worktrace.* / evidence.*` 全部权限键)。

> 需要更细粒度时,可绑定单项权限键(例如只读运维绑 `runtime.read` + `asset.read`,
> 不给 `handover.manage`)。权限键清单见 `backend/app/services/iam_service.py` 的 `BUILTIN_PERMISSIONS`。

| 控制平面区域 | 读需要 | 写/管理需要 |
|---|---|---|
| 运行时 / 采集任务 / 上报 | `runtime.read` | `runtime.manage` |
| 资产目录 / 归属 | `asset.read` | `asset.manage` |
| 工作历程(摘要) | `worktrace.read` | — |
| 工作历程**全文**(reveal) | `worktrace.content.read` | — |
| 证据 | `evidence.read` | — |
| 交接单 | `handover.read` | `handover.manage` |

## 1. 凭证加密密钥(用运行时凭证前必设)

运行时凭证以 **Fernet 密文**落库(specs/001 FR-016),需在 `.env` 配置独立密钥:

```bash
# 生成(独立于 SECRET_KEY)
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# 写入 .env
DUCKDOCK_CREDENTIAL_KEY=<上面输出>
```

- 不配置不影响其它功能,仅在**存取运行时凭证**时报清晰错误。
- 接口**绝不**回传明文;轮换走 runtime 的 `credential`(write-only)字段,就地换密文。
- ⚠️ 密钥丢失 = 已存密文不可解;请与其它 secret 一同纳入密钥管理与备份。

## 2. 采集 = Push 上报(无服务端外联)

DuckDock **不主动连厂商平台**(Pull Adapter 链已退役)。数据由现场 **Reporter 技能**Push 自报,按场景分两条链路:

- 日常日报/周报:Reporter 提交 `duckdock-structured-report-v1` JSON 到 `/reports/structured`,DuckDock 校验后直接物化轻量工作历程、资产引用、项目上下文和交接/风险信号。
- 交接/审计/证据包:Reporter 上传 `duckdock-pack-v1.zip`,Analysis Worker(LLM 或 baseline)解析物化,适合需要文件、hash、证据索引或完整归档的场景。

推荐接入步骤:
1. 员工/Agent 侧通过 DuckDock 预制的对话式 Reporter 接入流程登录 DuckDock。
2. Reporter 调 `POST /api/v1/reporters/enroll` 自助登记一个运行时 endpoint,并拿到一次性显示的长期
   **Reporter Credential**(`dkr_report_*`)。
3. Reporter 定时调用 `POST /api/v1/reporters/heartbeat`,让控制台能看到 last seen、版本、计划任务状态。
4. Reporter 的日常任务默认调用 `/reports/structured`;credential 需要 `report.structured` scope。
5. Reporter 在交接/审计场景用同一个长期 credential 创建一次性上传会话,拿短期 presigned PUT URL 直传 MinIO/S3。
6. Reporter finalize 后,DuckDock 创建 Analysis Job;Analysis Worker 领取任务,用 baseline/LLM 产出
   `duckdock-analysis-v1` 标准结果文件。DuckDock 在 finalize 阶段校验 schema/枚举/报告 ID,通过后才物化入库,
   并把 recipe/prompt/model/trace/token_usage 写入 Analysis Job 摘要供控制台审计。

Reporter Credential 管理:
- `GET /api/v1/reporter-credentials`:普通用户看自己的 credential;系统 admin 看全部。
- `POST /api/v1/reporter-credentials/{id}/rotate`:返回新 token 一次,撤销同 runtime/user/device 的旧自助凭证。
- `POST /api/v1/reporter-credentials/{id}/revoke`:撤销丢失设备或退役 agent 的 credential。

管理员仍可在运行时中心手工创建/revoke report token,用于运维兜底、设备迁移或批量预配。运行时的「**上报自检**」
按钮检查 token 有效性与最近上报状态(不再是"测试外部连接")。

## 3. 离职/项目交接生命周期

```
draft → collecting → analyzing → pending_approval → approved
      → executing → verifying → completed     (旁路终态:rejected / cancelled)
```

| 阶段 | 操作 | 端点 / 权限 |
|---|---|---|
| 创建 | 选员工/接收人/范围 | `POST /handovers`(handover.manage) |
| 采集 | 拉取该员工资产 | `/handovers/{id}/collect` |
| 分析 | 生成交接项(规则版 MVP) | `/handovers/{id}/analyze` |
| 提交审批 | 指派审批人 | `/handovers/{id}/submit` |
| 审批 | **指派审批人本人**或 handover.manage 可决 | `/approvals/{aid}/decide`;全票通过 → approved |
| 执行 | 默认 **manual**(人工在厂商平台操作);auto=探针 lease(待支持) | `/execute`(须 approved,否则 409) |
| 回执 | 人工执行后提交结果+说明(=自我二次审查,留证据+审计) | `/actions/{aid}/complete` |
| 验收 | **接收人**或 handover.manage 确认 → completed | `/handovers/{id}/verify`(须 verifying) |

要点:**审批前无法执行**(409);执行/回执/验收每步进审计 + 生成证据(`USER_CONFIRM`)。

## 4. 敏感工作历程查看(reveal)

- `confidential / restricted` 级工作历程默认**只给摘要**(`metadata_json` 遮蔽)。
- 查看全文:`POST /worktraces/{id}/reveal`,**必须填原因**(≥5 字),且为**本人**或持
  `worktrace.content.read`;每次查看写审计(谁、为何、看了什么)。

## 5. 上线前检查清单(控制平面增量)

- [ ] 第一个注册用户 = 企业管理员邮箱(自动成为系统 admin)
- [ ] 给运维/平台管理员绑定 `enterprise-admin`(或细分权限键)—— 否则 403
- [ ] `.env` 设 `DUCKDOCK_CREDENTIAL_KEY`(若使用运行时凭证)
- [ ] 配置员工/Agent 自助 Reporter enrollment;仅在运维兜底时手工创建 report token
- [ ] 创建 Analysis Worker token,并启动至少一个 analysis-worker
- [ ] `alembic upgrade head`(确认 `alembic current` 与 `alembic heads` 一致)
- [ ] 其余通用项见 README §12
