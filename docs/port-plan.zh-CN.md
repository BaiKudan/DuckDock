# DuckDock 端口说明

本文档记录 DuckDock 的默认开发端口和冲突处理方式。默认值用于让脚本、Compose、文档和本地调试保持一致，并不要求部署环境永久使用这些 host 端口。

## 1. 默认端口

| 服务 | Host 端口 | Container 端口 | 说明 |
|---|---:|---:|---|
| Frontend | `5174` | `5174` | Vite 开发服务器 |
| Backend | `8801` | `8801` | FastAPI |
| MySQL | `3307` | `3306` | 主业务库 |
| Redis | `6379` | `6379` | Celery 与限流 |
| MinIO API | `9000` | `9000` | S3-compatible API |
| MinIO Console | `9001` | `9001` | 管理控制台 |
| PostgreSQL | `5432` | `5432` | 仅 observability profile |
| Langfuse Web | `3200` | `3000` | 仅 observability profile |
| ClickHouse | 不暴露 | `8123` | 仅 observability 内网 |

`analysis-worker` 和 `observability` 都是可选 profile，默认 `docker compose up` 不启用。

## 2. 启动方式

macOS、Linux 和 WSL 推荐：

```bash
bash scripts/dev.sh up
```

Windows 可使用混合模式：

```powershell
.\scripts\dev-start.ps1
```

也可以直接启动默认 Compose 栈：

```bash
docker compose up -d
```

## 3. 端口冲突处理

如默认 host 端口与其他本地服务冲突，只修改 Compose 端口映射左侧的 host 端口：

```yaml
ports:
  - "<new-host-port>:<existing-container-port>"
```

container 端口和服务间内部 URL 属于应用契约，不应随 host 映射一起修改。调整后同步更新本地 `.env`、反向代理配置和浏览器访问地址。

提交端口变更前至少运行：

```bash
docker compose config
docker compose --profile analysis-worker config
docker compose --profile observability config
```

生产环境通过 nginx 统一入口暴露服务，参见 [`production-deployment.md`](production-deployment.md)。
