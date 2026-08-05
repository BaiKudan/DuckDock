# 安全策略

DuckDock 管理 Agent 资产、凭证、审计与交接证据。请负责任地披露安全问题，不要在公开 Issue、Pull Request、日志或截图中发布漏洞细节、真实令牌或业务数据。

## 支持范围

| 版本 | 安全更新 |
|---|---|
| `main` 与最新正式发布 | 支持 |
| 更早的提交、分支或自行修改的版本 | 不保证 |

当前候选版本为 `2.0.0-rc.1`，尚未完成独立第三方安全审计；RC 不构成生产部署授权。仓库已提供 TLS/分网/告警/异地加密备份/HA 参考实现和机器授权门禁，但部署方仍必须在目标环境产生证据。独立评估范围见 [`docs/security/2.0-independent-assessment-brief.zh-CN.md`](docs/security/2.0-independent-assessment-brief.zh-CN.md)，最终授权流程见 [`docs/ga-production-authorization.zh-CN.md`](docs/ga-production-authorization.zh-CN.md)。

## 报告漏洞

请通过 GitHub 仓库的 **Security → Report a vulnerability** 私密报告入口提交。仓库维护者应在公开仓库前启用 GitHub Private Vulnerability Reporting。

报告中请包含：

- 受影响的版本或 commit；
- 前置条件、复现步骤与影响范围；
- 可证明问题的最小请求、日志或代码；
- 已知的缓解方式；
- 便于协调修复与披露的联系方式。

提交前请移除真实凭证和个人/企业数据。不要对不属于你的系统进行扫描、获取持久访问、破坏数据、横向移动或拒绝服务测试。

维护者会尽快确认收到报告、评估严重性并协调修复和披露时间。修复发布前请保持漏洞信息私密。
