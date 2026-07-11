# DuckDock 企业 AI Agent 资产与交接控制平面业务架构图

> 汇报口径：DuckDock 不只是 Skill 仓库，而是企业统一管理 AI Agent 资产、运行现场、工作成果和离职交接的控制平面。

```mermaid
flowchart LR
    BOSS["经营目标<br/>资产看得见<br/>风险管得住<br/>离职接得住"]

    subgraph PEOPLE["人员与组织"]
        EMP["员工 / 外包 / 项目成员"]
        TEAM["部门 / 项目组 / 成本中心"]
        MANAGER["主管 / 交接负责人"]
        SECURITY["安全 / 法务 / 审计"]
    end

    subgraph RUNTIME["多平台 Agent 运行现场"]
        OPENCLAW["OpenClaw / ClawHub<br/>开源与私有化项目"]
        ARKCLAW["火山 ArkClaw<br/>云端 Agent 实例"]
        WORKBUDDY["腾讯 WorkBuddy<br/>桌面 Agent 工作台"]
        JVS["阿里云 JVS<br/>企业智能体套件"]
        CUSTOM["线下定制 Agent<br/>客户现场项目"]
    end

    subgraph ADAPTER["厂商适配与采集层"]
        CONNECTOR["连接器 / Adapter"]
        COLLECTOR["本地采集器 / Sidecar"]
        SYNC["资产同步 / 事件同步"]
    end

    subgraph CONTROL["DuckDock 控制平面"]
        IDENTITY["身份与组织<br/>SSO / LDAP / RBAC"]
        ASSET["AI 资产目录<br/>Skill / Prompt / Workflow / Agent 配置 / 工具连接器"]
        HISTORY["工作历程<br/>任务记录 / 工具调用 / 产物 / 知识沉淀"]
        GOVERNANCE["治理与门禁<br/>扫描 / 沙箱 / 评审 / 质量诊所 / 合规策略"]
        HANDOVER["交接编排<br/>盘点 / 冻结 / 迁移 / 凭据轮换 / 归档 / 签收"]
        AUDIT["审计与报表<br/>责任人 / 时间线 / 风险项 / 交接报告"]
    end

    subgraph OUTPUT["企业交付结果"]
        REUSE["能力可复用<br/>优秀经验沉淀为组织资产"]
        CONTINUE["业务可延续<br/>员工离职后任务和资产不断档"]
        RISK["风险可控制<br/>敏感资产、外部连接和权限可追踪"]
        DELIVERY["代理服务可标准化<br/>多厂商交付统一管理"]
    end

    BOSS --> PEOPLE
    PEOPLE --> RUNTIME
    RUNTIME --> ADAPTER
    ADAPTER --> CONTROL
    CONTROL --> OUTPUT

    EMP -->|"创建与使用"| OPENCLAW
    EMP -->|"桌面办公自动化"| WORKBUDDY
    TEAM -->|"项目交付与私有化"| CUSTOM
    MANAGER -->|"审批与接收"| HANDOVER
    SECURITY -->|"策略与审计"| GOVERNANCE

    OPENCLAW --> CONNECTOR
    ARKCLAW --> CONNECTOR
    JVS --> CONNECTOR
    WORKBUDDY --> COLLECTOR
    CUSTOM --> COLLECTOR
    CONNECTOR --> SYNC
    COLLECTOR --> SYNC

    SYNC --> ASSET
    SYNC --> HISTORY
    IDENTITY --> ASSET
    IDENTITY --> HANDOVER
    ASSET --> GOVERNANCE
    HISTORY --> GOVERNANCE
    GOVERNANCE --> HANDOVER
    HANDOVER --> AUDIT

    HANDOVER --> CONTINUE
    ASSET --> REUSE
    GOVERNANCE --> RISK
    ADAPTER --> DELIVERY
```

## 老板视角说明

这张图表达的是一个业务闭环：

1. 企业里不同员工和团队会在 OpenClaw、ArkClaw、WorkBuddy、JVS、线下定制项目里创建和使用 AI Agent 能力。
2. DuckDock 通过适配器和采集器把这些平台里的资产、工作记录和产物统一纳管。
3. 控制平面负责身份权限、资产目录、工作历程、质量安全、交接编排和审计报表。
4. 当员工离职或项目交接时，系统能自动盘点资产、冻结风险入口、迁移责任人、轮换凭据、归档工作成果并形成交接报告。
5. 最终价值不是“存 Skill”，而是让企业 AI 能力成为可管理、可复用、可交接、可审计的组织资产。

## 一句话定位

DuckDock 是企业 AI Agent 资产与交接控制平面，帮助代理服务商把多厂商、多项目、多员工沉淀下来的 AI 能力统一管理起来，并在人员流动时保证业务不断档、资产不丢失、风险可追踪。
