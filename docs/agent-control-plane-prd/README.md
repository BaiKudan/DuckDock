# DuckDock 企业 AI Agent 资产与交接控制平面 PRD

本文档集用于指导 DuckDock 从“私有 Skills 仓库”升级为“企业 AI Agent 资产与交接控制平面”。

核心定位：

> DuckDock 是企业 AI Agent 资产控制平面；专有 OpenClaw 是它的智能采集、理解和执行探针。

## 文档目录

| 文件 | 用途 |
|---|---|
| [01-product-prd.zh-CN.md](01-product-prd.zh-CN.md) | 产品定位、目标用户、业务场景、功能范围、MVP 定义 |
| [02-architecture-and-stack.zh-CN.md](02-architecture-and-stack.zh-CN.md) | 前后端分离架构、FastAPI/MySQL 技术栈、中间件选型 |
| [03-data-model-and-mysql.zh-CN.md](03-data-model-and-mysql.zh-CN.md) | MySQL 数据域、核心表、状态机、索引与迁移策略 |
| [04-api-contract.zh-CN.md](04-api-contract.zh-CN.md) | REST API 设计、权限边界、关键请求响应模型 |
| [05-frontend-prd.zh-CN.md](05-frontend-prd.zh-CN.md) | 前端信息架构、页面清单、组件规范、交互要求 |
| [06-probe-and-adapter-spec.zh-CN.md](06-probe-and-adapter-spec.zh-CN.md) | 专有 OpenClaw 探针、ArkClaw/WorkBuddy/JVS/OpenClaw Adapter 规范 |
| [07-roadmap-and-acceptance.zh-CN.md](07-roadmap-and-acceptance.zh-CN.md) | 里程碑、开发任务拆分、验收标准、风险清单 |
| [08-implementation-backlog.zh-CN.md](08-implementation-backlog.zh-CN.md) | 可直接拆 issue 的开发 Backlog |
| [09-vendor-research.zh-CN.md](09-vendor-research.zh-CN.md) | OpenClaw/ArkClaw/WorkBuddy/JVS 能力调研结论和待确认问题 |

## 强制技术约束

- 前后端分离。
- 后端必须使用 FastAPI。
- 数据库必须使用 MySQL。
- 前端使用成熟框架和组件库，避免重复造轮子。
- 所有跨厂商采集、判断和交接动作必须可审计、可回放、可人工复核。

## MVP 一句话

先做一个能接入 OpenClaw/JVS、登记 AI 资产、生成离职交接包、走审批并完成接管留痕的企业控制平面，再逐步补 ArkClaw 原生备份接入和 WorkBuddy 浏览器采集。
