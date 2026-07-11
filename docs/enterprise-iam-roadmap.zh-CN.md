# 企业 IAM / RBAC / SSO 开发路线图

本文档定义 DuckDock 在企业私有化部署场景下的身份、组织、权限与单点登录演进路线。

## 1. 当前状态

DuckDock 早期只有两层权限：

- 系统级：`system_role = admin | user`
- 命名空间级：`admin | developer | readonly`

这套模型可以支撑个人或小团队协作，但无法满足企业私有化场景。当前主线已经补上企业 IAM 底座，并在 `20260709_0026` 进一步落地企业身份归并：

- 已有组织、部门、团队结构：`OrgUnit` / `UserAffiliation`
- 已有细粒度权限点和角色模板：`Permission` / `Role` / `RolePermission` / `RoleBinding`
- 已有 OIDC / LDAP provider 配置、登录回调、LDAP 认证和凭证加密
- 已有 DuckDock 企业 UID：`users.enterprise_uid`
- 已有跨 IdP 身份链接：`identity_links`
- 已有 IdP claim/group 到 DuckDock RBAC 的 JIT 映射：`sso_role_mappings`

仍未完成的是 **SCIM / 批量目录同步 / 真实企业 IdP 现场验收**。也就是说：登录时 JIT 建号、同员工跨 OIDC/LDAP 合并、组映射到角色绑定和前端管理页已经有契约；企业目录生命周期的主动同步还没有做。

## 2. 目标模型

DuckDock 后续将演进为四层模型：

1. 身份层
   - 本地账号
   - OIDC 登录
   - LDAP 认证
   - DuckDock 企业 UID
   - 多身份源链接 `IdentityLink`

2. 目录层
   - 组织单元 `OrgUnit`
   - 用户归属 `UserAffiliation`
   - 分公司 / 部门 / 团队层级

3. 权限层
   - 权限点 `Permission`
   - 角色模板 `Role`
   - 角色权限绑定 `RolePermission`
   - 用户角色绑定 `RoleBinding`

4. 资源层
   - namespace / skill / webhook / robot / audit / replication 等业务资源
   - 最终统一由 RBAC 权限计算接管

## 3. 分阶段路线

### Phase 1：IAM 基础设施

目标：先把企业 IAM 的底座建出来，不破坏现有业务权限。

本阶段交付：

- 用户扩展字段：
  - `full_name`
  - `auth_source`
  - `external_subject`
  - `last_login_at`
- 组织模型：
  - `OrgUnit`
  - `UserAffiliation`
- RBAC 模型：
  - `Permission`
  - `Role`
  - `RolePermission`
  - `RoleBinding`
- SSO 配置骨架：
  - `SSOProviderConfig`
- 内置权限点与系统角色模板
- 管理 API：
  - 组织树管理
  - 用户归属管理
  - 角色 / 权限管理
  - 角色绑定管理
  - SSO Provider 配置管理

说明：

- 这一阶段保留现有 `system_role` 和 `namespace member role` 作为兼容回退。
- 新 RBAC 会先用于管理面，不会一次性替换掉全部业务鉴权。

### Phase 2：业务权限接管

目标：让业务接口逐步切到统一 RBAC。

本阶段计划：

- 把 namespace 读写 / 管理权限统一映射到权限点
- 将 webhook / robot / replication / lifecycle / clinic / audit 逐步改为权限点校验
- 允许通过角色绑定向用户授予跨 namespace 能力
- 保留旧权限模型作为迁移期 fallback

### Phase 3：SSO 登录与目录同步

目标：真正接入企业身份源。

已完成：

- OIDC 登录回调流程
- LDAP 用户认证
- 外部 subject 与本地用户绑定
- 首次登录自动建号
- `enterprise_uid` 归并同一员工在不同 provider 下的身份
- `identity_links` 记录 provider / subject / external_uid / claims 快照
- `sso_role_mappings` 将 IdP claim/group JIT 映射成 DuckDock `RoleBinding`

仍待完成：

- SCIM / HRIS 主动目录同步
- 组织 / 部门 / manager 的自动归属策略
- 真实企业 IdP 现场 E2E 验收

### Phase 4：治理增强

目标：让 IAM 成为真正的企业控制平面。

本阶段计划：

- 审批流与敏感操作二次确认
- SCIM / 批量导入导出
- 更细粒度的资源级审计
- 会话策略、禁用策略、失效策略

## 4. 已执行内容

已完成 Phase 1 到 Phase 3 的后端主契约：

- 新增组织单元、用户归属、权限、角色、角色绑定、SSO Provider 配置模型
- 新增 Alembic migration：`20260409_0004_enterprise_iam_foundation`
- 新增 IAM 管理 API：
  - `/api/v1/iam/bootstrap`
  - `/api/v1/iam/users`
  - `/api/v1/iam/users/{id}/permissions`
  - `/api/v1/iam/org-units`
  - `/api/v1/iam/org-units/tree`
  - `/api/v1/iam/users/{id}/affiliations`
  - `/api/v1/iam/permissions`
  - `/api/v1/iam/roles`
  - `/api/v1/iam/bindings`
  - `/api/v1/iam/sso/providers`
- 新增 IAM 权限计算服务，并保留旧 namespace role 兼容映射
- 新增 Alembic migration：`20260709_0026_enterprise_identity_links`
- 新增企业 UID 字段：`users.enterprise_uid`
- 新增身份链接模型：`IdentityLink`
- 新增 SSO 组/claim 到角色绑定模型：`SSORoleMapping`
- SSO JIT 登录时按 `enterprise_uid` 归并用户，并刷新 identity link
- SSO JIT 登录时按 provider claim 自动创建 / 刷新 / 撤销由 SSO 管理的 `RoleBinding`
- 前端 `/iam` 页面可查看 SSO providers / identity links，并创建、编辑、启停、删除 SSO role mappings
- 新增 IAM 管理 API：
  - `/api/v1/iam/sso/identity-links`
  - `/api/v1/iam/sso/role-mappings`

## 5. 下一步执行建议

优先顺序：

1. 用真实企业 IdP 跑一次 OIDC / LDAP JIT 登录 E2E，记录 claim mapping 样本
2. 设计 SCIM / HRIS 主动同步：用户禁用、部门变更、manager 变更、离职状态
3. 把 `enterprise_uid` 显示到员工/agent 详情、交接、日报周报等管理视图
4. 根据企业策略决定是否允许用户自助绑定身份源，或必须由 IAM 同步/管理员预授权

## 6. 设计原则

- 先兼容，后替换
- 先管理面，后业务面
- DuckDock 内部使用 `enterprise_uid` 做稳定员工 UID，不把单个 IdP 的 subject 当长期主键
- JIT 登录只管理带 `managed_by=sso_role_mapping` 标记的授权，不覆盖管理员手工绑定
- 先支持 OIDC/LDAP JIT，后补 SCIM/HRIS 主动同步
- 先企业可运维，后追求协议完整
