# DuckDock Git 全历史秘密扫描

DuckDock 的内部安全预审和 backend CI 都必须使用 Gitleaks 扫描完整 Git 历史，
不能把普通 `grep`、当前工作树扫描或 GitHub 的浅克隆当成等价证据。

## 固定供应链

- 扫描器：[Gitleaks v8.30.1](https://github.com/gitleaks/gitleaks/releases/tag/v8.30.1)
- 发布 tag commit：`83d9cd684c87d95d656c1458ef04895a7f1cbd8e`
- 官方多架构 OCI 镜像：
  `ghcr.io/gitleaks/gitleaks:v8.30.1@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f`

入口为 `scripts/run-gitleaks.sh`。容器以当前 UID/GID、只读根文件系统、只读仓库、
无网络、无 Linux capabilities 和 `no-new-privileges` 运行；报告必须写在仓库外，
始终使用 100% redact。未显式传入 `--log-opts`，因此固定版本执行其默认的
`git log -p -U0 --full-history --all --diff-filter=tuxdb` 全引用历史扫描。

本地运行：

```bash
report_parent="$(mktemp -d)"
bash scripts/run-gitleaks.sh "$report_parent/duckdock-gitleaks.json"
```

## Reviewed fingerprint baseline

首次扫描 217 个 commit、约 19.71 MB diff，得到 33 条候选。所有输出均在 100%
redact 下审阅，候选分为：

| 类别 | 结论 |
|---|---|
| 测试或 CI 固定夹具 | 只用于本地密码学/拒绝路径，不对应外部账号或生产 secret |
| schema、digest、idempotency、object-key | 高熵标识符被 `generic-api-key` 误报 |
| 私钥材料检测器常量 | 归档器用于拒绝私钥的字符串本身被 `private-key` 规则命中 |
| OTLP smoke `curl -u` | 认证值来自运行时环境变量，仓库没有实际 credential |

基线只存放于 `.gitleaksignore`，仅包含这 33 个精确 fingerprint。禁止使用以下方式
消除告警：

- 按整个 commit、目录、测试目录或规则放行；
- 把 redact 关闭后将原始匹配写入 issue、日志或评审文档；
- 仅因字符串看起来像 fixture 就添加 fingerprint，而不确认它不对应外部系统；
- 为使 CI 通过而改写 Git 历史。

新增候选必须先按泄露事件处理：识别归属、立即轮换/吊销、确认日志和构建产物传播
范围，再决定是否需要清理历史。只有可证明不具备外部权限的精确假阳性，才能经
Security review 后增加单个 fingerprint。

## CI 与 GA 边界

backend CI checkout 使用 `fetch-depth: 0`，运行相同入口并保留 90 天的 redact JSON
报告。内部安全预审也运行该门禁，并把报告纳入内容寻址回执。

全历史扫描通过只证明仓库没有被当前规则发现的未基线秘密。它不能证明目标 Secret
Manager 配置、运行时环境变量、CI organization secret、容器层、外部日志或备份介质
安全，也不能替代独立第三方安全评估。
