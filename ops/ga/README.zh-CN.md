# GA 授权文件

`production-authorization.example.json` 是 DuckDock 2.0 正式生产授权模板。
复制到受控证据目录后填写，禁止把真实人员身份、内部报告路径、签名或
allowed-signers 误提交到公开仓库。

`approval-policy.example.json` 是独立的组织信任根模板。它必须由发布机构而非
任一审批者通过只读控制路径提供，绑定唯一共享 allowed-signers 文件及四个角色
允许的精确身份。授权文件只保存 policy ID 和 SHA-256；个人 approval 不允许覆盖
信任库。正式验证必须使用 `--approval-policy` 显式选择受控策略文件。

结构检查：

```bash
python backend/scripts/verify_ga_production_authorization.py \
  ops/ga/production-authorization.example.json --lint
```

完整目标文件必须通过内容摘要、证据新鲜度和每类目标报告的内容级解析，不能用
无关 JSON 配合表层声明通过。Application readiness 必须由
`backend/scripts/collect_ga_target_readiness.py` 通过目标 HTTPS 采集；TLS、容量和 HA
使用各自工具输出。以下模板定义了其余必须由目标执行结果填充的版本化协议：

- `secrets-evidence.example.json`：secret store 与真实轮换结果；
- `network-evidence.example.json`：外部端口扫描与 CNI ingress/egress 负向测试；
- `alerting-evidence.example.json`：firing、值班确认和 resolved 三张回执；
- `recovery-evidence.example.json`：签名备份与非生产破坏性恢复；
- `independent-security-evidence.example.json`：独立渗透/代码审查和签名报告；
- `high-availability-evidence.example.json`：目标多故障域故障注入。
- `state-services-ha-evidence.example.json`：托管 MySQL、Redis、对象存储与 RWX
  的真实故障切换回执；必须由审批策略中的 Operations 身份签名。

所有这些目标报告以及 readiness v1、TLS v2、容量 v2 报告都必须绑定相同 target
ID、source commit、backend/frontend 镜像摘要；报告内部 `observed_at` 必须与授权
文件的证据时间相同。模板中的 PASS 值只
描述合格结构，不是可提交的证据，所有 `__CHANGE_ME` 和示例快照都必须替换为
实际回执。完整流程见
[`docs/ga-production-authorization.zh-CN.md`](../../docs/ga-production-authorization.zh-CN.md)。
