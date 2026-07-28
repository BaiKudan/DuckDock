# 阿里云 AgentLoop 全景调研与 DuckDock 战略对标报告

> 调研日期：2026-07-17（Asia/Shanghai）
> DuckDock 基线：`main` / `33b4eec54b6ea9a44f612b886dfd6ce46c5870f6` / `v0.1.0`
> 报告性质：产品、技术、商业与落地决策报告
> 结论置信度：产品边界与公开商业规则为高；未开源的内部实现、性能和效果为中低

## 1. 执行摘要

### 1.1 最终判断

“AgentLoop 与 DuckDock 很相似，而且像高级版本”这个判断**有一半正确，但需要重新定义比较对象**。

- 相似之处是真实的：两者都覆盖 Agent 资产、审计、评估、运行数据与治理闭环。
- 关键差异在产品中心：AgentLoop 以**生产运行轨迹和质量优化飞轮**为中心；DuckDock 以**资产供应链、受控发布、证据治理和人员/项目交接**为中心。
- 如果只比较运行可观测、数据集、评测、实验和持续优化，AgentLoop 明显领先 DuckDock。
- 如果比较 Skill 供应链、Scanner/Sandbox Gate、人工审批、证据交接、自托管和数据主权，DuckDock 有明确差异化，部分能力反而更深入。
- 真正可称为“高级版 DuckDock”的不是 AgentLoop 单品，而是阿里云的组合：**AI Registry/MSE + AgentTeams/AgentRun + AgentLoop + ARMS/SLS/RAM + Higress**。

因此，DuckDock 不应复制 AgentLoop，也不应改造成一个闭源云观测产品的平替。更合理的方向是：

> 保持 DuckDock 作为开源、自托管、以资产与执行治理为核心的 Agent Control Plane，并增加一个供应商中立的 Agent Quality Loop 模块，把线上轨迹、数据集、评测和发布门禁连接起来。

### 1.2 建议决策

建议选择“**自建核心闭环 + 可插拔集成**”路线：

1. DuckDock 继续作为资产、权限、审批、证据和交接的事实源。
2. 采用 OpenTelemetry GenAI 语义接收 Agent、Tool、Model 轨迹，但默认只收元数据，内容需显式开启并先脱敏。
3. 建立 Bad Case / Golden Set、评测套件、回归结果和发布门禁，先解决“每次版本变更是否更好”这一高价值问题。
4. 对阿里云客户提供 AgentLoop Connector，同时保留 Langfuse、Phoenix 或自建存储适配器，避免核心数据面锁定。
5. 不在第一阶段复制 eBPF 审计、通用 Memory 平台或全自动自优化；这些投入大、隐私风险高，且不是 DuckDock 当前最强的战略支点。

## 2. 调研范围与证据分级

本报告覆盖：

- AgentLoop 产品定位、架构、接入、观测、审计、数据集、评测、实验、资产和记忆。
- 商业化时间、计费结构、依赖服务、SLA、部署和厂商锁定。
- 官方开源仓库和可验证的工程成熟度。
- DuckDock 当前主分支的代码、文档、数据模型、API、测试资产和部署边界。
- 与 LangSmith、Langfuse、Phoenix 等同类产品的类别定位。
- DuckDock 的目标架构、分阶段路线和 PoC 验收标准。

证据按以下等级使用：

| 等级 | 证据 | 本报告使用方式 |
|---|---|---|
| A | 阿里云产品文档、API 文档、计费、SLA、官方公告 | 可作为当前公开规则，但仍区分正式可用、预览和路线图 |
| B | 官方博客、官方 GitHub 仓库 | 用于理解设计和公开实现，不把营销效果当独立验证结果 |
| C | DuckDock 当前代码与仓库文档 | 用于静态实现审计；没有在本次调研中重新执行全量测试 |
| D | 分析推论 | 明确标为判断、风险或建议，不包装成厂商事实 |

## 3. AgentLoop 到底是什么

### 3.1 官方定位

[AgentLoop 产品概述](https://help.aliyun.com/zh/document_detail/3033860.html)将其定义为面向企业 Agent 的一站式自进化平台，覆盖可观测、审计、评测、实验、资产管理和 Context Engineering。它可以接入 Dify、LangChain/LangGraph、AgentScope、OpenClaw、LlamaIndex、Claude Agent SDK，以及代码类 Agent 工具。

更准确的工程定义是：

> AgentLoop 是部署在既有 Agent 外部的生产质量与数据飞轮控制面，而不是 Agent 开发框架、通用运行时或工作流调度器。

它的核心对象不是“员工、项目、Skill 发布单或交接单”，而是：

- Trajectory：一次 Agent 运行的完整轨迹。
- Dataset：从线上轨迹或人工样本形成的评测数据集。
- Pipeline：从日志到过滤、去重、聚类、采样、标注和数据集的处理链。
- Evaluation：针对模型响应或完整 Agent 轨迹的质量评价。
- Experiment：在数据集上比较不同 Prompt、Skill、Tool、Model 或 Agent 版本。
- Memory / Experience：从历史交互提取可复用上下文。

这些概念在[核心概念文档](https://help.aliyun.com/zh/document_detail/3042001.html)中有明确说明。

### 3.2 五环闭环

[Higress 官方文章](https://higress.ai/blog/higress-mmse_awbbpb_grlu3hqq25wxbuxl)把 AgentLoop 描述为五个循环：观察与审计、轨迹分析、效果评估、实验回测、持续优化。把它翻译成工程数据流，可以得到：

```text
外部 Agent / Tool / Model
          │
          ▼
   OTel / 探针 / SDK 采集
          │
          ▼
Trajectory + Metrics + Logs + Audit Events
          │
          ├──── 在线诊断、风险检测、成本归因
          │
          ▼
Trace2Dataset Pipeline
  过滤 → 去重 → 聚类 → 采样 → 标注
          │
          ▼
 Bad Case / Golden Dataset
          │
          ▼
Evaluation → Experiment → 版本比较
          │
          ▼
Prompt / Skill / Tool / Model / Memory 建议
          │
          └──── 人工确认后进入下一轮
```

这里的“自进化”必须谨慎理解：当前公开证据能证明的是**数据闭环和优化建议**，不能等价解释为平台能够安全、自动地修改并发布生产 Agent。

### 3.3 AgentLoop 不是什么

AgentLoop 不应被误解为：

- Agent 编排或 Durable Execution 引擎。
- 模型网关、模型路由、限流和故障转移产品。
- 完整的 MCP 工具市场或工具执行沙箱。
- 可下载、可私有化部署的开源 Agent 平台。
- Higress 的一个开源子模块。

阿里云的 Agent 构建与运行能力更多归属于 [AgentRun](https://www.alibabacloud.com/help/en/functioncompute/what-is-agentrun)，多 Agent 协作与企业级团队治理更多归属于 [AgentTeams](https://help.aliyun.com/zh/document_detail/3040377.html)，Prompt/Skill 资产管理更接近 [MSE AI Registry / AI 治理中心](https://help.aliyun.com/zh/mse/user-guide/what-is-the-ai-registry-ai-governance-center-2)。Higress 是独立的开源 AI Gateway，文章发布位置不能证明它是 AgentLoop 核心实现。

## 4. AgentLoop 能力全景

### 4.1 接入与可观测

AgentLoop 支持 OpenTelemetry、ARMS 探针、Python/Java SDK 和 MCP 等接入方式，目标是重建 `Agent → Tool → Model` 拓扑，并统一 Trace、Metric 和 Log。其优势在于对阿里云 ARMS、SLS、MSE 和 RAM 的原生整合。

值得借鉴的不是某个专有探针，而是以下产品原则：

- 框架无关：业务 Agent 不必迁移到指定框架。
- 轨迹优先：一次任务中的模型、工具、检索、重试和异常属于同一条 Trajectory。
- 版本关联：轨迹必须能回溯当时的 Skill、Prompt、Tool、Model 和配置版本。
- 成本归因：Token、延迟、失败和重试需要按 Agent、工具、模型、命名空间聚合。

OpenTelemetry 的 GenAI 语义约定仍在演进，包含 `invoke_agent`、`execute_tool` 等 Span 类型，内容记录通常需要显式启用。因此 DuckDock 若采用该标准，应固定兼容版本并设置适配层，而不是把尚在变化的属性名直接写死到业务模型中。[OpenTelemetry GenAI spans 规范](https://github.com/open-telemetry/semantic-conventions/blob/main/model/gen-ai/spans.yaml)

### 4.2 Trace2Dataset Pipeline

[Pipeline 文档](https://help.aliyun.com/zh/cms/cloudmonitor-2-0/user-guide-for-agentloop-pipeline)描述了从 SLS LogStore 到数据集的处理链，包含过滤、去重、聚类、采样和 AI 标注等节点。其战略价值是把“海量但嘈杂的运行日志”转换成“可重复评测的数据资产”。

这比单纯做 Trace UI 高一个层级，因为：

- 线上故障可以沉淀为回归样本。
- 高频同类错误可以聚类，而不是逐条人工阅读。
- 版本对比使用同一个冻结数据集，结果可复现。
- 线上反馈能够进入下一次发布门禁。

产品文档提到 13 类处理节点；该数字可能随版本快速变化，不应作为采购或架构决策的核心依据。

### 4.3 数据集

[数据集文档](https://help.aliyun.com/zh/document_detail/3042278.html)显示其支持自定义 Schema、版本、全文/语义/SQL 检索。对于 Agent 质量平台，数据集至少应区分：

- Golden Set：人工确认的高质量标准样本。
- Bad Case Set：真实线上失败或低评分样本。
- Safety Set：越权、注入、敏感信息和危险工具调用样本。
- Regression Set：与某个 Skill/Prompt/Agent 版本强绑定的回归样本。

DuckDock 当前尚未形成一等公民的数据集、数据集版本和 Case 生命周期，这是双方最大的结构性差距之一。

### 4.4 评测与 Agent-as-a-Judge

[评测文档](https://help.aliyun.com/zh/document_detail/3042180.html)和[评测任务文档](https://help.aliyun.com/zh/document_detail/3042181.html)覆盖模型响应与 Agent 轨迹评价。Agent 类评估器包括轨迹质量、工具选择合理性和工具调用成功等维度。

Agent-as-a-Judge 相比单次 LLM-as-a-Judge 的改进在于：Judge 可以读取完整轨迹、调用验证工具并检查中间步骤。但它不是天然可靠的“自动裁判”，仍必须：

- 使用人工标注集校准。
- 记录 Judge 模型、Prompt、工具与版本。
- 监控与人工评分的一致性和漂移。
- 对安全、合规和发布阻断保留人工复核。

[官方 FAQ](https://help.aliyun.com/zh/document_detail/3042703.html)同时说明，当前主要支持单个 Trace/Trajectory 评价，多轮 Session 评价预计约在 2026 年 9 月提供。这意味着产品仍在快速补齐关键能力。

### 4.5 实验与回放

实验的本质是：在固定数据集上，用同一组指标比较两个或多个配置变体。变量可以是：

- Prompt 版本。
- Skill 版本。
- 工具集、参数 Schema 或权限。
- 模型、温度、上下文策略。
- Memory、RAG 或路由策略。

其价值不只是“做一个排行榜”，而是生成可审计、可复现、能够绑定发布决策的证据。DuckDock 已有发布门禁和人工审批，因此非常适合把实验结果变成现有门禁的一项输入，而不必另造一个发布系统。

### 4.6 运行时审计与安全

AgentLoop 的[审计概述](https://help.aliyun.com/zh/document_detail/3045691.html)同时覆盖应用会话日志和 eBPF 运行时事件，可关注进程、文件、网络、提示注入、敏感文件、凭据与数据外发风险；相关能力还包括[规则配置](https://help.aliyun.com/zh/document_detail/3045694.html)和[风险事件](https://help.aliyun.com/zh/document_detail/3045696.html)。

这是 AgentLoop 对一般 LLM Observability 产品的重要差异，但也带来最高级别的治理风险：

- Prompt、输出、工具参数和结果可能包含个人信息、密钥或商业数据。
- eBPF 文件与网络观测会扩大采集边界。
- 脱敏、采样、保留期、跨境和内部访问必须在采集前设计。
- “不可篡改证据链”等表述目前主要来自厂商文档，未发现可公开审计的核心实现。

DuckDock 不应在第一阶段复制 eBPF。优先级更高的是建立可信的应用层轨迹、内容最小化采集、脱敏和来源证明。

### 4.7 资产、Memory 与 Context Engineering

AgentLoop 把 Prompt、Skill、数据集、经验和 Memory 放入同一个 Context Engineering 视角。[核心概念文档](https://help.aliyun.com/zh/document_detail/3042001.html)描述了 Facts、Episodic、Summary 和 Custom Memory 类型，并兼容 Mem0 风格接口。

这一方向有战略价值，但公开文档显示经验库仍有内测属性，Context Engineering 和 MSE 资产能力也处于公开预览阶段。Memory 后端、抽取、混合检索、重排和隔离的核心实现没有开源，不能仅凭适配器推断其工程质量。

## 5. 产品成熟度与事实边界

### 5.1 当前可确认状态

| 维度 | 公开状态 | 专业判断 |
|---|---|---|
| 轨迹观测 | 已有产品文档与阿里云服务接入 | 产品核心能力，云上整合较强 |
| Dataset / Pipeline | 已有使用与计费文档 | 已产品化，但规模性能缺少独立基准 |
| 单 Trace/Trajectory 评测 | 当前支持 | 可用于离线回归，Judge 仍需人工校准 |
| 多轮 Session 评测 | 官方 FAQ 指向约 2026-09 | 路线图能力，不应计入当前已交付 |
| Playground | 文档存在时间表和预览痕迹 | 状态快速变化，采购前需租户实测 |
| Context Engineering | 公开预览 | 不宜承担核心生产 SLA |
| 经验库 | 内测/渐进开放 | 概念领先于可验证成熟度 |
| 核心服务端源码 | 未公开 | 无法做源码级安全、HA 和算法审计 |
| 私有化部署 | 未发现官方核心部署方案 | 以阿里云托管 SaaS 看待 |

### 5.2 厂商宣称与独立可验证性

以下能力可以写成“官方文档宣称”，但不能写成“本次已经验证”：

- 20+ 评估器、Agent-as-a-Judge 的实际准确率。
- Pipeline 的高成功率和人工节省比例。
- 不可篡改审计、风险检测的召回率与误报率。
- 探针对延迟、吞吐和资源占用的影响。
- 高并发、高可用和多租户隔离强度。
- Memory 提取、检索与持续学习效果。

官方不同页面还存在“13 个评估器”与“20+ 评估器”等口径差异。这更像快速迭代期的文档版本差异，不适合把具体数量作为产品领先性的主要证据。

### 5.3 SLA

[AgentLoop SLA](https://terms.alicdn.com/legal-agreement/terms/b_end_product_protocol/20260624152513992/20260624152513992.html)对覆盖范围内的付费服务给出 99.9% 月度可用性承诺，但预览、邀测和免费能力通常不在相同保障范围内。任何采购判断都应逐项确认所用模块是否已经进入 SLA 覆盖。

## 6. 商业模式与总体拥有成本

### 6.1 商业化时间

[阿里云商业化公告](https://www.alibabacloud.com/en/notice/commercialization_notice_for_agentloop_795?_p_lc=1)显示 AgentLoop 计划于 **2026-07-20** 开始正式计费；本报告日期为 2026-07-17，因此产品正处于商业化切换前夕。

### 6.2 公开计费结构

[计费说明](https://help.aliyun.com/zh/document_detail/3044490.html)列出的主要价格为：

| 项目 | 公开单价/口径 |
|---|---|
| AI Credits | ¥0.01 / Credit |
| 评测 | 官方估算约 10 Credits / 次 |
| 实验 | 官方估算约 1 Credit / 次 |
| Dataset 存储 | ¥0.00004 / 条 / 天 |
| Pipeline 执行 | ¥0.001 / 次 |
| 新用户权益 | 31 天；10,000 Credits、31,000 条/天存储、2,000 次执行 |

官方示例中，2,000 Credits、10,000 条日存储和 10,000 次执行合计约 ¥30.4/天；按 30 天静态外推约 ¥912/月。这个数字**不包含**模型调用、ARMS 观测、SLS 存储与查询、网络流量，以及 MSE 预览结束后的潜在费用。

### 6.3 TCO 风险

AgentLoop 采购不能只计算 AgentLoop 账单，还需同时计算：

```text
AgentLoop Credits / Dataset / Pipeline
+ ARMS 探针与可观测
+ SLS 审计、评测和实验结果
+ MSE 资产治理未来费用
+ Judge / 被测模型 Token
+ 数据保留、跨地域与出口流量
+ 团队运营和人工标注
```

对高流量 Agent，最大的成本未必是平台执行单价，而可能是内容存储、Judge 模型和全量轨迹保留。

## 7. 开源、部署与厂商锁定

### 7.1 核心平台没有开源

截至 2026-07-17，在 `aliyun`、`alibaba` 和 `higress-group` 官方 GitHub 组织中，没有发现 AgentLoop 服务端、控制台、评测引擎、Pipeline、审计引擎、Memory 后端、数据库 Schema 或私有化部署代码。

官方目前有代码的仓库主要是：

- [AgentLoop Memory MCP Server](https://github.com/aliyun/alibabacloud-agentloop-memory-mcp-server)：把托管 MemoryStore 暴露为 MCP SSE 服务。
- [AgentLoop Agent FS](https://github.com/aliyun/alibabacloud-agentloop-agent-fs)：通过 FUSE/provider 抽象访问 AgentLoop Memory 和 Nacos。

另有两个同名仓库在核验时为空。两个有代码的适配器采用 MIT License，也有测试雏形，但没有正式 Tag/Release 和常规自动化 test/build CI。官方[Memory MCP 快速接入](https://help.aliyun.com/en/cms/cloudmonitor-2-0/mcp-server-quick-access)仍要求预先开通阿里云 AgentLoop Memory、Workspace 和 Memory Store，本地运行的是代理，不是 Memory 后端。

因此，公开代码证明的是“可接入的客户端适配层”，而不是“AgentLoop 可自托管”。

### 7.2 锁定程度

综合判断：**核心平台锁定高，接入层锁定中等**。

高锁定来源：

- 核心服务不可自托管。
- AgentSpace 与 CloudMonitor、MSE、SLS、RAM 绑定。
- Memory 适配器依赖阿里云账号、Region、Workspace 和 Memory Store。
- 未发现完整数据 Schema、批量可迁移导出、替代后端 SPI 或核心部署包。

降低锁定的因素：

- 支持 OpenTelemetry。
- 接入框架覆盖较广。
- MCP/Agent FS 适配器采用 MIT License。

“框架无关”降低的是 Agent 代码接入成本，不等于平台数据可自由迁移。

## 8. 阿里云组合栈与 DuckDock 的正确比较方式

| 阿里云层次 | 主要职责 | 与 DuckDock 的对应关系 |
|---|---|---|
| Higress | AI/API Gateway、流量治理 | DuckDock 当前没有一等模型网关 |
| AgentRun | Agent 构建、部署、Serverless 运行与沙箱 | DuckDock 明确不是通用 Agent Runtime |
| AgentTeams | 多 Agent、团队和资源协作治理 | 对应 DuckDock 的组织/Agent 管理的一部分 |
| MSE AI Registry | Prompt/Skill 版本、分发和治理 | 最接近 DuckDock Skills 资产供应链 |
| AgentLoop | 观测、审计、Dataset、评测、实验、Memory | 对应 DuckDock 尚待加强的运行质量闭环 |
| ARMS/SLS/RAM | 遥测、日志、存储与云 IAM | 对应 DuckDock 的基础设施和审计底座 |

所以，“阿里云方案比 DuckDock 高级”在广度上成立，但这是多款云产品拼成的组合，并附带云依赖、成本和数据主权代价。AgentLoop 单品只覆盖其中一层。

## 9. DuckDock 当前能力审计

### 9.1 当前产品中心

DuckDock 当前定位是企业级 AI Agent 资产治理控制平面，包含两个明显支柱：

1. Skills 资产注册、Git 版本、扫描、沙箱、Clinic、发布 Gate 和分发。
2. Agent 工作/证据采集、分析、人工审批、离职与项目交接。

核心设计强调 Push-only、结构化报告包、最小暴露和“AI 只建议、不替代审批”。可参考仓库的 [README](../README.md)、[架构文档](architecture.md)和[控制面管理员指南](control-plane-admin-guide.zh-CN.md)。

### 9.2 静态实现规模

对当前主分支的静态统计为：

| 指标 | 数量 |
|---|---:|
| SQLAlchemy 业务表 | 57 |
| API 路由模块 | 18 |
| API endpoint 装饰器 | 202 |
| Alembic 迁移 | 26 |
| 后端测试文件 | 56 |
| 后端 `test_*` 函数 | 503 |
| 前端单元测试文件 | 5 |
| Playwright 文件 | 2 |
| 前端测试用例 | 24 |

本次调研只做代码与文档静态审计，未安装被清理的依赖或重新执行全量测试。因此这些数字证明“测试资产和 CI 结构存在”，不表示本次已经重新确认所有测试为绿色。

### 9.3 已形成的强项

#### Skill 供应链和发布门禁

- Skill/SkillVersion、命名空间与版本来源已形成一等数据模型。
- Scanner、Sandbox、Clinic 与 Release Gate 已连成受控发布链。
- 发布结果能够绑定治理检查和人工审批。
- 相比 AgentLoop 公开预览中的资产管理，DuckDock 这一部分更接近可审计的工程实现。

关键实现包括：

- `backend/app/api/v1/endpoints/skills.py`
- `backend/app/services/release_gate_service.py`
- `backend/app/workers/scan_tasks.py`
- `backend/app/models/skill.py`
- `backend/app/models/governance.py`

#### 证据与交接闭环

- 支持结构化报告和较重的报告包上传。
- 支持分析租约、队列和元数据。
- 离职/项目交接采用明确状态机、人工收据、证据验证和交接包。
- 自动交接路径明确返回未实现，而不是伪装成已完成，这与“人类批准优先”的产品边界一致。

这一能力不是 AgentLoop 的核心目标，也是 DuckDock 最具辨识度的企业价值。

#### IAM 与组织治理

- 已有组织、命名空间、角色和授权模型。
- 具备 SSO/IAM 的结构基础。
- 但真实 IdP/SCIM、Reporter 自注册策略仍有未完成任务，不能等同于完整企业身份生命周期。

### 9.4 当前薄弱点

#### 运行轨迹不是一等对象

当前 `WorkTrace` 更接近工作摘要/历史记录，不是包含 Agent、Turn、Step、Tool、Model、Token 和异常的标准轨迹。通用适配器返回空或可用性信息，OpenClaw 适配器主要提供元数据样例；现有 Push 报告也不能替代完整运行 Trace。

#### Clinic 不是完整的 Agent 评测平台

Clinic 已覆盖多个静态质量维度，并支持 LLM Judge 与确定性回退；但它主要评价 Skill/资产质量，而非：

- 在冻结 Dataset 上回放完整 Agent 轨迹。
- 追踪每次 EvalRun 的 Judge、版本、输入和结果。
- 比较两组运行配置并做统计判断。
- 形成线上 Bad Case 到下次发布门禁的自动闭环。

#### 缺少一等质量对象

当前没有完整的一等对象族：Trajectory、DatasetVersion、Case、EvalSuite、EvalRun、Experiment、Variant、PromptVersion 和在线 SafetyFinding。`MemoryCandidate` 是待审候选，不等同于生产 Memory Service；`AssetType.PROMPT` 也不等同于完整 Prompt Registry。

#### 企业生产边界仍需补强

- 审计日志不是外部 WORM 证据链，部分审计异常被容错处理。
- 生产部署以单 Compose 为主，没有完整 HA/Kubernetes/恢复演练证据。
- 没有 Prometheus/Grafana/OTel 级 Agent 拓扑、成本和 Session Replay。
- Reporter 自报数据需要更强的签名、来源证明和防重放机制。
- 仓库明确没有第三方安全审计。

## 10. AgentLoop 与 DuckDock 能力矩阵

| 维度 | AgentLoop | DuckDock 当前 | 判断 |
|---|---|---|---|
| 产品中心 | 生产质量与数据飞轮 | 资产、治理、证据、交接 | 相邻但不同层 |
| Agent Runtime | 不是核心 Runtime | 不是通用 Runtime | 都应与运行时解耦 |
| 轨迹采集 | OTel/ARMS/SDK，多框架 | Push 报告、适配器较浅 | AgentLoop 明显领先 |
| Agent/Tool/Model 拓扑 | 官方产品能力 | 尚未形成 | AgentLoop 领先 |
| Token/延迟/成本归因 | 官方产品能力 | 尚未形成 | AgentLoop 领先 |
| Trace2Dataset | Pipeline 已产品化 | 尚未形成 | AgentLoop 领先 |
| Dataset 版本 | 一等对象 | 尚未形成 | AgentLoop 领先 |
| Agent 轨迹评测 | 当前支持单轨迹 | Clinic 偏静态 Skill 质量 | AgentLoop 领先 |
| 多轮 Session 评测 | 路线图约 2026-09 | 尚未形成 | 双方都不成熟 |
| 实验/回放 | 一等产品概念 | 尚未形成 | AgentLoop 领先 |
| Prompt/Skill 资产 | MSE/AgentLoop 预览能力 | Skill 版本与发布链完整 | DuckDock 在 Skill 供应链更实 |
| Scanner/Sandbox Gate | 非 AgentLoop 核心 | 已实现受控门禁 | DuckDock 领先 |
| 运行时安全审计 | 应用日志 + eBPF | 业务审计和证据审计 | 方向不同；AgentLoop 更广 |
| 交接/离职治理 | 非核心场景 | 核心状态机与证据闭环 | DuckDock 显著领先 |
| 人工批准约束 | 产品文档未突出 | 架构硬边界 | DuckDock 差异化 |
| Memory | 托管产品与适配器 | 候选/审查模型 | AgentLoop 领先，但核心闭源 |
| 多租户/IAM | AgentSpace + RAM | 组织/命名空间/RBAC | 都有；DuckDock 可审计性更高 |
| 自托管 | 核心不可用 | 原生目标 | DuckDock 显著领先 |
| 数据主权 | 阿里云区域和服务边界 | 客户可控部署 | DuckDock 显著领先 |
| 核心源码可审计 | 否 | 是 | DuckDock 显著领先 |
| 生态集成 | 阿里云栈和主流框架广 | 当前集成较少 | AgentLoop 领先 |

### 10.1 一句话归纳

- AgentLoop 强在“Agent 上线以后，如何知道哪里坏了、为何坏、如何形成样本并验证改进”。
- DuckDock 强在“Agent 资产如何进入企业、怎样安全发布、谁能操作、证据怎样留存、人员离开后怎样受控交接”。

二者不是零和关系。DuckDock 的最佳机会是把这两条链连起来。

## 11. 同类产品与市场位置

AgentLoop 更接近 LLM/Agent Engineering 平台，而不是通用 Agent IDE：

- [Arize Phoenix](https://arize.com/docs/phoenix)提供开源/自托管的 Trace、评测、Prompt、Dataset 和 Experiment，并采用 OpenTelemetry/OpenInference。
- [LangSmith Evaluation](https://docs.langchain.com/langsmith/evaluation)覆盖离线/在线评测、数据集、实验与反馈闭环。
- [Langfuse Experiments](https://langfuse.com/docs/evaluation/experiments/data-model)把 Dataset、Run、Item 和 Score 组织成可复现实验。

AgentLoop 的差异化主要是阿里云原生整合、应用到 eBPF 的安全观测、Agent-as-a-Judge 和 Context Engineering 叙事。Trace、Dataset、Eval 和 Experiment 本身已是该类别的共同基线能力，并非 AgentLoop 独有。

对 DuckDock 来说，这意味着质量闭环应采用供应商中立的数据模型，AgentLoop 只是一个重要后端/连接器，而不是唯一架构参考。

## 12. 风险清单

| 风险 | 概率 | 影响 | 建议控制 |
|---|---|---|---|
| 把营销“自进化”理解为自动生产变更 | 中 | 高 | 所有优化建议进入现有人工审批与发布 Gate |
| 全量记录 Prompt/工具结果导致隐私泄露 | 高 | 高 | 元数据默认、内容显式开启、采集前脱敏、短 TTL |
| Judge 评分偏差或漂移 | 高 | 高 | 人工 Golden Set、版本化 Judge、持续一致性校准 |
| AgentLoop/ARMS/SLS/MSE 组合成本失控 | 中 | 高 | PoC 先测每千 Session 成本，设置采样与预算上限 |
| OTel GenAI Schema 快速变化 | 高 | 中 | Collector 适配层、Schema 版本、原始属性保留 |
| 绑定阿里云数据面后迁移困难 | 中 | 高 | 内部中立模型、批量导出、双写或可插拔后端 |
| 把自报 Trace 当成可信证据 | 高 | 高 | Agent 身份、签名、时间戳、Nonce、防重放、哈希链 |
| 过早做 eBPF 导致周期和权限失控 | 中 | 高 | 延后到应用层闭环稳定之后，独立安全评审 |
| DuckDock 同时复制所有 AgentLoop 模块 | 高 | 高 | 聚焦发布回归闭环，不做大而全的平台追赶 |
| 预览模块被当成生产 SLA 能力 | 中 | 高 | 按租户逐项验收，合同中明确 GA/SLA 范围 |

## 13. DuckDock 推荐目标架构

### 13.1 设计原则

1. **治理事实源不变**：DuckDock 继续管理组织、资产、版本、审批、证据和交接。
2. **质量数据面可替换**：AgentLoop、Langfuse、Phoenix 或自建后端通过 Provider 接口接入。
3. **业务库边界不变**：MySQL 只保存业务元数据、索引、摘要、评测指针和审批关系；高量 Trace 放入可选观测存储。
4. **内容最小化**：默认只采元数据；Prompt、输出、RAG 文本和 Tool 结果需要策略授权。
5. **建议不自动发布**：LLM 可生成诊断、评分或候选优化，但生产变更仍走人工 Gate。
6. **一次采集，多处使用**：同一条轨迹服务于故障定位、成本、数据集、评测、安全和审计，避免重复上报。

### 13.2 建议逻辑架构

```text
Agent Runtime / WorkBuddy / OpenClaw / Hermes / Custom Agent
                  │
         OTLP + signed reporter envelope
                  │
                  ▼
        DuckDock Telemetry Gateway
   schema adapter / auth / dedupe / redaction
                  │
          ┌───────┴────────┐
          ▼                ▼
DuckDock MySQL         Observability Provider
metadata/index/ACL     AgentLoop | Phoenix |
eval pointers          Langfuse | ClickHouse
          │                │
          └───────┬────────┘
                  ▼
        Dataset & Evaluation Control Plane
 bad case / golden set / suite / run / result
                  │
                  ▼
      Existing Skill Release Gate & Approval
                  │
                  ▼
  publish / canary / rollback / evidence package
```

### 13.3 建议新增的一等对象

不要把现有 `WorkTrace` 强行扩展成运行遥测万能表。建议新增或以外部引用表达：

- `AgentSession`：一次用户目标或多轮会话。
- `AgentTrajectoryRef`：外部 Trace ID、Provider、租户、采样和内容策略。
- `AgentRunVersion`：绑定 Skill、Prompt、Tool、Model 和配置版本。
- `Dataset` / `DatasetVersion` / `DatasetCase`。
- `EvaluationSuite` / `EvaluationRun` / `EvaluationResult`。
- `Experiment` / `Variant` / `ExperimentRun`。
- `PromptArtifact` / `PromptVersion`。
- `SafetyFinding`：注入、越权、敏感信息、危险工具调用。
- `RuntimeCostDaily`：按命名空间、Agent、模型和工具汇总。

高量 Span 不建议全部落 MySQL。MySQL 保存租户、权限、版本关系、状态和摘要；原始遥测保存在 Provider 或可选 ClickHouse 等观测存储，原始大附件可进入 MinIO。这样既遵守 DuckDock 业务数据库边界，也为不同客户保留部署选择。

### 13.4 Provider 接口

至少定义以下可替换接口：

```text
TelemetryProvider.ingest/query/get_trajectory
DatasetProvider.materialize/export/import
EvaluatorProvider.evaluate/calibrate
ExperimentProvider.run/compare
MemoryProvider.add/search/delete/export
AuditSink.append/verify/export
```

首批实现建议为：

- `duckdock-native-metadata`：所有部署都可用。
- `langfuse` 或 `phoenix`：开源、自托管参考后端。
- `aliyun-agentloop`：面向阿里云客户的托管连接器。

## 14. 分阶段路线图

以下周期按 **2 名后端 + 1 名前端 + 0.5 名平台/安全工程师**并行估算，是规划区间，不是交付承诺。

### P0：架构、可信采集与隐私基线（4–6 周）

目标：能够可靠回答“哪个版本在何时执行了什么，数据是否可信”。

- 冻结内部 Trajectory Envelope 与 OTel GenAI 适配版本。
- 建立 OTLP/HTTP Telemetry Gateway。
- 增加 Agent 身份、签名、Nonce、防重放和幂等去重。
- 实现元数据默认、内容显式启用、采集前脱敏和租户 TTL。
- 关联 Agent、SkillVersion、运行配置、Tool、Model 和 Trace ID。
- 接入一个真实运行时，不接受仅返回样例元数据的“假集成”。
- 提供基础 Session/Trajectory 列表、错误、Token、延迟和成本摘要。

完成标准：真实端到端轨迹可查、来源可验证、敏感测试数据不以明文进入存储。

### P1：Dataset、评测与回归发布门禁（6–10 周）

目标：把线上失败变成可重复的发布证据。

- 建立 Bad Case、Golden Set、DatasetVersion 和 Case 审核流。
- 支持确定性评估器、LLM Judge 和人工评分。
- 版本化 Judge Prompt、模型、工具和阈值。
- 实现 EvaluationSuite/Run/Result 和可复现重跑。
- 将回归结果接入现有 Skill Release Gate。
- 增加按版本的质量、成本、延迟和失败趋势。

完成标准：一个已知回归能从线上 Trace 进入数据集，并阻断有问题的下一版 Skill 发布。

### P2：实验、灰度和在线风险（8–12 周）

目标：让变更比较从人工印象升级为受控实验。

- 支持两个或多个 Skill/Prompt/Model 变体离线比较。
- 增加小流量 Canary、回滚条件和预算上限。
- 把 Prompt 升级为完整版本资产。
- 增加 Prompt Injection、敏感信息、越权工具调用评估器。
- 支持 AgentLoop 与至少一个开源后端 Provider。
- 生成可附加到审批/交接包的实验与风险证据。

### P3：Memory/Experience 与企业韧性（后续）

目标：在基础闭环稳定后扩展上下文优化和大规模生产能力。

- Memory Provider SPI、Facts/Episodic/Summary 与可删除/可导出策略。
- 经验提取只生成候选，人工确认后生效。
- 外部 WORM Audit Sink、哈希验证和证据导出。
- Kubernetes/HA、备份恢复演练、SCIM 与真实 IdP 完整接入。
- 评估是否需要独立 eBPF 安全采集；不与主产品进度捆绑。

### 14.1 预期时间判断

- 可用的“轨迹 + 数据集 + 回归门禁”MVP：约 3–4 个月。
- 覆盖实验、在线风险、多后端和企业韧性的广义能力：约 6–12 个月以上。
- 追求 AgentLoop 全面功能对等并不经济，也不符合 DuckDock 的差异化定位。

## 15. 四周 PoC 方案

### 15.1 范围

- 选择一个真实 WorkBuddy、Hermes、OpenClaw 或内部 Agent。
- 收集 500–1,000 个 Session；内容按采样与脱敏策略进入。
- 实现 3 类评估器：任务完成、工具调用成功、安全风险。
- 从线上失败形成 Bad Case Dataset。
- 比较两个 Skill 或 Prompt 版本。
- 将结果接入现有 Release Gate，但保持人工批准。

### 15.2 建议验收指标

以下是 DuckDock 的建议目标，不是 AgentLoop 官方指标：

| 指标 | PoC 目标 |
|---|---:|
| Session/Tool/Model 关联完整率 | ≥95% |
| 遥测接收成功率 | ≥99% |
| 已知测试密钥/PII 明文入库 | 0 |
| Judge 与人工标签一致率 | ≥80% |
| 或 Cohen's κ | ≥0.70 |
| 同一 Dataset/版本重复运行 | 结果可复现并保留完整版本信息 |
| 已知回归 | 能被 Gate 阻断并可人工覆盖 |
| 成本 | 能给出每 1,000 Session 的平台、存储和模型成本 |
| 回滚 | Canary 失败可在约定窗口内人工回滚 |

### 15.3 AgentLoop 的 PoC 采购问题

若同步试用 AgentLoop，必须让厂商在真实租户内回答并演示：

1. 当前哪些模块是 GA、预览或邀测，分别适用什么 SLA？
2. 一条真实 Trajectory 的端到端延迟、丢 Span 率和探针开销是多少？
3. 全量导出 Trajectory、Dataset、Eval Result 和 Memory 的格式与 API 是什么？
4. PII/密钥脱敏默认是否开启，发生在客户端、采集端还是存储后？
5. eBPF 采集需要何种权限，哪些数据会离开业务网络？
6. 多租户和 AgentSpace 的物理/逻辑隔离如何验证？
7. Judge 的人工校准、版本记录、误报和漂移如何管理？
8. ARMS、SLS、MSE、模型和出口流量的完整月度账单模拟是多少？
9. 删除请求是否同时删除索引、向量、缓存、审计副本和备份？
10. 从 AgentLoop 迁移到其他 OTel 后端需要哪些步骤？

## 16. Buy / Integrate / Build 决策

| 选项 | 优点 | 缺点 | 适用条件 |
|---|---|---|---|
| 全量采用 AgentLoop | 上线快、阿里云整合强、功能广 | 闭源、成本叠加、数据面锁定、预览能力多 | 核心业务已在阿里云且接受托管 |
| DuckDock 全部自建 | 数据主权、可审计、完全可控 | 周期长，容易陷入追求全面对等 | 强私有化/合规客户，团队资源充足 |
| 自建控制面 + 可插拔后端 | 保留差异化，复用成熟观测/评测能力 | Provider 和数据一致性设计更复杂 | **推荐；符合 DuckDock 当前阶段** |

最终建议：

> 把 AgentLoop 当作 DuckDock “运行质量层”的标杆和可选 Provider，而不是把 DuckDock 定义成 AgentLoop 的追赶者。

DuckDock 的战略叙事可以升级为：

> 开源、自托管、以资产供应链和人类治理为核心的 Agent Control Plane；让每一次 Agent 运行都能回到数据集、评测、发布门禁和可验证证据中。

## 17. 结论

AgentLoop 的产品设计先进，尤其在生产轨迹、Trace2Dataset、Agent 评测、实验和 Context Engineering 的连贯性上，为 DuckDock 提供了清晰的下一阶段参考。但它的核心是阿里云托管闭源服务，多个关键模块仍处于预览、内测或明确路线图阶段，公开适配器不能证明核心实现质量，也不能替代真实租户 PoC。

DuckDock 当前不是一个落后的 AgentLoop，而是位于不同控制平面：它在 Skill 供应链、受控发布、人工审批、证据交接和自托管方面拥有可持续差异化。真正需要补齐的不是“所有 AgentLoop 功能”，而是最关键的一段：

```text
真实运行轨迹 → 可治理数据集 → 可复现评测 → 版本实验 → 发布 Gate → 证据与回滚
```

只要这一段与现有治理系统打通，DuckDock 就能形成比单纯 Observability 平台更完整的企业闭环，同时保留开放部署和人类责任边界。

## 18. 主要资料来源

### AgentLoop 官方资料

- [Higress：阿里云刚发布的 AgentLoop 是什么？](https://higress.ai/blog/higress-mmse_awbbpb_grlu3hqq25wxbuxl)
- [AgentLoop 产品概述](https://help.aliyun.com/zh/document_detail/3033860.html)
- [AgentLoop 核心概念](https://help.aliyun.com/zh/document_detail/3042001.html)
- [AgentLoop 快速入门](https://help.aliyun.com/en/document_detail/3033823.html)
- [Dataset](https://help.aliyun.com/zh/document_detail/3042278.html)
- [Pipeline](https://help.aliyun.com/zh/cms/cloudmonitor-2-0/user-guide-for-agentloop-pipeline)
- [Evaluation](https://help.aliyun.com/zh/document_detail/3042180.html)
- [Evaluation Task](https://help.aliyun.com/zh/document_detail/3042181.html)
- [Audit Overview](https://help.aliyun.com/zh/document_detail/3045691.html)
- [FAQ](https://help.aliyun.com/zh/document_detail/3042703.html)
- [计费说明](https://help.aliyun.com/zh/document_detail/3044490.html)
- [商业化公告](https://www.alibabacloud.com/en/notice/commercialization_notice_for_agentloop_795?_p_lc=1)
- [OpenAPI Operations](https://help.aliyun.com/en/document_detail/3041792.html)
- [API Change Log](https://help.aliyun.com/zh/document_detail/3041796.html)
- [SLA](https://terms.alicdn.com/legal-agreement/terms/b_end_product_protocol/20260624152513992/20260624152513992.html)

### 官方开源与生态资料

- [aliyun/alibabacloud-agentloop-memory-mcp-server](https://github.com/aliyun/alibabacloud-agentloop-memory-mcp-server)
- [aliyun/alibabacloud-agentloop-agent-fs](https://github.com/aliyun/alibabacloud-agentloop-agent-fs)
- [AgentRun](https://www.alibabacloud.com/help/en/functioncompute/what-is-agentrun)
- [AgentTeams](https://help.aliyun.com/zh/document_detail/3040377.html)
- [MSE AI Registry / AI Governance](https://help.aliyun.com/zh/mse/user-guide/what-is-the-ai-registry-ai-governance-center-2)
- [OpenTelemetry GenAI semantic conventions](https://github.com/open-telemetry/semantic-conventions/blob/main/model/gen-ai/spans.yaml)
- [Arize Phoenix](https://arize.com/docs/phoenix)
- [LangSmith Evaluation](https://docs.langchain.com/langsmith/evaluation)
- [Langfuse Experiments](https://langfuse.com/docs/evaluation/experiments/data-model)

### DuckDock 本地证据

- [README](../README.md)
- [Architecture](architecture.md)
- [Production Deployment](production-deployment.md)
- [Permission Matrix](permission-matrix.zh-CN.md)
- [Clinic Langfuse](clinic-langfuse.zh-CN.md)
- `backend/app/api/v1/endpoints/skills.py`
- `backend/app/api/v1/endpoints/control_plane.py`
- `backend/app/services/release_gate_service.py`
- `backend/app/services/clinic_service.py`
- `backend/app/services/structured_report_service.py`
- `backend/app/services/analysis_service.py`
- `backend/app/services/adapters/`

---

本报告中的产品状态、价格、仓库活跃度和路线图均以 2026-07-17 的公开信息为准；AgentLoop 处于快速迭代与商业化切换阶段，正式采购或集成前应重新核验。
