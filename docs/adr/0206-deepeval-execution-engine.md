# ADR-0206: DeepEval 是评测执行引擎，不是事实源

- Status: Accepted
- Date: 2026-07-17

## Context

DeepEval 提供 Agent、Tool、任务完成度和轨迹效率评测，但不应承担 DuckDock 的数据集治理、调度、身份或最终发布决策。

## Decision

- DeepEval 通过 EvaluationEnginePort 在独立 Celery queue 中执行。
- DuckDock 固定 DatasetVersion、EvaluatorVersion、Judge 配置和输入 hash。
- 详细输入输出进入 MinIO，结果摘要和 Evidence 引用进入 MySQL。
- 分数可以镜像到 Langfuse；镜像失败不得改变 DuckDock EvaluationRun 事实。
- 确定性 Evaluator 与 LLM Judge 并存。

## Consequences

- 可替换评测库而不迁移治理数据。
- 需要对模型限流、预算、超时和部分失败进行统一封装。
- LLM Judge 结果必须校准后才能参与强制 Gate。

## Validation

- Fake Engine 和 DeepEval Engine 通过同一 Contract。
- 同一幂等键和输入 hash 不重复运行。
- 超预算、超时和部分失败产生明确状态，不默认为通过。
