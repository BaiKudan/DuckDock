# Clinic 回归手册

本文档说明如何在 DuckDock 中运行 `Clinic` 回归测试，以及如何用 Promptfoo 做探索式对比。

## 1. 回归目标

当前回归拆成两层：

- 稳定回归：使用 Python 脚本跑固定 fixture，并做严格断言
- 探索评估：使用 Promptfoo 读取同一批 fixture，便于人工对比输出

## 2. Fixture 位置

当前黄金样例集位于：

- [`clinic_golden_set.v1.jsonl`](../backend/app/clinic_assets/fixtures/clinic_golden_set.v1.jsonl)

每条 fixture 当前支持以下字段：

- `id`
- `namespace`
- `mode`
- `requires_judge`
- `notes`
- `expected_overall_range`
- `dimension_assertions`
- `skills`
- `scan_results`
- `prev_scores`

## 3. 运行稳定回归

默认只跑稳定 fixture，不跑依赖真实 LLM judge 的用例：

```powershell
python scripts/run_clinic_regression.py
```

查看机器可读输出：

```powershell
python scripts/run_clinic_regression.py --json
```

只跑某个 fixture：

```powershell
python scripts/run_clinic_regression.py --case deterministic-strong-quality
```

## 4. 运行 live judge fixture

当你要验证百炼 `qwen3.6-plus` 的真实 judge 行为时，显式带上：

```powershell
python scripts/run_clinic_regression.py --include-live
```

说明：

- `requires_judge=true` 的 fixture 默认不会跑
- 这样做是为了避免 CI 或本地日常开发被网络波动影响

## 5. Promptfoo 工作台

Promptfoo 配置已预留在：

- [`promptfooconfig.clinic.yaml`](../promptfoo/promptfooconfig.clinic.yaml)
- [`clinic_provider.py`](../promptfoo/clinic_provider.py)

安装并运行示例：

```powershell
cd promptfoo
npx promptfoo@latest eval -c promptfooconfig.clinic.yaml
```

环境注意：

- 当前 Promptfoo 发布版要求 Node `^20.20.0 || >=22.22.0`
- 如果本机 Node 版本低于这个要求，CLI 可能直接启动失败
- 遇到这种情况，先升级 Node，再执行 Promptfoo 工作台命令

这个配置当前定位是：

- 读取同一份 fixture
- 调用本地 Python provider
- 返回完整 Clinic 评测结果供人工审阅

它现在不承担 CI 严格断言，CI 还是以 `scripts/run_clinic_regression.py` 为准。

## 6. 推荐工作流

日常开发：

1. 修改 rubric / judge prompt / Clinic 逻辑
2. 先跑 `scripts/run_clinic_regression.py`
3. 如果需要观察 live judge 变化，再跑 `--include-live`
4. 如需人工对比不同 prompt 输出，再用 Promptfoo 工作台

## 7. 后续建议

后续可以继续补：

- fixture 分层：`deterministic` / `hybrid` / `regression-critical`
- Promptfoo 断言脚本
- 将 live fixture 结果写入 Langfuse trace
