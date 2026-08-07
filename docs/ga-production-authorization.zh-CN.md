# DuckDock 2.0 GA 生产授权

DuckDock 有两个刻意分离的门禁：

1. `/api/v2/operations/ga-readiness` 证明应用数据、API 合约、发布回执、身份、
   恢复和 SLO 闭环处于 `READY`；
2. `backend/scripts/verify_ga_production_authorization.py` 证明一个具体的 `2.0.0`
   commit、镜像摘要和目标环境已经通过部署、安全、容量、HA、值班以及四方授权。

只有第二个门禁输出 `GA_AUTHORIZED` 才能称为生产 GA。应用侧 `READY`、仓库
基线 `PASS` 或 RC 验证都不能替代它。

## 执行顺序

1. 从 `ops/ga/production-authorization.example.json` 复制目标环境文件。
2. 将两个应用镜像改为流水线输出的 `registry/repo@sha256:...`，版本必须为
   `2.0.0`，commit 必须为完整 40 位。

```bash
export DUCKDOCK_GA_SOURCE_COMMIT='40-character Git commit'
export DUCKDOCK_GA_BACKEND_IMAGE='registry.example.com/duckdock/backend@sha256:64-hex-digest'
export DUCKDOCK_GA_FRONTEND_IMAGE='registry.example.com/duckdock/frontend@sha256:64-hex-digest'
export DUCKDOCK_GA_BACKEND_ATTESTATION_IMAGE='registry.example.com/duckdock/backend@sha256:index-64-hex-digest'
export DUCKDOCK_GA_FRONTEND_ATTESTATION_IMAGE='registry.example.com/duckdock/frontend@sha256:index-64-hex-digest'
export DUCKDOCK_GA_CONTRACT_DIGEST='64-character frozen API contract SHA-256'
```

3. 先建立发布供应链基础，不能把 CI 中“曾经请求过 provenance/SBOM”当成最终证据。
   `v2.0.0` 必须是签名 tag，且 peeled commit 必须等于
   `DUCKDOCK_GA_SOURCE_COMMIT`。由发布机构从
   `ops/ga/release-provenance-trust-policy.example.json` 建立内容寻址策略，固定
   builder signer、公钥、source repository、builder ID 和精确到
   `refs/tags/v2.0.0` 的 workflow ref；builder identity/key 不得与四方审批人或外部
   安全评估人复用。BuildKit 的 SLSA `builder.id` 可能包含具体 Actions run，不能把
   example 或测试值当成真实值；必须从最终 registry attestation 读取，由发布机构
   精确批准后再签原始 build report，禁止通配或前缀匹配。

   受控 release workflow 为 backend/frontend 的最终 `@sha256` 镜像分别导出 SLSA
   provenance v1、SPDX 2.3 SBOM 和机器可读漏洞扫描。归一化 SLSA 的唯一 subject
   必须是对应镜像 repository/digest，并绑定同一 source/tag/commit、builder、workflow
   run ID 和构建起止时间；扫描须在构建后 24 小时内完成、漏洞库不旧于 7 天，且无
   open Critical/High。release job 的四份 SARIF 会以 90 天 Actions artifact 留存；
   发布机构须下载 backend/frontend 原始文件，以内容摘要写入 `scout_sarif`，并按
   `image-vulnerability-scan.example.json` 记录相同 SARIF driver/version、固定的
   `critical,high` 过滤器和零结果。按 `build-provenance-report.example.json` 组合原始报告后，受控
   builder 直接签署它：

```bash
git verify-tag v2.0.0 >/secure/evidence/v2.0.0-tag-verification.txt 2>&1
test "$(git rev-parse 'refs/tags/v2.0.0^{}')" = "$DUCKDOCK_GA_SOURCE_COMMIT"
git cat-file tag v2.0.0 >/secure/evidence/v2.0.0-tag-object.txt
git archive --format=tar.gz --prefix=duckdock-2.0.0/ \
  -o /secure/evidence/duckdock-2.0.0-source.tar.gz v2.0.0

docker buildx imagetools inspect "$DUCKDOCK_GA_BACKEND_ATTESTATION_IMAGE" \
  --format '{{json .Provenance.SLSA}}' >/secure/evidence/backend-buildkit-slsa-predicate.json
docker buildx imagetools inspect "$DUCKDOCK_GA_FRONTEND_ATTESTATION_IMAGE" \
  --format '{{json .Provenance.SLSA}}' >/secure/evidence/frontend-buildkit-slsa-predicate.json
docker buildx imagetools inspect "$DUCKDOCK_GA_BACKEND_ATTESTATION_IMAGE" \
  --format '{{json .SBOM.SPDX}}' >/secure/evidence/backend-buildkit-sbom-predicate.spdx.json
docker buildx imagetools inspect "$DUCKDOCK_GA_FRONTEND_ATTESTATION_IMAGE" \
  --format '{{json .SBOM.SPDX}}' >/secure/evidence/frontend-buildkit-sbom-predicate.spdx.json

ssh-keygen -Y sign \
  -f /release-authority/release-builder-key \
  -n duckdock-release-build-provenance \
  /secure/evidence/duckdock-2.0.0-build-report.json

python backend/scripts/collect_ga_release_provenance.py \
  --git-commit "$DUCKDOCK_GA_SOURCE_COMMIT" \
  --backend-image "$DUCKDOCK_GA_BACKEND_IMAGE" \
  --frontend-image "$DUCKDOCK_GA_FRONTEND_IMAGE" \
  --contract-digest "$DUCKDOCK_GA_CONTRACT_DIGEST" \
  --provenance-policy /release-authority/duckdock-release-provenance-policy.json \
  --build-signer-identity release-builder@example.com \
  --build-report /secure/evidence/duckdock-2.0.0-build-report.json \
  --build-signature /secure/evidence/duckdock-2.0.0-build-report.json.sig \
  --output /secure/evidence/duckdock-2.0.0-release-provenance.json
```

   CI summary 同时给出可部署的 linux/amd64 manifest digest 与承载 attestation 的
   image-index digest；授权文件使用前者，build report 的 `attestation_image` 使用后者。
   `imagetools` 的 `.Provenance.SLSA` 是 registry attestation predicate，作为
   `registry_provenance` 原文件保留；按
   `release-slsa-provenance.example.json` 加入唯一的 exact image subject 形成待签名
   Statement v1。最终门禁要求 Statement 的 predicate 与 registry 导出逐字段相同，
   不允许修改 configSource、builder、invocation 或时间；BuildKit 可选的
   `resolvedDependencies`/`byproducts` 必须按原输出保留，不得人工增删。SPDX 同样把
   registry 导出的原始 predicate 保存为 `registry_sbom`，再按
   `release-sbom-spdx.example.json` 加入唯一的 exact image subject 形成 Statement v1；
   默认生成器的 document name 可以是 `sbom`，真正的镜像绑定来自 Statement subject。
   最终门禁要求 SPDX predicate 逐字段相同、格式为 2.3 且 package 非空；不满足时应
   修复 SBOM generator 后重建镜像，禁止在导出后手工升级/补字段。空 `builder.id`
   同样不可接受。`release-scout-sarif.example.json` 只是 SARIF 2.1.0 形状示例；正式
   文件必须直接来自最终 Docker Scout 步骤。授权器会重读原始 SARIF，要求至少一个
   有明确 driver/version 的 run、结果数为零，并验证归一化 scan 对同一文件的 SHA-256
   引用；不能仅凭 Actions 作业显示绿色或手写零漏洞摘要。

   将 collector 输出文件的路径、SHA-256 和 `observed_at` 写入授权文件
   `release.provenance`。最终门禁会再次读取策略、allowed-signers、原始签名报告和
   全部内容寻址 artifact，重读原始 SARIF、重算扫描统计并核对 wrapper 投影；tag/SLSA/SBOM/scan/镜像
   交叉拼接、签名后修改、未批准 builder 或角色/公钥复用都会停在 `FOUNDATION`。

   在开始第 4 步以及后续真实目标采集、压测和故障注入前，发布机构还必须预先定稿
   全部九份信任策略。复制 `ops/ga/trust-topology-manifest.example.json`，填入审批、
   release provenance、TLS、secrets、network、alerting、recovery、capacity 和
   state-services policy 的绝对路径及 SHA-256，然后执行：

```bash
python backend/scripts/verify_ga_trust_topology.py \
  --manifest /release-authority/duckdock-ga-trust-topology.json \
  --output /secure/evidence/duckdock-2.0.0-trust-topology-verification.json
```

   预检要求九份 policy 各自内容寻址其 allowed-signers，并要求所有组织职责的 identity
   和公钥全局唯一；不能让同一人员、服务 identity 或公钥在不同策略中换一个角色名继续
   使用。命令只创建不可覆盖的 PASS 回执，失败不留输出。该回执用于在昂贵演练前暴露
   组织配置错误并随最终 bundle 归档；最终生产授权器仍会直接从证据引用的九份 policy/
   trust store 重算 `organizational_trust_separation`，所以不能通过省略预检回执绕过。

   随后复制 `ops/ga/execution-campaign-request.example.json`，只填写最终 release/target、
   精确 Kubernetes context/Namespace、与 production 不同的 recovery/staging target、
   备份签名专用 allowed-signers 的绝对路径及 SHA-256、最长 14 天窗口，以及一个尚不
   存在的专用 evidence root。发布机构使用刚才的独立
   topology PASS 回执生成计划：

```bash
python backend/scripts/prepare_ga_execution_campaign.py \
  --request /release-authority/duckdock-2.0.0-execution-request.json \
  --trust-topology-receipt /secure/evidence/duckdock-2.0.0-trust-topology-verification.json \
  --output /release-authority/duckdock-2.0.0-execution-campaign.json \
  --assembly-request-output /release-authority/duckdock-2.0.0-preapproval-request.json
```

   工具会重新验证 topology receipt、manifest、九份 policy/trust store，内容寻址绑定
   release/target/window，分配全部 raw/signature/wrapper/closure 的唯一预期路径并验证 phase DAG。
   容量在 readiness/network/secrets 后，HA 在 network/state-services 后，破坏性恢复仅能
   指向独立 recovery/staging target。生成的每个 phase 都是
   `PENDING_EXTERNAL_EVIDENCE`；`PLANNED_EXTERNAL_EXECUTION` 明确不授权任何目标变更，
   外部负责人仍须逐步审阅并执行 acknowledgement。工具同时生成后续 assembler 可直接
   使用的预审批 request，避免采集结束后再次手抄 release/target 和九份 evidence 路径。

   campaign 文件落盘后、`window_starts_at` 之前，必须先完成 Security 与 Operations
   双人执行授权。manifest 由工具从 campaign 推导，不能手写 phase 列表；它绑定 campaign
   SHA-256、最终 release/target、执行窗口、Kubernetes context/Namespace、恢复目标，以及
   TLS/network/secrets/capacity/alerting/recovery/state-services/HA 八个带精确
   acknowledgement 的阶段、工具和输出。两名签署者必须来自 topology 固定的同一份
   approval policy/shared allowed-signers，且 identity 与公钥互不重叠：

```bash
python backend/scripts/prepare_ga_execution_authorization.py \
  --campaign /release-authority/duckdock-2.0.0-execution-campaign.json \
  --output /secure/evidence/execution-authorization-manifest.json

python backend/scripts/sign_ga_execution_authorization.py \
  --campaign /release-authority/duckdock-2.0.0-execution-campaign.json \
  --manifest /secure/evidence/execution-authorization-manifest.json \
  --approval-policy /release-authority/duckdock-ga-approval-policy.json \
  --role Security \
  --identity security-release-authorizer@corp.example \
  --key /secure/keys/security-release-authorizer \
  --statement-output /secure/evidence/execution-authorization-security.json \
  --signature-output /secure/evidence/execution-authorization-security.json.sig

python backend/scripts/sign_ga_execution_authorization.py \
  --campaign /release-authority/duckdock-2.0.0-execution-campaign.json \
  --manifest /secure/evidence/execution-authorization-manifest.json \
  --approval-policy /release-authority/duckdock-ga-approval-policy.json \
  --role Operations \
  --identity operations-release-authorizer@corp.example \
  --key /secure/keys/operations-release-authorizer \
  --statement-output /secure/evidence/execution-authorization-operations.json \
  --signature-output /secure/evidence/execution-authorization-operations.json.sig

python backend/scripts/verify_ga_execution_authorization.py \
  --campaign /release-authority/duckdock-2.0.0-execution-campaign.json \
  --manifest /secure/evidence/execution-authorization-manifest.json \
  --security-statement /secure/evidence/execution-authorization-security.json \
  --security-signature /secure/evidence/execution-authorization-security.json.sig \
  --operations-statement /secure/evidence/execution-authorization-operations.json \
  --operations-signature /secure/evidence/execution-authorization-operations.json.sig
```

   verifier 只返回 `AUTHORIZED_FOR_NAMED_PHASE_EXECUTION`；它不授权 GA、不授权未列明的
   mutation，也不替代安全评估的单独 Security 签署委托。缺任一签名、角色/identity 不符、
   签署后改动、跨 campaign/target 复用或在窗口开始后补签都会失败。closure 和离线归档
   verifier 会再次验证原始两份 statement/signature，不能只保留命令输出。

   外部执行期间不要等到第 71 份产物才发现早期错误。可随时运行非授权增量检查器：

```bash
python backend/scripts/inspect_ga_execution_campaign.py \
  --campaign /release-authority/duckdock-2.0.0-execution-campaign.json \
  --assembly-request /release-authority/duckdock-2.0.0-preapproval-request.json
```

   它会独立重验 campaign/request/topology/九份 policy/trust store，按原始 lexical 计划路径
   检查 71 份外部产物的 missing/regular-file/symlink/size/JSON/private-key marker，并验证
   所有已出现 `path+sha256`、manifest、allowed-signers 和 signature 引用只能指向计划产物
   或内容寻址的发布机构输入。输出的 `duckdock-ga-execution-campaign-progress-v1` 按 phase
   区分 `PENDING`、`PARTIAL`、`INVALID`、`BLOCKED_BY_DEPENDENCIES` 与
   `ARTIFACTS_READY`，给出下一 phase 的工具、policy role、acknowledgement 和缺失产物。
   `ARTIFACTS_READY` 只表示当前文件及引用形状可进入 closure 尝试，明确不表示证据 PASS，
   也不授权执行目标 mutation。需要保存 checkpoint 时使用新的、不可覆盖且位于 evidence
   root 之外的 `--output` 路径。

4. 运行 `python3 scripts/verify-production-baseline.py`，保留 JSON；在目标
   Kubernetes overlay 中替换镜像、域名以及宽泛 egress，并做 server dry-run。
   然后用独立、短期、专用管理员 token 通过真实目标 HTTPS 仅调用只读 readiness
   endpoint（token 只放环境变量，报告不会保留）：

```bash
export DUCKDOCK_GA_ADMIN_TOKEN='short-lived target validation token'
python backend/scripts/collect_ga_target_readiness.py \
  --base-url https://duckdock.example.com \
  --target-environment customer-production \
  --source-commit "$DUCKDOCK_GA_SOURCE_COMMIT" \
  --backend-image "$DUCKDOCK_GA_BACKEND_IMAGE" \
  --frontend-image "$DUCKDOCK_GA_FRONTEND_IMAGE" \
  --output /secure/evidence/target-readiness.json
unset DUCKDOCK_GA_ADMIN_TOKEN
```

   输出必须是 `duckdock-ga-target-readiness-v1`、`scope=target-production`、
   `transport=network HTTPS against target`，且 API 检查时间与证据采集时间相差不
   超过五分钟。裸 `/ga-readiness` 响应、本地 HTTP 报告或上一候选镜像的报告均
   不能授权生产。若 API 不是 `READY`，collector 仍保留 BLOCKED 报告并退出 2。
5. 由发布机构先从 `ops/ga/tls-trust-policy.example.json` 建立只读、内容寻址的 TLS
   探测策略。策略固定外部探测执行人的精确 identity、公钥、probe/vantage ID 和
   全球可路由来源 CIDR；allowed-signers 禁止通配 principal 和跨 identity 公钥复用。
   探测必须从策略批准的目标网络之外执行，不能在目标集群或开发机内部冒充公网视角。
   使用 `probe_ga_target_tls.py` 探测真实公网 application/object-store health URL，
   并把原始报告绑定到当前 target、commit 和两个不可变镜像：

```bash
python backend/scripts/probe_ga_target_tls.py \
  --app-url https://duckdock.example.com/health \
  --object-store-url https://objects.example.com/minio/health/live \
  --target-environment customer-production \
  --source-commit "$DUCKDOCK_GA_SOURCE_COMMIT" \
  --backend-image "$DUCKDOCK_GA_BACKEND_IMAGE" \
  --frontend-image "$DUCKDOCK_GA_FRONTEND_IMAGE" \
  --exercise-id ga-tls-20260806 \
  --probe-id external-tls-probe-01 \
  --vantage-id internet-hangzhou-01 \
  --source-ip "$EXTERNAL_TLS_PROBE_PUBLIC_IP" \
  --acknowledge-external-vantage customer-production \
  --output /secure/evidence/target-tls-raw.json
```

   v3 原始报告保留 TLS 1.2/1.3 两次握手的 peer IP、证书 SHA-256、serial、subject/
   issuer、有效期和 cipher；HTTP 状态、完整响应 header 摘要、HSTS 原文与 body prefix
   摘要；以及 TLS 1.0/1.1 的有界 OpenSSL 原始输出。外部探测执行人直接签署该文件：

```bash
ssh-keygen -Y sign \
  -f /release-authority/tls-probe-key \
  -n duckdock-tls-probe-report \
  /secure/evidence/target-tls-raw.json
```

   再由无私钥参数的组合器重新验签、核对策略来源网段并生成最终 v3 证据：

```bash
python backend/scripts/collect_ga_target_tls.py \
  --target-environment customer-production \
  --source-commit "$DUCKDOCK_GA_SOURCE_COMMIT" \
  --backend-image "$DUCKDOCK_GA_BACKEND_IMAGE" \
  --frontend-image "$DUCKDOCK_GA_FRONTEND_IMAGE" \
  --application-url https://duckdock.example.com/health \
  --object-store-url https://objects.example.com/minio/health/live \
  --exercise-id ga-tls-20260806 \
  --tls-policy /release-authority/duckdock-tls-policy.json \
  --probe-signer-identity tls-probe@example.com \
  --probe-report /secure/evidence/target-tls-raw.json \
  --probe-signature /secure/evidence/target-tls-raw.json.sig \
  --output /secure/evidence/target-tls.json
```

   只有 `duckdock-ga-tls-evidence-v3` 可授权生产。最终 GA 门禁会再次重读策略、
   allowed-signers、原始 JSON 和签名，重算证书剩余天数、TLS 协议集合、HSTS max-age
   与旧协议是否真的未协商，并核对 wrapper/授权文件投影。旧 v1/v2、自报布尔值、
   未签名报告、未批准来源 CIDR、local-validation 或其他候选版本报告均不能授权生产。
6. 在专用 performance Namespace 创建限时 Reporter/User token，通过环境变量运行
   真实 HTTPS 容量门禁（token 不得写入命令行或报告）。先由发布机构从
   `ops/ga/capacity-trust-policy.example.json` 建立只读、内容寻址的容量策略；
   策略固定托管 MySQL provider、必查计数器与增长/积压/复制延迟阈值，并分别授权
   负载执行人、存储观察人和清理验证人。三种精确 identity 与公钥必须互不重合，
   allowed-signers 禁止通配 principal 和公钥复用。存储观察人在压测前先保留 baseline
   快照，再开始负载：

```bash
export DUCKDOCK_CAPACITY_REPORTER_TOKEN='<dedicated reporter token>'
export DUCKDOCK_CAPACITY_USER_TOKEN='<dedicated namespace member token>'
python backend/scripts/g2_target_capacity_gate.py \
  --base-url https://duckdock.example.com \
  --target-environment customer-production \
  --acknowledge-target-mutation customer-production \
  --source-commit "$DUCKDOCK_GA_SOURCE_COMMIT" \
  --backend-image "$DUCKDOCK_GA_BACKEND_IMAGE" \
  --frontend-image "$DUCKDOCK_GA_FRONTEND_IMAGE" \
  --namespace-id 123 \
  --exercise-id ga-capacity-20260806 \
  --output /secure/evidence/target-capacity-load.json
```

   G2 输出的是待组合的 `duckdock-target-capacity-gate-v2` 原始负载报告。负载执行人
   必须直接对该文件签名；不得签署人工整理的摘要：

```bash
ssh-keygen -Y sign \
  -f /release-authority/capacity-load-key \
  -n duckdock-capacity-load-report \
  /secure/evidence/target-capacity-load.json
```

   存储观察人从目标 MySQL/供应商监控取得压测前后快照，按
   `ops/ga/capacity-growth-receipt.example.json` 写入原始行数、当前 exercise/run tag
   行数、pending outbox、data/index bytes 与 replica lag。baseline 必须早于负载开始，
   post-growth 必须晚于负载结束；然后由存储观察人直接签署：

```bash
ssh-keygen -Y sign \
  -f /release-authority/capacity-storage-key \
  -n duckdock-capacity-growth-receipt \
  /secure/evidence/capacity-growth.json
```

   确认增长回执已落盘后，删除专用 performance Namespace、撤销两个 token，并按
   `ops/ga/capacity-cleanup-receipt.example.json` 记录 exercise 作用域的残留计数。独立
   清理验证人确认 Namespace 已删除、两类凭证均已撤销、四类残留均为 0 后签名：

```bash
unset DUCKDOCK_CAPACITY_REPORTER_TOKEN DUCKDOCK_CAPACITY_USER_TOKEN
ssh-keygen -Y sign \
  -f /release-authority/capacity-cleanup-key \
  -n duckdock-capacity-cleanup-receipt \
  /secure/evidence/capacity-cleanup.json
```

   最后用无数据库密码、无 token 参数的组合器重新验签并生成最终 v3 证据：

```bash
python backend/scripts/collect_ga_target_capacity.py \
  --target-environment customer-production \
  --source-commit "$DUCKDOCK_GA_SOURCE_COMMIT" \
  --backend-image "$DUCKDOCK_GA_BACKEND_IMAGE" \
  --frontend-image "$DUCKDOCK_GA_FRONTEND_IMAGE" \
  --base-url https://duckdock.example.com \
  --namespace-id 123 \
  --exercise-id ga-capacity-20260806 \
  --database-provider "Managed MySQL" \
  --capacity-policy /release-authority/duckdock-capacity-policy.json \
  --load-signer-identity capacity-load@example.com \
  --storage-signer-identity capacity-dba@example.com \
  --cleanup-signer-identity capacity-cleanup@example.com \
  --load-report /secure/evidence/target-capacity-load.json \
  --load-signature /secure/evidence/target-capacity-load.json.sig \
  --growth-receipt /secure/evidence/capacity-growth.json \
  --growth-signature /secure/evidence/capacity-growth.json.sig \
  --cleanup-receipt /secure/evidence/capacity-cleanup.json \
  --cleanup-signature /secure/evidence/capacity-cleanup.json.sig \
  --output /secure/evidence/target-capacity.json
```

   只有 `duckdock-target-capacity-gate-v3` 可授权生产。组合器和最终生产授权器都会
   重读策略、信任库、三份原始 JSON 与 OpenSSH 签名，重算 50,000+ materialized
   AgentRun 是否等于真实 MySQL 行增量和 run-tag 行数，Audit/Outbox 增量是否足够，
   数据文件是否达到批准的增长下限，积压/复制延迟是否在阈值内，以及清理是否完成。
   仅有 HTTP 201 数、人工 DBA 摘要、本机 ASGI + MySQL 报告、旧 v2 wrapper 或上一
   候选版本报告都不能替代当前 release 的真实目标 v3 证据。
7. 先运行本地参考演练，确认发布镜像能在受限安全上下文启动、三类无状态服务
   正常跨域、节点 drain 时持续可用、Beat 能迁移且故障域返回后重新均衡：

```bash
bash scripts/rehearse-kubernetes-ha.sh
```

   该命令输出 `duckdock-kubernetes-ha-failover-v1`，但其 scope 固定为
   `local-rehearsal`，状态服务、RWX 与 NetworkPolicy enforcement 固定为 false，
   因而绝不能授权生产。若 Docker Desktop 只有约 8 GiB 内存，先暂停本地
   DuckDock/Langfuse 栈，演练结束后再恢复，避免宿主 OOM 污染结果。

   目标生产演练必须使用 `collect_ga_target_ha.py`，不能手填 PASS 模板。先按后续
   网络步骤取得同一 release 的目标 CNI/外部扫描报告。状态服务必须使用 v2 多方
   证据，不能由 Operations 单方填写汇总：发布机构先从
   `ops/ga/state-services-trust-policy.example.json` 建立内容寻址策略，固定 provider
   与独立 verifier 的精确身份/不同公钥、四类 approved provider；这两类 identity/key
   也不得与四方审批人或独立安全评估人复用。基础设施提供方按
   `state-services-provider-receipt.example.json` 签署真实 topology、来源/目标故障域、
   自动切换 event 和原始事件摘要；独立验证人按
   `state-services-verification-receipt.example.json` 签署故障前后既有数据摘要、写入
   probe 与读回摘要。两类 receipt 使用不同 namespace：

```bash
ssh-keygen -Y sign \
  -f /release-authority/state-provider-key \
  -n duckdock-ha-provider-failover-receipt \
  /secure/evidence/state-provider-receipt.json

ssh-keygen -Y sign \
  -f /release-authority/state-verifier-key \
  -n duckdock-ha-state-verification-receipt \
  /secure/evidence/state-verification-receipt.json

python backend/scripts/collect_ga_state_services_ha.py \
  --target-environment customer-production \
  --source-commit "$DUCKDOCK_GA_SOURCE_COMMIT" \
  --backend-image "$DUCKDOCK_GA_BACKEND_IMAGE" \
  --frontend-image "$DUCKDOCK_GA_FRONTEND_IMAGE" \
  --exercise-id ga-state-ha-20260806 \
  --state-services-policy /release-authority/duckdock-state-services-policy.json \
  --provider-signer-identity state-provider@example.com \
  --verifier-signer-identity state-verifier@example.com \
  --provider-receipt /secure/evidence/state-provider-receipt.json \
  --provider-signature /secure/evidence/state-provider-receipt.json.sig \
  --verification-receipt /secure/evidence/state-verification-receipt.json \
  --verification-signature /secure/evidence/state-verification-receipt.json.sig \
  --output /secure/evidence/state-services-ha.json
```

   组合器只输出 `duckdock-ga-state-services-failover-v2`：四类服务都必须至少跨两个
   故障域，provider event 与 verification receipt 一一对应，故障前后摘要和写后读
   摘要必须相等；单次切换不得超过 4 小时，provider 到 verifier 也必须在同一 4 小时
   窗口内，receipt 与最终 wrapper 均须在完成后 5 分钟内收口。随后由组织审批策略中
   的 Operations 身份签署最终 wrapper：

```bash
ssh-keygen -Y sign \
  -f ~/.ssh/operations-ga-approval \
  -n duckdock-ha-state-services \
  /secure/evidence/state-services-ha.json
```

   在已批准的故障演练窗口运行以下命令。它会 taint 并 drain 所选 zone 的全部
   合格 worker 节点，属于有意的生产 disruption；确认值必须与 target ID 完全一致。
   命令先校验精确 kube context、RBAC、三类 3 副本、Beat 所在区和实际 Deployment
   镜像，故障期间从执行机持续探测公网 HTTPS，最后在 `finally` 中移除 taint、
   uncordon，并滚动再均衡返回的故障域：

```bash
python backend/scripts/collect_ga_target_ha.py \
  --context customer-production-admin \
  --namespace duckdock \
  --target-environment customer-production \
  --acknowledge-target-disruption customer-production \
  --source-commit "$DUCKDOCK_GA_SOURCE_COMMIT" \
  --backend-image "$DUCKDOCK_GA_BACKEND_IMAGE" \
  --frontend-image "$DUCKDOCK_GA_FRONTEND_IMAGE" \
  --base-url https://duckdock.example.com \
  --drain-zone cn-hangzhou-a \
  --network-evidence /secure/evidence/target-network.json \
  --state-services-evidence /secure/evidence/state-services-ha.json \
  --state-services-signature /secure/evidence/state-services-ha.json.sig \
  --state-services-signer-identity operations-release-approver@example.com \
  --approval-policy /release-authority/duckdock-ga-approval-policy.json \
  --output /secure/evidence/target-ha.json
```

   只有 `duckdock-kubernetes-ha-failover-v2` 可授权生产。报告保留 before/drain/
   rebalance Pod→node→zone 原始快照、全部 drained node、每次 HTTPS 状态码、cleanup
   结果，并内容寻址地绑定网络证据和 Operations 签名的状态服务 v2 wrapper。最终
   门禁还会重读状态服务策略、provider/verifier 原始 JSON 与签名，重算故障域、事件
   对应、数据连续性和时间线。旧 v1、单方 Operations 汇总、共享密钥、仅有 YAML 的
   声明、手填 Pod 名称或清理不完整的报告都会被拒绝。
8. 使用 `collect_ga_target_secrets.py` 在目标 Kubernetes 环境采集
   `duckdock-ga-secrets-evidence-v2`，不能手填 PASS 模板。先由发布机构从
   `ops/ga/secrets-trust-policy.example.json` 建立只读、内容寻址的信任策略；策略
   固定批准的 Secret Manager、`application-signing`/`database`/`object-store`
   三类 secret、`backend`/`worker`/`beat` 三个消费者，以及互不重叠的 provider
   和独立 verifier 精确身份。allowed-signers 禁止通配 principal 和公钥复用。

   采集器启动前四个 receipt/signature 路径必须不存在。它先通过 Kubernetes
   JSONPath 读取 `duckdock-runtime-secrets`、Deployment 和 Pod 的元数据；不会读取
   Secret `data`，也没有任何接收 credential 值的参数。外部 Secret Manager 完成
   每类版本轮换、停用旧版本和审计落盘后，写入
   `duckdock-ga-secret-rotation-receipt-v1`，再由 provider 服务身份签名。独立验证
   系统实际以旧版本和新版本做正反向认证探测，写入
   `duckdock-ga-secret-verification-receipt-v1`，证明旧版本拒绝、新版本可用，并由
   不同身份/密钥签名：

```bash
ssh-keygen -Y sign \
  -f /release-authority/secret-provider-key \
  -n duckdock-secret-rotation-receipt \
  /secure/evidence/secret-rotation.json

ssh-keygen -Y sign \
  -f /release-authority/secret-verifier-key \
  -n duckdock-secret-verification-receipt \
  /secure/evidence/secret-verification.json
```

   两份签名出现后，采集器等待 Secret `resourceVersion` 变化、三个 Deployment
   generation 前进且恢复全量 ready/updated/available，并证明 before/after Pod UID
   完全不重叠：

```bash
python backend/scripts/collect_ga_target_secrets.py \
  --context customer-production-admin \
  --namespace duckdock \
  --target-environment customer-production \
  --acknowledge-target-rotation customer-production \
  --source-commit "$DUCKDOCK_GA_SOURCE_COMMIT" \
  --backend-image "$DUCKDOCK_GA_BACKEND_IMAGE" \
  --frontend-image "$DUCKDOCK_GA_FRONTEND_IMAGE" \
  --provider "External Secrets" \
  --secret-name duckdock-runtime-secrets \
  --exercise-id ga-secrets-20260806 \
  --secrets-policy /release-authority/duckdock-secrets-policy.json \
  --provider-signer-identity secret-provider@example.com \
  --verifier-signer-identity secret-verifier@example.com \
  --rotation-receipt /secure/evidence/secret-rotation.json \
  --rotation-signature /secure/evidence/secret-rotation.json.sig \
  --verification-receipt /secure/evidence/secret-verification.json \
  --verification-signature /secure/evidence/secret-verification.json.sig \
  --output /secure/evidence/target-secrets.json
```

   生产授权器会重新读取策略、信任库、两份原始 JSON 和签名，重算 Kubernetes
   before/after 差异及时间线；旧 v1、自报布尔值、预先存在的 receipt、provider
   代替 verifier 签名、未滚动 Pod 或包含 secret/token/password 值都不能通过。
   opaque version/receipt/audit ID 只用于关联外部系统，不得放入任何 credential 值。
9. 使用 `collect_ga_target_network.py` 采集待签名的
   `duckdock-ga-network-probe-v3`，再由
   `collect_ga_target_network_evidence.py` 组合最终
   `duckdock-ga-network-evidence-v3`，不能手填 PASS wrapper。发布机构先从
   `ops/ga/network-trust-policy.example.json` 建立只读、内容寻址的策略，固定精确
   probe signer/key、probe/vantage ID、全球可路由来源 CIDR、目标 kube context、
   Namespace 和 CNI DaemonSet。执行机必须位于目标网络之外，同时具备目标集群只读
   NetworkPolicy/CNI DaemonSet 与三类 probe Pod `exec` 权限，并已安装 `nmap`。
   预先准备三个不挂载生产 Secret、包含 Python 3 的 Ready probe Pod：入口受信
   Namespace 带 `duckdock.io/ingress=true`，监控受信 Namespace 带
   `duckdock.io/monitoring=true`，非受信 Namespace 不得带任一标签。另准备一个不在
   backend egress allowlist 中、可由非受信 probe 访问的 TCP 对照监听器；只有同一
   目标由对照组连通、backend 被拒绝，才证明拒绝来自策略而非目标宕机。

```bash
python backend/scripts/collect_ga_target_network.py \
  --context customer-production-admin \
  --namespace duckdock \
  --target-environment customer-production \
  --acknowledge-external-vantage customer-production \
  --source-commit "$DUCKDOCK_GA_SOURCE_COMMIT" \
  --backend-image "$DUCKDOCK_GA_BACKEND_IMAGE" \
  --frontend-image "$DUCKDOCK_GA_FRONTEND_IMAGE" \
  --base-url https://duckdock.example.com \
  --exercise-id ga-network-20260806 \
  --scanner-id external-scanner-hz-01 \
  --vantage-id internet-hangzhou-01 \
  --scanner-source-ip "$EXTERNAL_SCANNER_SOURCE_IP" \
  --database-address 10.20.1.10 \
  --redis-address 10.20.1.11 \
  --object-store-direct-address 10.20.1.12 \
  --trusted-probe-namespace duckdock-ingress-probes \
  --trusted-probe-pod network-probe \
  --monitoring-probe-namespace duckdock-monitoring-probes \
  --monitoring-probe-pod network-probe \
  --untrusted-probe-namespace duckdock-untrusted-probes \
  --untrusted-probe-pod network-probe \
  --approved-egress-host mysql.internal.example.com \
  --approved-egress-port 3306 \
  --unapproved-egress-host network-control.example.com \
  --unapproved-egress-port 443 \
  --cni-daemonset-name cilium \
  --output /secure/evidence/target-network-raw.json

ssh-keygen -Y sign \
  -f /release-authority/network-probe-key \
  -n duckdock-network-probe-report \
  /secure/evidence/target-network-raw.json

python backend/scripts/collect_ga_target_network_evidence.py \
  --target-environment customer-production \
  --source-commit "$DUCKDOCK_GA_SOURCE_COMMIT" \
  --backend-image "$DUCKDOCK_GA_BACKEND_IMAGE" \
  --frontend-image "$DUCKDOCK_GA_FRONTEND_IMAGE" \
  --exercise-id ga-network-20260806 \
  --network-policy /release-authority/duckdock-network-policy.json \
  --probe-signer-identity network-probe@example.com \
  --probe-report /secure/evidence/target-network-raw.json \
  --probe-signature /secure/evidence/target-network-raw.json.sig \
  --output /secure/evidence/target-network.json
```

   执行器从外部对公网入口扫描全部 1–65535 TCP 端口，并分别探测 MySQL 3306、
   Redis 6379 和对象存储直连 9000；在集群内同时验证受信 ingress 放行、非受信
   ingress 拒绝、批准 egress 放行和同一对照目标的非批准 egress 拒绝。报告保留
   原始 nmap XML 及其摘要、CNI DaemonSet 不可变镜像、Namespace/Pod UID、完整
   NetworkPolicy spec 与 kubectl exit code。组合器和生产授权器都会重读内容寻址
   策略和 trust store，验证原始报告签名、外部来源、cluster/CNI 身份；生产授权器
   还会重新计算 spec 摘要并拒绝任意 `0.0.0.0/0`/`::/0` egress，即使汇总字段仍
   自报 PASS。修改签名后的原始报告、wrapper 投影或使用未批准的观测点都不能通过。
   最终 v3 文件必须先于第 6
   步的目标 HA disruption 生成，并由 HA v2 报告内容寻址绑定。配置文件、server
   dry-run、本地 kind 回执或旧 v1 报告都不是目标运行证据。
10. 使用 `collect_ga_target_recovery.py` 从异地、加密、不可变备份介质对明确命名的
   非生产恢复目标做破坏性恢复，输出 `duckdock-ga-recovery-evidence-v2`。`backup`
   必须引用 `seal-backup.sh` 生成的原始 `manifest.json`、`.sig`、备份 signer identity
   与 allowed-signers；采集器和生产门禁都会在 namespace `duckdock-backup` 实际验签，
   解析 release commit、age 加密方式及三类 artifact digest/size。

   发布机构先从 `ops/ga/recovery-trust-policy.example.json` 建立只读、内容寻址的
   恢复策略，分别授权存储服务、恢复执行人和独立验证人。三类精确 identity 与公钥
   必须互不重合；策略固定 `minio/mysql/repos` 三个加密 artifact、
   `mysql/repositories/object-store` 三个恢复阶段和至少 30 天保留期。采集器启动前
   六个 receipt/signature 路径必须不存在：

   - 存储集成从供应商 API 取得对象 version ID、digest、size、Object Lock mode 与
     retained-until，写入 `duckdock-ga-backup-media-receipt-v1` 并由服务身份签名；
   - 恢复执行工作流只在已确认的 `recovery`/`staging` 环境执行，记录三个阶段的
     command ID、exit code 和脱敏日志 SHA-256，写入
     `duckdock-ga-restore-execution-receipt-v1` 并由执行人签名；
   - 与执行人独立的验证方核对 MySQL 行集摘要、对象清单摘要、Git refs 摘要、HTTPS
     与后台 worker 就绪，写入 `duckdock-ga-recovery-verification-receipt-v1` 并签名。

```bash
ssh-keygen -Y sign -f /release-authority/storage-key \
  -n duckdock-backup-media-receipt /secure/evidence/backup-media.json
ssh-keygen -Y sign -f /release-authority/restore-executor-key \
  -n duckdock-restore-execution-receipt /secure/evidence/restore-execution.json
ssh-keygen -Y sign -f /release-authority/recovery-verifier-key \
  -n duckdock-recovery-verification-receipt /secure/evidence/recovery-verification.json
```

```bash
python backend/scripts/collect_ga_target_recovery.py \
  --target-environment customer-production \
  --restore-target-environment customer-recovery-staging \
  --acknowledge-destructive-restore customer-recovery-staging \
  --source-commit "$DUCKDOCK_GA_SOURCE_COMMIT" \
  --backend-image "$DUCKDOCK_GA_BACKEND_IMAGE" \
  --frontend-image "$DUCKDOCK_GA_FRONTEND_IMAGE" \
  --exercise-id ga-recovery-20260806 \
  --maximum-rpo-seconds 900 \
  --maximum-rto-seconds 14400 \
  --storage-provider "AWS S3" \
  --backup-manifest /secure/evidence/manifest.json \
  --backup-signature /secure/evidence/manifest.json.sig \
  --backup-allowed-signers /release-authority/backup-allowed-signers \
  --backup-signer-identity backup-operator@example.com \
  --recovery-policy /release-authority/duckdock-recovery-policy.json \
  --storage-signer-identity backup-storage@example.com \
  --restore-signer-identity restore-executor@example.com \
  --verifier-signer-identity recovery-verifier@example.com \
  --media-receipt /secure/evidence/backup-media.json \
  --media-signature /secure/evidence/backup-media.json.sig \
  --restore-receipt /secure/evidence/restore-execution.json \
  --restore-signature /secure/evidence/restore-execution.json.sig \
  --verification-receipt /secure/evidence/recovery-verification.json \
  --verification-signature /secure/evidence/recovery-verification.json.sig \
  --output /secure/evidence/target-recovery.json
```

   采集器不接收解密密钥、数据库密码或对象存储 credential。它会核对异地对象摘要/
   大小与已签 manifest 完全一致，并从 `recovery_point_at → failure_injected_at →
   independent verification completed_at` 重算 RPO/RTO。旧 v1、手填 RPO/RTO、短于
   30 天的 Object Lock、生产/恢复 target 相同、角色/公钥复用、预先存在的 receipt、
   自报 `signature_verified` 或修改签名后内容都不能通过。
11. 使用 `collect_ga_target_alerting.py` 主动触发并恢复唯一的目标 Alertmanager
    告警。发布机构先从 `ops/ga/alerting-trust-policy.example.json` 建立独立、只读、
    内容寻址的告警信任策略：`delivery_identities` 是能从通知供应商取得真实投递回执
    的服务身份，`oncall_schedules` 将命名 schedule 映射到实际值班人员身份；两类
    身份及公钥不得重合，allowed-signers 禁止通配 principal。

    采集器运行期间，通知集成根据它输出的 `exercise_id`/`alert_name` 原子写入 firing
    与 resolved 的 `duckdock-ga-alert-delivery-receipt-v1` 文件；每份文件必须列出至少
    两个不同 channel、receiver 和 provider receipt ID，且 firing/resolved 覆盖完全
    相同的目标，再由 delivery 身份签名。实际值班人员确认告警后写入
    `duckdock-ga-oncall-acknowledgement-v1`，由本人签名。三个 receipt/signature 路径
    在演练开始前必须不存在，防止复用历史回执：

```bash
ssh-keygen -Y sign \
  -f /release-authority/alert-delivery-key \
  -n duckdock-alert-delivery-receipt \
  /secure/evidence/alert-firing.json

ssh-keygen -Y sign \
  -f ~/.ssh/oncall-ack-key \
  -n duckdock-oncall-acknowledgement \
  /secure/evidence/alert-ack.json
```

    firing 与人工 ack 签名出现后，采集器才会向同一 Alertmanager alert 写入 resolve；
    通知集成随后以相同 delivery 身份签署 resolved receipt。正式命令示例：

```bash
python backend/scripts/collect_ga_target_alerting.py \
  --target-environment customer-production \
  --acknowledge-oncall-exercise customer-production \
  --source-commit "$DUCKDOCK_GA_SOURCE_COMMIT" \
  --backend-image "$DUCKDOCK_GA_BACKEND_IMAGE" \
  --frontend-image "$DUCKDOCK_GA_FRONTEND_IMAGE" \
  --alertmanager-url https://alerts.example.com \
  --ca-file /release-authority/target-alertmanager-ca.pem \
  --bearer-token-file /run/secrets/alertmanager-probe-token \
  --exercise-id ga-20260806-001 \
  --oncall-schedule platform-primary \
  --alerting-policy /release-authority/duckdock-alerting-policy.json \
  --delivery-signer-identity alert-delivery@example.com \
  --oncall-signer-identity oncall-primary@example.com \
  --firing-receipt /secure/evidence/alert-firing.json \
  --firing-signature /secure/evidence/alert-firing.json.sig \
  --ack-receipt /secure/evidence/alert-ack.json \
  --ack-signature /secure/evidence/alert-ack.json.sig \
  --resolved-receipt /secure/evidence/alert-resolved.json \
  --resolved-signature /secure/evidence/alert-resolved.json.sig \
  --output /secure/evidence/target-alerting.json
```

    输出协议只能是 `duckdock-ga-alerting-evidence-v2`。生产授权器会重新读取策略和
    三份原始 JSON，核对摘要、双接收目标、精确身份角色、OpenSSH namespace，并
    重新解析受大小限制的 Alertmanager 原始 HTTP 请求/响应来验证 exact labels、
    startsAt/endsAt 与 active→inactive 匹配数。加载规则、仅送达无签名 webhook、预先存在
    的 receipt、自报 `signature_verified` 或 delivery 服务代替人签 ack 均不能通过。
    若任一阶段失败，采集器会尽力自动 resolve 已注入的告警；重试必须使用新的
    exercise ID 和全新的 receipt/signature 路径，不能复用失败演练留下的文件。
12. 委托与项目实现方、四方审批人均独立的安全机构按指定范围执行渗透测试和人工
    代码审查，关闭并复测全部 Critical/High。测试开始前必须先完成机器可验证的
    rules-of-engagement 双阶段握手，不能只在测试结束后接收一份报告。组织发布策略必须先升级为
    `duckdock-ga-approval-policy-v2`，在 `independent_security_assessors` 中把机构名称
    映射到评估方精确 identity，并将其公钥放入与四方审批共享的 allowed-signers；
    外部评估人与内部审批人不得复用 identity 或公钥。

    execution campaign 创建并审阅后，Security 角色先按
    `ops/ga/security-assessment-engagement.example.json` 填写不可变委托：
    精确 provider/assessor、target、contract、commit、镜像、最长 30 天测试窗口、非全网
    来源 CIDR、非秘密测试账号 ID、测试机 ID、急停联系人和 15 分钟内停止 SLA。委托必须
    禁止 DoS、破坏性改数、生产数据外传、持久化/后门、社工、物理测试以及越界测试第三方
    系统，并要求证据静态加密、不记录秘密、不保留生产数据、窗口后最多 90 天删除且交付
    删除证明。`authorized_at` 不得早于 campaign 创建，assessment window 必须完全落在
    campaign window 内；只有 approval policy 中精确授权的 Security identity 能签署该委托：

```bash
ssh-keygen -Y sign \
  -n duckdock-security-assessment-engagement \
  -f /secure/security-role-signing-key \
  /secure/duckdock-2.0-security-engagement.json
shasum -a 256 /secure/duckdock-2.0-security-engagement.json
```

    评估方按 `ops/ga/security-assessment-report.example.json` 交付 v2 机器可读原始 JSON，
    逐条列出 findings，并绑定前述 engagement ID/SHA-256、最终 PDF SHA-256、相同 target、
    contract、commit 和两个镜像摘要；`started_at`/`completed_at` 必须落在委托窗口内，
    `execution_identity` 必须逐项等于批准的 CIDR/测试机/账号，且
    `data_handling_attestation` 必须确认工作证据加密、未记录 secret、未保留生产数据并已
    在截止日前删除。评估方直接对该 JSON 签名：

```bash
ssh-keygen -Y sign \
  -n duckdock-security-assessment \
  -f /secure/assessor-signing-key \
  /secure/duckdock-2.0-final-assessment.json

python backend/scripts/collect_ga_independent_security.py \
  --target-environment customer-production \
  --source-commit "$DUCKDOCK_GA_SOURCE_COMMIT" \
  --backend-image "$DUCKDOCK_GA_BACKEND_IMAGE" \
  --frontend-image "$DUCKDOCK_GA_FRONTEND_IMAGE" \
  --contract-digest "$DUCKDOCK_GA_CONTRACT_DIGEST" \
  --provider "Independent Security Lab" \
  --security-authorizer-identity security@duckdock.example \
  --assessor-signer-identity assessor@independent-security.example \
  --assessment-engagement /secure/duckdock-2.0-security-engagement.json \
  --engagement-signature /secure/duckdock-2.0-security-engagement.json.sig \
  --assessment-report /secure/duckdock-2.0-final-assessment.json \
  --assessment-signature /secure/duckdock-2.0-final-assessment.json.sig \
  --approval-policy /release-authority/duckdock-ga-approval-policy.json \
  --output /secure/evidence/independent-security.json
```

    v3 采集器和最终门禁都只信任显式传入的组织策略：先用 Security 角色重新验签委托并
    验证安全边界，再用独立 assessor 角色验签报告，重算 engagement/PDF digest 和逐级
    finding 统计，并核对 wrapper 投影。自选 allowed-signers、越过窗口、放宽禁止动作、
    替换委托、隐藏 High、修改投影或签名后改 PDF 均不能通过。campaign 会将委托 JSON、
    委托签名、报告 JSON、报告签名、PDF 和 v3 wrapper 六份文件列为精确计划工件。
13. 为每份证据填绝对或授权文件相对路径、SHA-256 与 UTC 时间。Readiness、TLS、
    Secrets、网络、告警、恢复、容量、HA 和独立安全报告内部 `observed_at` 必须与
    各自授权证据时间相同，且 `scope` 只能是 `target-production`。本地 dev/kind
    回执不能转换成该 scope。
14. 由组织发布机构（不能是任一审批者临时自建；且必须在 HA 状态服务和第 12 步
    独立安全证据签署前完成）从
    `ops/ga/approval-policy.example.json` 建立受控审批策略。策略必须使用
    `duckdock-ga-approval-policy-v2`，为 Product、Architecture、Security、
    Operations 分配互不重叠的精确 identity，并在
    `independent_security_assessors` 中预授权外部评估 provider/identity。全部内部与
    外部 identity、公钥必须互不重叠并引用同一份 OpenSSH allowed-signers。信任库
    不得使用通配 principal；其 SHA-256 写入策略，策略
    SHA-256 和 policy ID 再写入生产授权文件的 `approval_policy`。策略与信任库
    应由发布流水线/发布负责人以只读方式注入验证器，不能接受审批者随授权包提交
    的任意替代路径。例如：

```bash
shasum -a 256 /release-authority/duckdock-ga.allowed-signers
# 将摘要填入 /release-authority/duckdock-ga-approval-policy.json
shasum -a 256 /release-authority/duckdock-ga-approval-policy.json
# 将策略摘要和 policy_id 填入 authorization.json
```

15. 九类采集器和外部评估输出齐备后，不要再手工把报告字段、时间和摘要复制到完整
    authorization JSON，也不要在正式 campaign 中直接调用通用 assembler。使用计划阶段
    自动生成的 campaign 和 preapproval request。先要求增量检查器确认可尝试 closure：

```bash
python backend/scripts/inspect_ga_execution_campaign.py \
  --campaign /release-authority/duckdock-2.0.0-execution-campaign.json \
  --assembly-request /release-authority/duckdock-2.0.0-preapproval-request.json \
  --require-ready-for-closure \
  --output /release-authority/checkpoints/duckdock-2.0.0-ready-for-closure.json
```

`--require-ready-for-closure` 在未齐备时退出 `2`；非法/过期 evidence 即使不带该参数也退出
`2`，campaign/拓扑协议错误退出 `3`。checkpoint 不进入 evidence root，也不能替代下述
闭环工具。只有状态 `READY_FOR_CLOSURE_ATTEMPT` 才运行：

```bash
python backend/scripts/close_ga_execution_campaign.py \
  --campaign /release-authority/duckdock-2.0.0-execution-campaign.json \
  --assembly-request /release-authority/duckdock-2.0.0-preapproval-request.json
```

闭环工具按 campaign 预分配路径要求 71 份外部产物全部存在，拒绝 symlink、缺失、摘要
冲突和任何指向未计划路径的 `path+sha256`、`path+manifest_sha256`、allowed-signers 或
signature 引用；十份最终 wrapper 的 `observed_at` 必须位于执行窗口内。它重新生成并逐字
比较 campaign/request、重验 topology/九份 policy/trust store 和单独内容寻址的 backup
allowed-signers，然后调用同一 assembler 与权威授权器。只有结果精确为
`APPROVAL_COLLECTION` 时才原子写出计划内的 `preapproval-authorization.json`、
`preapproval-assembly-receipt.json` 和 `execution-campaign-closure.json`。任一步失败会删除
本轮已创建的输出；第二次运行不能覆盖。closure 的 `PREAPPROVAL_ASSEMBLED` 只表示证据
接线闭合，仍明确 `does_not_authorize_GA_or_target_mutation`。

`assemble_ga_preapproval_authorization.py` 仍保留为可单独测试投影规则的底层原语；它本身
不证明产物来自已审阅 campaign，不能替代上述正式发布路径。

使用受控策略再次运行不可绕过的预签字门禁。授权文件的 `approvals` 此时为空，
但发布基础、九类目标证据、外部安全签名及所有内容摘要已经完整：

```bash
python backend/scripts/verify_ga_production_authorization.py \
  /secure/duckdock-2.0.0-authorization.json \
  --approval-policy /release-authority/duckdock-ga-approval-policy.json \
  --require-evidence-ready \
  --output /secure/duckdock-2.0.0-preapproval-result.json
```

只有命令退出 `0`，且结果同时满足以下条件，才能把其中的 `release_digest` 交给审批人：

```text
status=AWAITING_EXTERNAL_APPROVALS
campaign_stage=APPROVAL_COLLECTION
foundation_ready=true
evidence_ready_for_approval=true
failed_foundation_checks=[]
failed_evidence_checks=[]
next_action=collect_organizational_approvals
```

`--allow-blocked` 仅供诊断，不能作为流水线签字前置条件。缺文件、证据过期、跨 commit/
镜像复用、签名或内容不一致时，门禁保持 `BLOCKED` 且
`campaign_stage=EVIDENCE_COLLECTION`；版本、目标或发布机构策略无效时停在
`campaign_stage=FOUNDATION`。这两种状态都禁止继续签字。

预签字门禁通过后，先冻结一次有时限的审批活动。正式 freeze v2 先独立重验 persisted
execution closure 的 campaign/request/topology、71 份外部产物、95 个闭包输入、139 条
引用和预审批评估，再把 closure、空 `approvals` 授权文件、发布机构策略、release digest、
冻结时间和审批截止时间共同内容寻址到不可覆盖的回执；窗口默认 24 小时，最大 72 小时：

```bash
python backend/scripts/freeze_ga_approval_campaign.py \
  --authorization /secure/duckdock-2.0.0-authorization.json \
  --approval-policy /release-authority/duckdock-ga-approval-policy.json \
  --execution-closure /secure/evidence/execution-campaign-closure.json \
  --approval-window-hours 24 \
  --receipt-output /secure/duckdock-2.0.0-campaign-freeze.json
```

冻结输入不能预先包含 `approval_campaign`；该字段由 finalizer 在四份签字完成后写入
最终授权文件。若从完整 example 复制底稿，冻结前应同时移除示例 approvals 和该示例引用。
`--allow-legacy-unbound` 仅用于审计历史 v1 fixture；正式 freezer、signer、finalizer、最终
授权 CLI、archiver 与 archive verifier 默认全部拒绝没有 execution closure 绑定的 v1，
发布流水线不得使用该兼容开关。

然后将同一份只读授权文件、全部内容寻址证据、发布机构策略和 campaign freeze 回执提供给
四个不同负责人。授权文件的 `approvals` 此时必须为空；每位负责人都必须在自己的
可信工作站重新执行完整预签字门禁。签字工具不接受人工复制的 `--digest`，而是从
当前权威评估结果读取 release digest，核对 identity 仅属于指定角色，并用发布机构
共享 trust store 立即反向验签：

```bash
bash scripts/sign-ga-approval.sh \
  --authorization /secure/duckdock-2.0.0-authorization.json \
  --approval-policy /release-authority/duckdock-ga-approval-policy.json \
  --campaign-freeze /secure/duckdock-2.0.0-campaign-freeze.json \
  --role Product \
  --identity product-release-approver@example.com \
  --approved-at 2026-08-06T10:30:00+08:00 \
  --key ~/.ssh/product-ga-approval \
  --signature-output /secure/signatures/product.sig \
  --approval-output /secure/product-approval.json \
  --preflight-output /secure/product-preflight.json
```

签名采用 OpenSSH namespace `duckdock-ga`。每个 identity 必须属于组织策略中对应
角色，四个 approval 必须使用不同 identity，并在最新证据之后签署。v2 禁止在
approval 或安全证据内提供替代 `allowed_signers_path`；所有签名只信任组织策略绑定的
共享信任库。
规范化签名 statement 同时覆盖 release digest、campaign ID、freeze 回执摘要、role、
identity、decision 和 approved_at，授权包组装者不能事后改写审批活动、角色、决定或时间。
签字时间必须位于冻结窗口内；任何证据、目标、
版本或审批策略摘要变化都会改变 release digest，使旧签名失效。

`product-preflight.json` 保留该签字人实际看到的授权文件/策略/freeze 摘要、完整门禁结果和
反向验签结果；它不是替代签名的信任根。四个签字人分别生成 Product、Architecture、
Security、Operations 的 approval/preflight/signature，禁止共享私钥或由一个操作员
代签。

四份 approval 不再手工复制进 JSON。使用未签字的原授权文件作为 base；组装器要求
base 与最终输出在同一目录，以保持相对证据路径语义，并且只有持久化后的文件再次
得到 `GA_AUTHORIZED` 才会留下输出：

```bash
python backend/scripts/finalize_ga_authorization.py \
  --authorization /secure/duckdock-2.0.0-authorization.json \
  --approval-policy /release-authority/duckdock-ga-approval-policy.json \
  --campaign-freeze /secure/duckdock-2.0.0-campaign-freeze.json \
  --approval-entry /secure/product-approval.json \
  --approval-entry /secure/architecture-approval.json \
  --approval-entry /secure/security-approval.json \
  --approval-entry /secure/operations-approval.json \
  --output /secure/duckdock-2.0.0-authorized.json \
  --receipt-output /secure/duckdock-2.0.0-finalization.json
```

组装器拒绝重复角色/identity、跨 campaign 混签、freeze 过期、窗口外签字、错误或过期
digest、错误密钥、签名缺失、base 已带审批、证据变化和非同目录输出；失败时不会留下
看似可用的最终授权文件。成功时会在最终授权的 `approval_campaign` 中保留 freeze
回执的 path/SHA-256/campaign ID；权威授权器会重新打开 freeze 和其绑定的原始空
approvals base，核对最终文件除审批与该引用外完全一致。因此即使绕过 finalizer 手工
拼 JSON，也不能用虚构或缺失的 freeze 得到 `GA_AUTHORIZED`。v2 freeze 摘要又进入每份
签名 statement，因此 closure、64 份计划产物、空 base、策略或窗口中任一项变化都会使
四份签名同时失效。

最后仍需以独立命令对持久化授权文件执行同一权威门禁：

```bash
python backend/scripts/verify_ga_production_authorization.py \
  /secure/duckdock-2.0.0-authorized.json \
  --approval-policy /release-authority/duckdock-ga-approval-policy.json \
  --output /secure/duckdock-2.0.0-ga-authorization-result.json
```

退出码 `0` 且状态 `GA_AUTHORIZED` 才是正式生产授权；该 CLI 默认还要求 v2 freeze，
所以历史 v1 即使四份签名有效也不能作为正式结果退出 `0`。退出码 `2` 是证据或签字
阻断，退出码 `3` 是授权文件或正式协议错误。得到该结果后使用授权归档器收口活动：

```bash
python backend/scripts/archive_ga_authorized_bundle.py \
  --authorization /secure/duckdock-2.0.0-authorized.json \
  --approval-policy /release-authority/duckdock-ga-approval-policy.json \
  --supplemental-file /secure/duckdock-2.0.0-assembly.json \
  --supplemental-file /secure/duckdock-2.0.0-finalization.json \
  --supplemental-file /secure/product-preflight.json \
  --supplemental-file /secure/architecture-preflight.json \
  --supplemental-file /secure/security-preflight.json \
  --supplemental-file /secure/operations-preflight.json \
  --supplemental-file /secure/duckdock-2.0.0-ga-authorization-result.json \
  --output /secure/duckdock-2.0.0-ga-bundle.tar.gz \
  --manifest-output /secure/duckdock-2.0.0-ga-bundle.manifest.json \
  --digest-output /secure/duckdock-2.0.0-ga-bundle.sha256
```

归档器在收集前后各运行一次同一权威门禁，只有两次均为无阻断
`GA_AUTHORIZED` 才写出结果；三个输出均不可覆盖，持久化后会重新读取并验证。
它不会扫描目录，只沿 JSON 中显式的 `path+sha256`、`path+manifest_sha256`、
`allowed_signers_path+allowed_signers_sha256`、`signature_path` 引用闭包，并加入明确
传入的 supplemental receipt/result。正式 v2 campaign freeze 已内容寻址 execution closure；
closure 又显式引用 campaign/request/topology、71 份外部产物和 preapproval outputs，因此
完整“计划→实际证据”图会自动进入 portable archive，不再需要把 closure 作为 supplemental
重复传入。campaign freeze 及其空 base 同样由最终授权中的内容寻址引用自动进入闭包。
因此同目录审批私钥、builder 私钥和未引用文件不会进入归档；即使显式引用，常见
OpenSSH/PEM/age 私钥材料也会被拒绝。授权文件
目录和策略目录自动成为允许根；引用位于其他受控目录时，
必须显式追加 `--include-root evidence=/absolute/evidence/root`，越界引用、符号链接、
摘要不符、证据中途变化或大于默认上限的输入都会 fail closed。

归档 manifest v2 会为每个 JSON 原始路径建立 source member → target member 引用索引，
并把 canonical evaluation time 固定为 `created_at`。显式 `--created-at` 只能位于实际归档
开始前 300 秒内；归档器仍会在真实当前时间前后重验，所以该参数不能绕过证据过期。
相对路径与权威授权器一致，以 authorization 所在目录为基准；正式 collector 默认输出
绝对路径以避免跨工具歧义。备份 manifest 的 `allowed_signers_path` 也必须同时提供
`allowed_signers_sha256`，无内容摘要的旧恢复证据不再具有 GA 授权资格。

外部 manifest 逐文件记录允许根标签、相对路径、内容寻址 archive path、size 和
SHA-256；tar/gzip 元数据固定，因此相同输入和相同 `--created-at` 生成字节级一致的
bundle。`--created-at` 只固定 manifest 时间，不会回拨权威门禁的当前时间或延长证据
有效期。发布机构应把 `.sha256` 发布到独立不可变渠道。授权结果、原文件、全部证据、
签名、campaign freeze、四份 approval/preflight/signature、finalization receipt、审批策略和共享
allowed-signers 均应在 manifest 中可追溯。生产验证时仍必须从发布机构控制的独立
只读路径选择策略，不能从待验证授权包自动发现信任根。

归档传输或长期保存后必须运行独立复验器。digest sidecar 必须从发布机构的独立、
不可变渠道获得，不能只信任与 bundle 同渠道传来的文件：

```bash
python backend/scripts/verify_ga_authorized_archive.py \
  --archive /received/duckdock-2.0.0-ga-bundle.tar.gz \
  --manifest /received/duckdock-2.0.0-ga-bundle.manifest.json \
  --digest /trusted-channel/duckdock-2.0.0-ga-bundle.sha256
```

也可以用独立渠道记录的裸摘要替代 sidecar：

```bash
python backend/scripts/verify_ga_authorized_archive.py \
  --archive /received/duckdock-2.0.0-ga-bundle.tar.gz \
  --manifest /received/duckdock-2.0.0-ga-bundle.manifest.json \
  --expected-sha256 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
```

复验器先检查外部摘要、embedded/external manifest、成员集合/metadata/size/SHA-256、
引用索引和私钥排除，再把成员物化到临时隔离目录。随后以严格路径重映射重跑同一
权威 evaluator：任何未进入 manifest 的原主机路径都会返回 missing，绝不回退读取
接收机或原发布机文件系统。只有历史 canonical 时点仍得到完全一致的
`GA_AUTHORIZED`、closure-bound freeze v2 和 release digest，才输出
`GA_AUTHORIZED_ARCHIVE_VERIFIED`。closure 的引用账本会按已验证 manifest 映射到临时
内容寻址成员后重新闭合。

授权结果还会固定输出 `failed_foundation_checks`、`failed_evidence_checks`、
`failed_approval_checks` 和 `next_action`。发布活动只能按 `next_action` 前进，不能因
总状态文字相近而把外部证据阻断误判成“只等签字”。任何证据在预签字后发生变化，
都会改变 `release_digest`；必须重新跑 `--require-evidence-ready` 并废弃旧签名。

## 正式发布：只发布通过授权归档复验的 draft

`v2.0.0` tag 工作流负责重新运行 backend、frontend、E2E、Compose/生产基线和镜像漏洞
门禁，并生成不可变镜像及 provenance；它本身不会创建公开 GitHub Release。完成真实
目标 campaign、四方签字、`GA_AUTHORIZED` 归档和独立摘要发布后，发布机构才能执行
`.github/workflows/publish-ga.yml`。仓库管理员必须先为
`ga-production-publication` GitHub Environment 配置发布机构 required reviewers；仅创建同名
Environment 而不配置保护规则不算完成组织审批。

发布机构先为已经签名的 `v2.0.0` tag 建立 draft Release，初始只附加以下两份资产：

- `duckdock-2.0.0-ga-bundle.tar.gz`；
- `duckdock-2.0.0-ga-bundle.manifest.json`。

随后从独立不可变渠道读取 archive SHA-256，在 Actions 中手动运行
`Publish authorized DuckDock GA`，填写 draft release 数字 ID、两个资产名和该裸摘要。
工作流只在 `BaiKudan/DuckDock` 运行，并在受保护 Environment 批准后完成以下闭环：

1. checkout 并通过 GitHub tag verification API 重验 annotated signed `v2.0.0` 与 commit；
2. 要求指定 release 仍是该 tag 的 draft，且初始资产集合精确；
3. 下载到 runner 隔离目录，以独立摘要调用 `authorize_ga_publication.py`；该工具重新运行
   portable archive 的成员/引用/签名/closure/full-evaluator 验证，默认且不可配置地拒绝
   legacy unbound v1；
4. 要求归档内版本为 `2.0.0`、目标为命名的 production `kubernetes-ha`、commit/镜像与
   授权完全一致，并要求 canonical `GA_AUTHORIZED` 到 publication 不超过固定的 24 小时；
   CLI 不提供放宽参数，超时必须重新执行完整 GA 门禁；
5. 从已验证的 release provenance 取出最终 tag CI run ID，再通过 GitHub API 确认该
   `.github/workflows/ci.yml` push run 对同一 commit 的结论为 `success`；
6. 先把不可覆盖的 `duckdock-ga-publication-authorization-v1` 回执及其 SHA-256 sidecar
   附到 draft，回读逐字节比较后才把同一个 draft 切换为公开 Release。失败后若 draft
   已有两份精确回执，重跑会先验证它们并继续，不覆盖既有资产。

回执状态 `GA_PUBLICATION_AUTHORIZED` 表示“允许工作流发布这个精确 draft”，同时固定
`does_not_prove_external_publication=true`；只有工作流最后从 GitHub API 看到
`draft=false` 才证明外部发布动作完成。当前仓库没有真实授权 archive，也没有替发布机构
创建或公开 `v2.0.0` Release。
