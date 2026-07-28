# ADR-0207: Collector 是遥测信任边界

- Status: Accepted
- Date: 2026-07-17

## Context

Agent 可以伪造 OTLP Resource Attributes。若直接信任客户端上报的 Namespace、Runtime 或 Deployment ID，跨租户污染和错误 Gate 关联不可避免。

## Decision

- OTLP 只进入受控 OpenTelemetry Collector。
- 每个 Runtime 使用独立 mTLS 身份、短期 JWT 或受控 API Key。
- Collector 删除客户端提供的 DuckDock 治理属性。
- Collector 根据凭证映射注入 namespace、runtime、credential 和 environment。
- 基础 Run 控制信封校验 timestamp、canonical payload hash 和 idempotency key；不得把客户端 Namespace/Runtime 字段作为认证输入。
- 基础协议不要求全局单调 sequence：离线恢复与跨 Session 并发允许乱序。队列 sequence 只用于单 Adapter 实例的补传顺序和 ack cursor。
- Handshake 使用有时限的 client nonce；只有启用 cryptographic/workload attestation 的 profile 才执行 server challenge-response。
- 信任必须拆成两个正交字段：`trust_level=CHANNEL_AUTHENTICATED|PRODUCER_ATTESTED|UNVERIFIED` 表示保证强度，`trust_source=REPORTER|COLLECTOR|IMPORT|ADMIN` 表示入口来源。
- Bearer/mTLS/API key 最多建立 `CHANNEL_AUTHENTICATED`；只有 `AttestationVerifierPort` 成功才能升级为 `PRODUCER_ATTESTED`。Checksum 只证明字节完整性，不能升级信任等级。

## Consequences

- 可信身份不会由 Agent 自报。
- Collector 配置成为安全关键资产，必须版本化和测试。
- 本地开发需要可用的测试身份映射。

## Validation

- 伪造 Namespace/Runtime 属性被覆盖或拒绝。
- 重放 handshake nonce、同 key 不同 payload 和失效 attestation challenge 被拒绝并审计。
- Reporter、Collector 和 Import 的相同保证强度使用相同 `trust_level`，同时由 `trust_source` 保留来源差异。
- Trace 与控制信封缺失任一侧时产生可见完整性告警。
