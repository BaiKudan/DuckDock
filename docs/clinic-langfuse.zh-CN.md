# Clinic Langfuse 接入说明

当前 DuckDock 已经为 `Clinic` 评测接入了可选的 Langfuse 埋点，但默认关闭。

## 1. 当前埋点范围

开启后会记录两层观测：

- `clinic-evaluation`
  - 一次完整命名空间评测的根 span
- `clinic-judge-{dimension}`
  - 每个 LLM 维度的 generation

当前覆盖的 LLM 维度：

- `skill_quality`
- `skill_coherence`
- `style_consistency`
- `interaction_quality`

## 2. 配置项

在 `.env` 中配置：

```env
CLINIC_LANGFUSE_ENABLED=true
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_BASE_URL=https://cloud.langfuse.com
LANGFUSE_ENVIRONMENT=development
LANGFUSE_TIMEOUT_SECONDS=5
```

说明：

- 没有配置 key 时，Langfuse 自动关闭
- 即使 Langfuse 不可用，也不会影响 Clinic 评测结果

## 3. 当前记录内容

根 span 会记录：

- namespace 名称
- skill 数量
- 评测模式
- rubric 版本
- prompt 版本
- overall score
- grade

维度 generation 会记录：

- dimension id
- model
- facts summary
- sampled skill names
- judge JSON 输出
- 错误时的 fallback 信息

## 4. 代码位置

- [`backend/app/services/langfuse_service.py`](../backend/app/services/langfuse_service.py)
- [`backend/app/services/clinic_service.py`](../backend/app/services/clinic_service.py)
- [`backend/app/workers/clinic_tasks.py`](../backend/app/workers/clinic_tasks.py)

## 5. 建议使用方式

推荐顺序：

1. 先用 `scripts/run_clinic_regression.py` 稳定回归
2. 再开启 Langfuse 观察真实 judge 行为
3. 调整 rubric / prompt 后，对比不同 trace 的输出差异

## 6. 当前边界

这版 Langfuse 接入只做了埋点，不做：

- dataset 管理
- experiment 管理
- 在线告警
- trace 到页面的反向链接展示

如果后续继续扩展，建议优先补：

- generation token / cost 记录
- fixture 与 trace 的关联
- report 页面显示 trace id 或 trace URL
