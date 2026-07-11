# DuckDock 下一步开发路线图与未验证台账

> 最后更新: 2026-07-09
> 目标: 把“已实现”“已验证”“待目标环境验证”和“明确未来项”分开记录，避免用旧 PRD 或旧路线图误导后续开发。

## 1. 当前结论

DuckDock 当前主线已经从早期“注册表 + 控制平面原型”推进到 **Skills 注册表治理 + Reporter Credential + 结构化轻报告 + Worker-first 交接包入库 + 交接闭环 + 生产硬化** 的可运行形态。

这次重审没有发现新的阻断性半成品；主要收敛点是把文档里的旧说法修正为当前真实状态，并把还没有真实验证的项目单独列账。

## 2. 已实现并有自动化覆盖的主链路

| 能力 | 当前状态 | 证据口径 |
|---|---|---|
| Skills 私有注册表 | 已实现 | namespace / skill / version / artifact / ClawHub discovery / public release / release gate |
| 发布门禁 | 已实现 | static scan、sandbox、clinic、release gate、公开分享审批 |
| IAM / RBAC / SSO | 已实现 | system admin、role binding、namespace 权限、OIDC/LDAP、Fernet 凭证、企业 UID、identity link、SSO claim/group→RoleBinding JIT 映射 |
| Push-only Reporter 上报 | 已实现 | 日报/周报 `/reports/structured` 轻量入库；交接/审计包 `report_upload_sessions` FSM、MinIO presigned PUT、finalize 校验 |
| Reporter Credential | 已实现 | `/reporters/enroll`、`/reporters/heartbeat`、`dkr_report_*`、`report.structured` / `report.upload` scope、rotate/revoke、legacy report token 兼容 |
| Worker-first 分析入库 | 已实现 | 交接/审计包 finalize 创建 `ReportAnalysisJob`，analysis-worker lease/finalize，标准结果文件校验后 materialize |
| Analysis Worker 扩容模型 | 已实现 | MySQL lease + worker token + reaper；可多副本消费积压队列 |
| 交接闭环 | 已实现 | collect/analyze/submit/approve/execute/manual receipt/verify/package；敏感项证据强制 |
| 生产硬化 | 已实现 | SOPS/age `.env.prod.enc`、`/readyz`、prod preflight、优雅停机、审计 GC、compose profile 约束 |
| P3-11 E2E 骨架 | 已实现 | Playwright auth smoke + seeded handover closed-loop spec + 后端 seed 夹具 |

## 3. 已实现并完成集成验证的补充链路

| 项 | 当前状态 | 证据口径 |
|---|---|---|
| **Hermes Reporter 集成** | 已在隔离测试环境跑通 self-enroll → heartbeat → structured report / pack upload → analysis-worker → 管理员与员工时间线可见；默认入口为结构化日报/周报，pack 仅用于 handover/audit | 验收步骤、数据边界与预期状态见 `workbuddy-reporter-validation.zh-CN.md`；仓库不记录环境专属 runtime、credential 或 report ID |
| **WorkBuddy Runtime MCP 集成** | 已验证 `duckdock.reporter.run_structured`、heartbeat、结构化入库、runtime overview 与自动化 create/delete 桥 | MCP 自检、结构化报告 schema 和 API 结果均有自动化测试；周期自主执行仍需目标桌面环境复验 |

## 4. 已实现但尚待目标环境验证

这些能力已经实现，但仍需要部署方在目标环境完成验收并保留记录。

| 项 | 当前状态 | 还需要什么 |
|---|---|---|
| **生产 backup→restore 演练** | 机制、脚本和 checklist 已具备 | 一台可覆盖恢复的 staging/部署主机、SOPS age 私钥、真实 `.env.prod.enc`、备份目录和恢复窗口 |
| **WorkBuddy 周期自主执行** | 结构化上报与自动化桥已实现并有隔离环境验证 | 在目标桌面环境注册并重载 MCP，创建 `duckdock.reporter.schedule`，观察至少一次真实触发；交接/审计 pack 模式另行验收 |
| **生产 LLM Provider 长稳验证** | 统一 LLM 客户端和 `ai_assist` 降级显式化已实现；支持 OpenAI-compatible 配置 | 用生产目标 key/base_url/model 跑一批报告包，记录 latency、token usage、降级率和失败重试表现 |
| **企业 IdP 现场 JIT 验证** | 后端已支持 `users.enterprise_uid`、`identity_links`、`sso_role_mappings`，OIDC/LDAP 登录时可按企业 UID 归并用户并按 claim/group 自动授予或撤销由 SSO 管理的 `RoleBinding`；前端 `/iam` 已可管理 identity links / role mappings | 需要一套真实或 staging IdP：确认 claim 名称、group 值、企业 UID 字段、回调 URL、退出/禁用用户策略 |
| **架构图 PNG 同步** | SVG 是权威源；本次会更新 SVG/README/说明 | 需要稳定 SVG→PNG 渲染工具时刷新 PNG 阅读版 |

## 5. 仍需产品拍板的设计口

这些是 006 之后保留的策略口，不影响当前代码运行，但会影响企业落地方式。

| 决策口 | 当前实现 | 待拍板方向 |
|---|---|---|
| Reporter self-enrollment | 当前允许已认证用户自助 enroll，一个员工/agent 可拿长期可撤销 `dkr_report_*` | 是否增加 org unit invite/approval、设备白名单或管理员预授权策略 |
| Runtime 建模粒度 | `RuntimeInstance` 作为 endpoint，`ReporterCredential.device_id` 可区分设备/agent | 企业策略是一人一个 runtime，还是每个 agent/device 一个 runtime |
| Heartbeat 历史 | 当前记录 latest heartbeat / last seen / credential last-used | 是否新增 N 天心跳历史表，用于 SLA、离线趋势和审计 |
| MCP 产品化 | `tools/duckdock-runtime-mcp` 第一版已存在 | 是否纳入企业桌面镜像、提供后台下载接入包和版本升级策略 |

## 6. 明确未来项 / 当前非目标

| 项 | 说明 |
|---|---|
| FR-019 auto 探针 lease | 当前 `manual` 是默认且可用；`auto` 在后端以 501 阻断，不创建悬空执行动作。现场探针执行器另立特性。 |
| 服务端 Pull 厂商适配器 | Pull 链已退役；生产路径是 Reporter Push。OpenClaw backup 上传式导入作为兼容/导入能力保留。 |
| L3 自治执行 / clinic 自愈 | LLM 只给建议和结构化分析，不做自治处置。自动修复、自动执行、clinic 自愈都不属于当前闭环。 |
| Registry 运营增强 | 下载趋势、热门技能、命名空间活跃、更多运营图表可做，但不是当前主线阻断。 |
| SCIM / 高级 IAM | OIDC/LDAP JIT 与 SSO 映射前端已可用；SCIM、HRIS 主动目录同步和复杂组织生命周期治理属于后续企业集成。 |
| `alembic downgrade base` 预存 bug | 不影响生产 `upgrade head`，仍作为非生产路径问题单独处理。 |

## 7. 推荐下一轮执行顺序

1. **WorkBuddy 周期自主执行复验**
   结构化轻报告、Runtime MCP 直接上报和 WorkBuddy 自动化 create/delete 已经跑通；下一步重载 WorkBuddy 后创建 weekly schedule，观察一次真实到点执行。

2. **CI 闭环 E2E 稳定性维护**
   保持阻塞 job 稳定，及时修复启动等待、seed 或浏览器环境问题。

3. **生产环境 backup→restore 演练**
   在 staging 主机用真实 SOPS age key 跑 `prod.sh backup` / restore runbook，记录恢复日志和耗时。

4. **Reporter 策略固化**
   根据集成反馈确定 self-enrollment、runtime 粒度、heartbeat 历史，必要时新增 org policy 和历史表。

5. **企业 IAM 目标环境验证**
   接一套真实 IdP 验证企业 UID / group claim，再设计 SCIM/HRIS 同步。

6. **治理产品化增强**
   再做 registry 运营指标、Clinic 校准集、MCP 企业预置包等增强项。

## 8. 开工判据

下一步优先完成 **WorkBuddy 周期自主执行复验** 或 **生产 backup→restore 演练**。Hermes 与 WorkBuddy 的集成验证已经覆盖三个关键假设：

- 员工侧长期 credential + 定时任务是否顺手。
- 结构化轻报告和 Analysis Worker 重包输出是否都能支撑 admin 控制台的个人 agent 时间线。
- 管理员能否基于资产、工作历程、证据和交接信号做长线治理。

下一轮应把 WorkBuddy 重载后的定时执行和生产恢复演练补齐，让 DuckDock 从“代码主线完成”进一步进入“部署与试运行可复制”的阶段。
