# Deployment Conversation Examples

## WorkBuddy First-Time Setup

User:

```text
帮我把这个 WorkBuddy 接入 DuckDock，每周五下午四点自动上报。
```

Assistant:

```text
我会部署 DuckDock Reporter。默认只上报企业 AI 资产索引、会话摘要、产物 hash、路径和交接风险，不上传原始私聊全文、密钥、cookie 或个人系统目录。是否继续？
```

User:

```text
继续。
```

Assistant:

```text
请提供 DuckDock API 地址、Runtime ID 和 Reporter Token。Token 只会写入本机安全配置，之后不会在对话里回显。
```

User:

```text
API 是 https://duckdock.example.com/api/v1，Runtime ID 是 12，Token 是 dkr_report_xxxx_xxxxxxxxx。
```

Assistant:

```text
配置已保存。现在我会先做一次试运行，只生成本地 DuckDock Pack，不上传。是否执行？
```

User:

```text
执行。
```

Assistant:

```text
试运行完成：
- assets: 18
- sessions: 42
- memories: 6
- artifacts: 27
- redactions: 5
未上传。是否创建每周五 16:00 的定时上报任务？
```

User:

```text
创建。
```

Assistant:

```text
定时任务已创建：DuckDock Weekly Reporter。是否现在执行一次测试上传？
```

## Change Schedule

User:

```text
把 DuckDock 上报改成每天晚上八点。
```

Assistant:

```text
我将把 DuckDock Weekly Reporter 的计划从每周五 16:00 改为每天 20:00。采集范围不变，仍为摘要和索引模式。是否确认修改？
```

## Pause

User:

```text
暂停 DuckDock 上报。
```

Assistant:

```text
我会禁用 DuckDock Weekly Reporter 定时任务，但保留本地配置和 Token，方便恢复。是否确认？
```

## Uninstall

User:

```text
把 DuckDock Reporter 从这台 WorkBuddy 移除。
```

Assistant:

```text
我会删除 DuckDock 定时任务和本地 Reporter 配置。已上传到 DuckDock 的历史记录不会被删除。是否确认？完成后还需要 DuckDock 管理员在控制平面撤销 Runtime Report Token。
```
