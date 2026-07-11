# DuckDock 生产级加强计划

## 目标

围绕 5 条主线把现有 DuckDock 从“可用底座”推进到“可上线治理平台”：

1. 技能生产流程从文件编辑升级为 package 工作流。
2. Sandbox 验证从能跑升级为可信门禁。
3. IAM 从权限框架升级为企业组织治理。
4. 公共区/共享机制补齐审批、到期和许可证语义。
5. Clinic 从评分工具升级为发布门禁的一部分。

## 执行阶段

### Phase 1：治理数据模型与门禁状态机

本次已完成：

- 新增 `namespace_governance_policies` 表。
- `skill_versions` 新增 `review_status / review_required / review_notes / review_requested_at / reviewed_at / reviewed_by / gate_result`。
- `public_skill_releases` 新增 `approval_status / approval_notes / approved_by / approved_at / expires_at / license_name / license_attested / risk_acknowledged`。
- `org_units` 新增 `legal_entity / region / cost_center`。
- `user_affiliations` 新增 `employment_status / joined_at / left_at / cost_center_override`。
- Alembic migration：`20260413_0006_governance_release_gate.py`

### Phase 2：后端治理与发布门禁

本次已完成：

- 命名空间治理 API：
  - `GET /api/v1/namespaces/{name}/governance`
  - `PUT /api/v1/namespaces/{name}/governance`
- 发布时接入治理策略：
  - 人工审核初始状态
  - 公开共享许可证确认
  - 公开共享风险确认
  - 公开共享默认到期
- 扫描任务接入 release gate：
  - 例子文件要求
  - `.duckdock/validation.yaml` 要求
  - sandbox 成功要求
  - Clinic 最低分/有效期门禁
  - 人工审核门禁
- 版本审核 API：
  - `PUT /api/v1/namespaces/{ns}/skills/{skill}/versions/{tag}/review`
- 公开共享审批 API：
  - `PUT /api/v1/namespaces/{ns}/skills/{skill}/versions/{tag}/sharing/approval`

### Phase 3：Package 工作流产品化

本次已完成：

- Package 模板中心 API：
  - `GET /api/v1/skills/package-templates`
- Package 校验 API：
  - `POST /api/v1/skills/package-validate`
- AI 生成支持 `template_key`
- 发布页支持：
  - 选择模板
  - 上传目录
  - 手动 package 校验
  - 公开共享许可证/风险/到期配置
- 命名空间技能面板的 AI 生成入口支持模板选择

### Phase 4：前端治理闭环

本次已完成：

- 命名空间新增“治理”标签页。
- 技能详情页新增：
  - Release gate 摘要
  - 版本审核按钮
  - 公开共享审批按钮
  - 共享状态补充审批/到期/许可证信息

### Phase 5：公共分发治理收口

本次已完成：

- 匿名 registry / ClawHub 查询改为只暴露：
  - `approval_status=approved`
  - 未过期
  - `production`
- 共享待审批版本不会被匿名分发面直接看见。

## 本次未完成

以下内容属于下一阶段，而不是本次提交：

- 更细的 sandbox 网络/文件策略真正落到 runner，而不只是策略字段。
- 行为级 `agent smoke` 断言升级为真正的执行验证。
- 版本回归门禁：
  - “新版本不得低于当前生产版”
  - “高风险维度必须人工复核”
- 公共区法律与合规：
  - 审批责任人
  - 法务确认
  - 分享范围细分
- IAM 组织治理进阶：
  - SCIM
  - JIT Provisioning
  - 入离职自动同步
  - 委派管理员

## 当前建议

下一步优先做下面 3 件事：

1. 把 sandbox runner 的 `network/workspace` 策略从配置字段接成真实执行约束。
2. 把 Clinic 门禁升级为“相对当前 production 的回归比较”。
3. 把 IAM 继续推进到 SCIM / JIT / 委派管理员。
