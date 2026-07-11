# DuckDock 企业权限矩阵

本文档定义 DuckDock 当前企业权限模型与业务能力的对应关系，作为：

- 企业实施时的权限设计基线
- 前后端鉴权开发时的统一语义来源
- OIDC / LDAP / SSO claim/group 映射到 DuckDock RBAC 的依据

## 1. 权限模型分层

DuckDock 当前的权限判定有 3 层：

1. `system_role`
   - 历史系统级角色
   - 当前包含 `admin` 和 `user`
   - `admin` 仍然拥有全局兜底权限

2. legacy namespace member role
   - 历史命名空间成员角色
   - 包含 `admin` / `developer` / `readonly`
   - 仍然保留兼容，用于旧数据平滑迁移

3. enterprise RBAC
   - 通过 `Role / Permission / RoleBinding` 实现
   - 支持：
     - 全局绑定
     - 组织单元绑定
     - 命名空间绑定
   - 是后续企业化权限的主方向

4. SSO JIT 映射
   - 通过 `users.enterprise_uid` 统一企业员工 UID
   - 通过 `IdentityLink` 记录不同 provider 的 subject / external_uid
   - 通过 `SSORoleMapping` 将 IdP claim/group 映射成 DuckDock `RoleBinding`
   - 只创建/刷新/撤销由 SSO 管理的绑定，不覆盖管理员手工授权

## 2. 作用域说明

### 2.1 system scope

用于整个平台级治理能力，例如：

- IAM 管理
- SSO 提供方管理
- 全局审计读取

### 2.2 org scope

用于企业组织目录能力，例如：

- 查看组织结构
- 管理组织单元
- 管理用户任职关系

### 2.3 namespace scope

用于命名空间内的资源访问和治理，例如：

- 技能与版本
- Scan
- Clinic
- 生命周期
- Webhook / Robot / Replication

## 3. 当前权限点

### 3.1 system 权限

- `iam.manage`
  - 管理 IAM 模型、角色、绑定、SSO 配置
- `users.manage`
  - 管理企业用户目录属性
- `audit.read`
  - 读取全局审计日志
- `sso.manage`
  - 管理 OIDC / LDAP 提供方配置

### 3.2 org 权限

- `org.read`
  - 读取组织架构和任职关系
- `org.manage`
  - 管理组织单元和用户任职关系

### 3.3 namespace 权限

- `namespace.read`
  - 读取命名空间内资源
- `namespace.write`
  - 修改命名空间内技能和版本内容
- `namespace.admin`
  - 管理命名空间及其成员
- `namespace.members.manage`
  - 管理命名空间成员
- `namespace.lifecycle.manage`
  - 管理配额、保留策略、GC
- `namespace.integrations.manage`
  - 管理 Webhook、Robot、复制规则等集成能力
- `namespace.scan.read`
  - 查看 Scan 结果
- `namespace.scan.run`
  - 触发 Scan
- `namespace.clinic.read`
  - 查看 Clinic 评测和报告
- `namespace.clinic.run`
  - 触发 Clinic 评测

## 4. 内置角色

### 4.1 企业级内置角色

- `enterprise-admin`
  - scope: `system`
  - 权限：
    - `iam.manage`
    - `users.manage`
    - `audit.read`
    - `org.read`
    - `org.manage`
    - `sso.manage`

- `org-manager`
  - scope: `org`
  - 权限：
    - `org.read`
    - `org.manage`

### 4.2 命名空间内置角色

- `namespace-admin`
  - scope: `namespace`
  - 权限：
    - `namespace.read`
    - `namespace.write`
    - `namespace.admin`
    - `namespace.members.manage`
    - `namespace.lifecycle.manage`
    - `namespace.integrations.manage`
    - `namespace.scan.read`
    - `namespace.scan.run`
    - `namespace.clinic.read`
    - `namespace.clinic.run`

- `namespace-developer`
  - scope: `namespace`
  - 权限：
    - `namespace.read`
    - `namespace.write`
    - `namespace.scan.read`
    - `namespace.scan.run`
    - `namespace.clinic.read`
    - `namespace.clinic.run`

- `namespace-readonly`
  - scope: `namespace`
  - 权限：
    - `namespace.read`
    - `namespace.scan.read`
    - `namespace.clinic.read`

## 5. legacy 角色到 RBAC 的兼容映射

### 5.1 legacy namespace admin

等价于：

- `namespace.read`
- `namespace.write`
- `namespace.admin`
- `namespace.members.manage`
- `namespace.lifecycle.manage`
- `namespace.integrations.manage`
- `namespace.scan.read`
- `namespace.scan.run`
- `namespace.clinic.read`
- `namespace.clinic.run`

### 5.2 legacy namespace developer

等价于：

- `namespace.read`
- `namespace.write`
- `namespace.scan.read`
- `namespace.scan.run`
- `namespace.clinic.read`
- `namespace.clinic.run`

### 5.3 legacy namespace readonly

等价于：

- `namespace.read`
- `namespace.scan.read`
- `namespace.clinic.read`

## 6. 业务能力与权限对应

### 6.1 命名空间与技能

- 查看命名空间详情
  - `namespace.read`
- 创建 / 编辑 / 删除技能
  - `namespace.write`
- 发布版本
  - `namespace.write`
- 查看版本详情 / diff
  - `namespace.read`

### 6.2 Scan

- 查看 Scan 结果
  - `namespace.scan.read`
- 手动触发 Scan
  - `namespace.scan.run`

### 6.3 Clinic

- 查看评测历史与报告
  - `namespace.clinic.read`
- 触发评测
  - `namespace.clinic.run`

### 6.4 生命周期

- 查看配额 / 保留策略
  - `namespace.read`
- 更新配额 / 保留策略 / 触发 GC
  - `namespace.lifecycle.manage`

### 6.5 集成能力

- Webhook 创建 / 修改 / 删除
  - `namespace.integrations.manage`
- Robot 创建 / 禁用 / 删除
  - `namespace.integrations.manage`
- Replication 规则创建 / 修改 / 删除 / 运行
  - `namespace.integrations.manage`

### 6.6 审计

- 读取全局审计日志
  - `audit.read`
- 读取本人/可见命名空间的有限审计信息
  - 由现有命名空间可见性和业务语义控制

### 6.7 IAM 与组织目录

- 查看/创建/编辑组织单元
  - 当前 API 收敛到 `iam.manage`
  - 后续可细化到 `org.read` / `org.manage`
- 查看/编辑角色与绑定
  - `iam.manage`
- 管理 SSO 提供方
  - `iam.manage`
  - 后续可进一步用 `sso.manage`
- 管理 SSO identity links / role mappings
  - `iam.manage`
  - 后续前端可进一步按 `sso.manage` 拆分

## 7. 当前已完成的接管范围

当前已经完成或桥接到 RBAC 的能力：

- 命名空间可见范围
- ClawHub 可见范围
- registry events 可见范围
- Webhook
- Robot
- Lifecycle
- Replication
- Audit 读取桥接
- Scan 读 / 触发
- Clinic 读 / 触发
- IAM 管理控制台
- OIDC / LDAP JIT 登录按 `enterprise_uid` 归并用户
- SSO claim/group 到 `RoleBinding` 的自动授予和撤销
- `/iam` 前端管理页查看 identity links、配置 SSO role mappings

### 7.1 IAM 控制台访问规则

当前 `/iam` 页面已经支持按细粒度权限进入，而不再只限定 `system_role=admin`。

满足以下任一条件即可进入：

- `iam.manage`
- `users.manage`
- `org.read`
- `org.manage`
- `sso.manage`

页面内部会继续按权限分区显示：

- 用户目录区：
  - `users.manage`
  - 或 `iam.manage`
- 组织单元区：
  - `org.read`
  - `org.manage`
  - 或 `iam.manage`
- 角色 / 绑定区：
  - `iam.manage`
- SSO 提供方区：
  - `sso.manage`
  - 或 `iam.manage`

## 8. 仍待继续收口的范围

后续仍建议继续细化：

- `skills` 相关操作从“粗粒度写权限”继续拆成更细动作
- `org.read / org.manage / sso.manage / users.manage` 从文档语义完全落实到 API 依赖
- 前端导航对 `audit.read` 等权限做更细粒度显示
- 真实企业 IdP claim 样本验收和默认映射模板
- SCIM / HRIS 主动目录同步、离职禁用和组织变更策略

## 9. 实施建议

企业部署时建议遵循以下策略：

1. 平台初始管理员使用 `system_role=admin`
2. 完成 `bootstrap` 后，逐步改用 RBAC 角色绑定
3. 对常规业务用户尽量使用 namespace 作用域角色
4. 对审计、SSO、组织目录采用 system / org 作用域角色
5. 逐步减少对 legacy namespace member role 的直接依赖
6. 企业 IdP 接入时优先提供稳定员工号/UID claim，把 DuckDock `enterprise_uid` 当长期主键，避免把 provider subject 当员工主键
