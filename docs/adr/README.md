# DuckDock Architecture Decision Records

本目录记录影响 DuckDock 2.0 跨领域边界、数据所有权、兼容性和安全模型的架构决策。

状态定义：

- Proposed：仍在评审，不能作为实现依据。
- Accepted：已批准，后续实现必须遵守。
- Superseded：已被新的 ADR 替代。
- Deprecated：保留历史背景，不再用于新实现。

DuckDock 2.0 Foundation 决策：

| ADR | 决策 |
|---|---|
| 0201 | WorkTrace 与 AgentRun 分离 |
| 0202 | MySQL 与遥测数据面边界 |
| 0203 | OTLP 实时、ATIF 归档 |
| 0204 | OTel GenAI/OpenInference 兼容层 |
| 0205 | Langfuse 是可替换 Provider |
| 0206 | DeepEval 是评测执行引擎 |
| 0207 | Collector 是遥测信任边界 |
| 0208 | ReleaseGate 只消费不可变证据 |
| 0209 | 内容采集默认关闭 |
| 0210 | 业务事件使用 Transactional Outbox |
| 0211 | Evidence 使用可空 typed WorkTrace link |
| 0212 | 历史租户归属使用逐 ID 显式 remediation manifest |

每个 ADR 必须包含状态、背景、决策、后果和验证方式。改变 Accepted 决策时新增 ADR，不覆盖历史原因。
