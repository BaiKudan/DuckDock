# 007 Enterprise Identity / SSO JIT

## 背景

企业使用 DuckDock 时，个人助手、WorkBuddy、Hermes 或其它数字人 agent 最终都要挂到企业自己的员工 UID 下。OIDC/LDAP 的 provider subject 只能说明“某个身份源里的账号”，不能作为 DuckDock 长期员工主键。

本轮目标是把 DuckDock 身份模型从“SSO 可登录”推进到“企业 UID 可归并、身份源可追踪、IdP 组可驱动 RBAC”的后端契约。

## 已锁定决策

- DuckDock 内部员工 UID 使用 `users.enterprise_uid`。
- 每个 OIDC/LDAP provider subject 落到 `identity_links`，同一个企业 UID 可跨 provider 归并到同一个 `User`。
- OIDC/LDAP 登录时执行 JIT：
  - 没有用户时自动建号。
  - 已有 `IdentityLink` 时直接回到对应用户。
  - 没有 link 但企业 UID 命中已有用户时，合并到该用户并创建 link。
- IdP claim/group 到 DuckDock RBAC 使用显式 `sso_role_mappings`。
- SSO role mapping 只管理自己创建的 `RoleBinding`，用 `permission_cache.managed_by = "sso_role_mapping"` 标记；管理员手工绑定不被覆盖。
- SCIM/HRIS 主动目录同步、真实企业 IdP 验收另列后续工作。

## 数据模型

- `users.enterprise_uid`
  - DuckDock 内部稳定员工 UID。
  - 可来自 IdP claim，例如 `enterprise_uid` / `employee_id` / `employeeNumber`。
  - 无 claim 时生成 `duid_*`。

- `identity_links`
  - `user_id`
  - `provider_id`
  - `source`
  - `issuer`
  - `external_subject`
  - `external_uid`
  - `username`
  - `email`
  - `full_name`
  - `claims_json`
  - `last_seen_at`

- `sso_role_mappings`
  - `provider_id`
  - `claim_name`
  - `claim_value`
  - `role_id`
  - optional `namespace_id` / `org_unit_id`
  - `enabled`
  - `priority`

## API 契约

- `GET /api/v1/iam/sso/identity-links`
- `GET /api/v1/iam/sso/role-mappings`
- `POST /api/v1/iam/sso/role-mappings`
- `PATCH /api/v1/iam/sso/role-mappings/{mapping_id}`
- `DELETE /api/v1/iam/sso/role-mappings/{mapping_id}`

上述接口当前复用 `SsoManagerUser` 权限依赖。

## 验收

- 同一个 `enterprise_uid` 从 OIDC 与 LDAP 两个 provider 登录，归并到同一个 `User`。
- 两个 provider 各自生成 `IdentityLink`，并记录 `external_uid` / claims 快照。
- IdP `groups` 命中 mapping 时自动创建对应 `RoleBinding`。
- 下一次登录不再命中 mapping 时，撤销由该 mapping 管理的绑定。
- 管理员手工创建的 `RoleBinding` 不被 SSO mapping 删除或覆盖。
- role scope 与 mapping 目标一致：system role 不得带 namespace/org，namespace role 必须带 namespace，org role 必须带 org。

## 后续

- 接一套真实或 staging IdP，记录 claim 样本并完成 OIDC/LDAP E2E。
- 设计 SCIM/HRIS 主动同步：用户禁用、部门变更、manager 变更、离职状态。
- 将 `enterprise_uid` 暴露到员工/agent 详情与交接相关视图。
