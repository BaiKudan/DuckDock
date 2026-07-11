# DuckDock 业务架构图集

使用 `fireworks-tech-graph` Flat Icon 风格绘制，覆盖当前项目的总体架构和主要功能流程。每张图都有 SVG 源文件，并可导出同名 PNG。

| 图 | 说明 |
|---|---|
| `duckdock-business-function-architecture.svg` | 业务功能架构：使用入口、运行时接入、Reporter 上报、分析归档、资产确认、交接闭环、私有 Skill 仓库、质量治理和组件管理 |
| `duckdock-overall-architecture.svg` | 总体业务架构：运行时、控制平面、存储、异步分析和可选组件 |
| `duckdock-iam-portal-routing-flow.svg` | 身份入口与权限分流：统一登录、管理员后台、员工 Portal、离职交接开关 |
| `duckdock-reporter-upload-flow.svg` | Reporter 接入与上报：私有 Registry、Reporter Credential、MinIO 直传、analysis job |
| `duckdock-analysis-llm-worker-flow.svg` | Analysis Worker：MySQL lease、下载包、baseline/LLM 分析、结果回写、materialize |
| `duckdock-asset-handover-workspace-flow.svg` | 个人工作台与交接闭环：员工确认、岗位状态、审批/执行/回执/验收 |
| `duckdock-skill-registry-governance-flow.svg` | 私有 Skill 仓库：版本、扫描、Sandbox、Clinic、分发 |
| `duckdock-component-observability-flow.svg` | 组件管理：核心服务与 Langfuse 可选观测解耦 |

## 当前验证边界

- SVG 是权威源；PNG 阅读版已按当前 SVG 重新导出。
- P3-11 seeded 闭环由 CI 阻塞 job 启动完整 dev 栈运行。
- 生产环境 backup→restore 和 WorkBuddy 周期 Reporter 仍需部署方在目标环境验收。
