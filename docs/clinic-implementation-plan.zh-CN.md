# Clinic 实施计划

本文档是 `docs/clinic-development-plan.zh-CN.md` 的执行版，面向当前仓库的实际改造工作。

## 目标

将当前 `Clinic` 从“启发式打分器”重构为以下结构：

- 静态检查器
- Judge Provider
- Rubric / Prompt / Fixtures 资产层
- 带 evidence / confidence 的报告输出

## 本次执行范围

### 执行项 1

重构 [`clinic_service.py`](../backend/app/services/clinic_service.py) 为：

- `ClinicFactsExtractor`
- `ClinicJudgeProvider`
- `ClinicService`

### 执行项 2

接入百炼 `qwen3.6-plus` 做 JSON judge：

- 优先读取专用 `CLINIC_*` 配置
- 未配置时回退到现有 Skill 生成配置

### 执行项 3

资产化评测内容：

- Rubric：`YAML`
- Judge Prompt：`Markdown`
- Fixtures：`JSONL`

### 执行项 4

扩展 Clinic 报告输出：

- `confidence`
- `evidence`
- `reasoning_summary`

### 执行项 5

整理下一步接入建议：

- Langfuse
- Promptfoo

## 不在本次范围

- 数据库新表和 Alembic migration
- Langfuse 实际接入
- Promptfoo 实际集成执行
- 发布门禁与自动阻断

## 代码落点

### 后端

- [`backend/app/core/config.py`](../backend/app/core/config.py)
- [`backend/app/models/clinic.py`](../backend/app/models/clinic.py)
- [`backend/app/schemas/clinic.py`](../backend/app/schemas/clinic.py)
- [`backend/app/services/clinic_service.py`](../backend/app/services/clinic_service.py)
- [`backend/app/workers/clinic_tasks.py`](../backend/app/workers/clinic_tasks.py)
- `backend/app/clinic_assets/*`

### 前端

- [`frontend/src/api/client.ts`](../frontend/src/api/client.ts)
- [`frontend/src/pages/ClinicReportPage.tsx`](../frontend/src/pages/ClinicReportPage.tsx)
- [`frontend/src/i18n.ts`](../frontend/src/i18n.ts)

## 实施顺序

1. 先补配置与资产目录
2. 重写 Clinic 服务层
3. 扩展 schema 和前端类型
4. 更新报告页展示
5. 编译与构建验证
6. 输出 Langfuse / Promptfoo 后续接入建议

## 验收标准

- 可以继续通过现有 Clinic API 触发评测
- 若配置了百炼 API，LLM 维度可输出结构化 JSON judge 结果
- 未配置百炼 API 时，静态维度仍可正常工作
- 报告页能展示 evidence 和 confidence
- 后端 `compileall` 通过
- 前端 `npm run build` 通过

## Langfuse 接入状态

当前仓库已完成 Langfuse 的可选埋点接入，但默认关闭。

已实现：

- `clinic-evaluation` 根 span
- 每个 LLM 维度的 generation 埋点
- 失败 fallback 记录

详细配置与使用方式见：

- [`clinic-langfuse.zh-CN.md`](clinic-langfuse.zh-CN.md)

## 后续接入建议

### Langfuse 下一步

适合继续补：

- token / cost 记录
- fixture 与 trace 关联
- report 页面反向展示 trace id 或 URL

### Promptfoo

适合在下一阶段引入，用于：

- 维护 Clinic 黄金样例集
- 跑 prompt 回归测试
- 对比不同 prompt 版本的 judge 稳定性

建议接入方式：

- 以 `backend/tests/fixtures/*.jsonl` 为基础生成 Promptfoo dataset
- 将 Judge Prompt 模板纳入 Promptfoo 的 prompt 管理
