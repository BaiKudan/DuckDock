# Feature Specification: 多租户/命名空间隔离

**Feature Branch**: `003-tenant-isolation`

**Created**: 2026-06-08

**Status**: Implementing — 2026-06-12 决策落定（方案 A）并实现 FR-002 后端权限门：8 个权限门 + 38 端点接线 + 接线锁测试。
2026-06-16 补齐 FR-002 敏感内容 **ownership 过滤**：`list_work_traces` / `get_work_trace`(by-id) / `list_evidence` 按归属排除他人敏感行（actor_user_id / created_by；持 worktrace.content.read / evidence.sensitive.read / 系统 admin 不受限），6 例测试（`test_sensitive_filtering.py`）。前端 403 适配已落地（`client.ts` 拦截器）。剩余：org_unit 维度细分（单租户下价值低，暂缓）。

**Input**: PRD 要求"所有查询强制 tenant_id"（FR-014），但当前 `backend/app/models` 中 **0 处 tenant_id**，
控制平面实体的隔离边界未定义；现实隔离仅靠 Namespace/RBAC。需统一隔离键并在数据层强制。

## Clarifications

### Session 2026-06-12

- Q: 隔离边界用 Namespace、独立 tenant，还是激活权限键？ → A: **方案 A——激活权限键**。不加 tenant 列；把 IAM 中已定义但闲置（0 引用）的系统权限键（`runtime.read/manage` · `asset.read/manage` · `handover.read/manage` · `worktrace.read` · `worktrace.content.read` · `evidence.read` · `evidence.sensitive.read`）接入控制平面全部 23 个 CurrentUser 端点，敏感读取按 ownership/org 过滤；部署模型显式声明为单租户（一企业一实例）。SaaS 多租户（PRD P2）时再引入 tenant 维度，本 spec 届时升版。
  **决策依据**：43 端点现仅"登录用户/系统 admin"两档；权限键已建模未启用——激活成本最低（零迁移）、即刻消除"任何登录用户可见全企业资产"的现实风险。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 跨边界不可见 (Priority: P1)

用户只能看到自己所属隔离边界（Namespace 或 tenant）内的资产、运行时、工作历程、证据。

**Why this priority**: 越权读取企业 AI 资产是最严重的安全事故面。

**Independent Test**: 构造两个边界 A/B，A 用户查询资产列表/详情，绝不返回 B 的任何记录。

**Acceptance Scenarios**:
1. **Given** 资产分属边界 A、B, **When** A 用户列资产, **Then** 只见 A 的。
2. **Given** B 的资产 id, **When** A 用户直接按 id 访问, **Then** 返回 403/404 而非内容。
3. **Given** 越权尝试, **When** 发生, **Then** 进审计日志。

### Edge Cases
- 共享/平台级资源（如公共 Skill 分发）如何归属边界？
- 跨边界 replication / handover 的合法跨界路径如何与隔离规则共存？

## Requirements *(mandatory)*

### Functional Requirements
- **FR-001**: 隔离模型 = **单租户部署 + 系统权限键强制**（2026-06-12 clarify）：不引入 tenant 列；部署文档 MUST 显式声明"一企业一实例"。
- **FR-002**: 控制平面全部读写端点 MUST 经对应系统权限键校验（`runtime.* / asset.* / handover.* / worktrace.* / evidence.*`），替代当前"任意登录用户可读"两档模型；敏感读取（worktrace 全文、敏感证据）MUST 叠加 ownership/org 过滤。
- **FR-003**: 跨边界访问 MUST 返回 403/404，且 MUST 记审计。
- **FR-004**: 合法跨界操作（replication、跨组织交接）MUST 有显式授权路径，不得绕过 FR-002。
- **FR-005**: 本方案**零数据迁移**（不加列）；若未来升级 tenant 维度（SaaS P2），届时 MUST 另立回填迁移规格。

### Key Entities
- 受影响：`runtime_instance` · `ai_asset` · `asset_ownership` · `runtime_binding` · `work_trace` · `work_artifact` · `evidence_item` · `handover_case` 等，新增隔离键列。

## Success Criteria *(mandatory)*

- **SC-001**: 越权测试矩阵中，跨边界数据泄露用例 = 0 通过（即全部被正确拒绝）。
- **SC-002**: 所有控制平面列表/详情接口在测试中验证隔离过滤。
- **SC-003**: 隔离键回填迁移可在空库与存量库上幂等执行。

## Assumptions
- 隔离模型已由 2026-06-12 `/speckit-clarify` 决策（见 Clarifications）；实施载体 = 既有 IAM 权限键 + RoleBinding。
- 隔离强制优先在 service/repository 层统一封装，避免逐查询遗漏。
