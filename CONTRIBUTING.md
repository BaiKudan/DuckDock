# 为 DuckDock 贡献

感谢你愿意改进 DuckDock。项目目前处于 Alpha 阶段，优先接受可复现的缺陷修复、安全加固、测试、文档与边界清晰的小型功能。

## 开始之前

- 先阅读 [`README.md`](README.md)、[开发运维文档](docs/dev-operations.zh-CN.md) 与 [项目约束](.specify/memory/constitution.md)。
- 缺陷与功能建议使用 GitHub Issue；安全漏洞不要在公开 Issue 中披露，按 [`SECURITY.md`](SECURITY.md) 报告。
- 不要提交真实账号、访问令牌、业务数据、客户名称、私钥或生产配置。示例数据必须是虚构内容。
- 大范围功能或数据模型变更先开 Issue 说明目标、边界、迁移与兼容策略，再开始实现。

## 本地开发

推荐使用 Docker Compose 启动完整开发栈：

```bash
cp .env.example .env
bash scripts/dev.sh up
```

详细命令、Windows 备选方式和端口说明见 [`docs/dev-operations.zh-CN.md`](docs/dev-operations.zh-CN.md)。首次启动后，在前端注册的第一个用户会成为系统管理员。

## 变更要求

- 延续现有模块边界与编码风格，避免夹带无关重构。
- 正确性、安全、权限、状态机、幂等或迁移行为发生变化时，先增加能复现问题的测试。
- 业务主库只使用 MySQL 8.x 兼容语义；迁移必须支持空库 `upgrade head`，并提供可执行的 downgrade。
- 敏感信息遵循最小暴露原则；凭证不得明文持久化；LLM 输出只能作为建议，不得绕过人工审批与审计。
- 用户可见行为或部署方式变化时，同步更新 README、运行手册或架构文档。

## 提交前检查

首次使用本机工具链时先安装开发依赖：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements-dev.txt
```

后端：

```bash
cd backend
../.venv/bin/python -m ruff check app alembic
../.venv/bin/python -m mypy app
../.venv/bin/python -m pytest
../.venv/bin/python -m alembic -c alembic.ini upgrade head
../.venv/bin/python -m alembic -c alembic.ini check
```

`mypy` 目前采用 [`backend/.mypy-baseline`](backend/.mypy-baseline) 递减门槛，错误数不得高于基线；完整判定逻辑以 [CI workflow](.github/workflows/ci.yml) 为准。

前端：

```bash
cd frontend
npm ci
npm run lint
npm test
npm run build
```

Compose：

```bash
cp .env.example .env
docker compose config --services
docker compose --profile analysis-worker config --services
```

Pull Request 请说明问题、实现方式、风险、迁移影响和实际执行过的验证。一个 PR 尽量只解决一个主题。

## 贡献许可

DuckDock 采用 [MIT License](LICENSE)。提交代码、文档、设计资源或其他内容，即表示你确认自己有权提交这些内容，并同意将该贡献按 MIT License 授权给项目使用和再分发。引入第三方内容时，必须确认其许可证兼容并保留原有版权与许可声明；不能确认来源或授权的内容不得提交。
