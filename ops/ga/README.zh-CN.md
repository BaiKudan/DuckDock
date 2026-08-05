# GA 授权文件

`production-authorization.example.json` 是 DuckDock 2.0 正式生产授权模板。
复制到受控证据目录后填写，禁止把真实人员身份、内部报告路径、签名或
allowed-signers 误提交到公开仓库。

结构检查：

```bash
python backend/scripts/verify_ga_production_authorization.py \
  ops/ga/production-authorization.example.json --lint
```

完整目标文件必须通过内容摘要、证据新鲜度、目标 TLS/容量报告解析、独立安全
评估和四份 OpenSSH 签名验证。流程见
[`docs/ga-production-authorization.zh-CN.md`](../../docs/ga-production-authorization.zh-CN.md)。
