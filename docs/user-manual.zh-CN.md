# DuckDock 使用手册（中文）

> **历史手册**：本文保留早期 Skills 注册中心操作说明。当前 Agent 控制平面、Reporter 接入、员工/管理员日常流程请优先阅读 [`duckdock-handbook.zh-CN.md`](./duckdock-handbook.zh-CN.md)。文档索引见 [`documentation-index.zh-CN.md`](./documentation-index.zh-CN.md)。

本文档面向日常使用 DuckDock 的管理员、开发者和自动化使用者，重点说明系统如何使用，而不是如何开发。

## 1. 产品定位

DuckDock 是面向 AI Skill / Prompt 资产的企业私有注册中心与控制台。

它解决的核心问题包括：

- 用 Git 管理 skill 的版本历史
- 用平台统一发布、扫描、审计和治理
- 用 MinIO 分发可同步的成品包
- 用共享开关控制哪些版本可以被外部系统匿名获取

## 2. 角色说明

系统内常见角色分为三层：

- 系统用户
  - `admin`
  - `user`
- 命名空间成员
  - `admin`
  - `developer`
  - `readonly`
- Robot 账号
  - 面向 CI/CD 或无人值守流程

权限理解建议：

- 系统角色决定平台级能力
- 命名空间角色决定某个命名空间内能做什么
- Robot 账号只在其所属命名空间内活动

## 3. 日常使用流程

### 3.1 注册与登录

1. 打开前端页面
2. 注册账号
3. 使用账号密码登录

注意：

- 当前系统没有预置默认管理员账号
- 首次使用请自行注册

### 3.2 创建命名空间

1. 进入 `Namespaces`
2. 点击 `New Namespace`
3. 填写命名空间名称和描述

建议：

- 一个团队或一个业务域使用一个命名空间
- 不同环境可以拆成不同命名空间，例如 `team-a-dev`、`team-a-prod`

### 3.3 创建 Skill

1. 打开某个命名空间详情页
2. 在 `Skills` 页签中创建 skill
3. 系统会为该 skill 建立 Git 仓库

一个 skill 对应一个受管仓库，后续所有版本都在这个 skill 下发布。

### 3.4 发布版本

1. 进入某个 skill 详情页
2. 点击 `Publish Version`
3. 填写语义化版本号，例如 `v1.0.0`
4. 编辑 `SKILL.md`
5. 如有需要可填写 `system_prompt.md`
6. 提交发布

发布后系统会：

- 把内容写入 Git
- 创建版本记录
- 触发扫描
- 生成 MinIO 成品包

### 3.5 共享到公共区域

DuckDock 现在支持“版本级共享”，不是“整个 skill 永久公开”。

你可以在两个位置控制共享：

- 发布版本时直接勾选共享
- 在版本详情页里后续开启或关闭共享

共享规则：

- 只有显式开启共享的版本，才会进入公共区域
- 只有共享且扫描通过进入 `production` 的版本，外部系统才能匿名获取
- 关闭共享后，该版本会从公共同步面撤下

### 3.6 查看版本详情

在 skill 详情页，你可以查看：

- 版本列表
- 文件内容
- 扫描结果
- 版本差异
- 该版本是否处于公共共享状态

### 3.7 比较版本差异

1. 打开 skill 详情页
2. 切换到 `Diff`
3. 选择起始版本和目标版本
4. 查看差异内容

适用于：

- 回顾变更
- 发布前复核
- 排查问题版本

### 3.8 安全扫描

1. 打开 skill 详情页
2. 进入 `Security`
3. 查看当前扫描结果
4. 必要时手动重新触发扫描

扫描结果会决定版本是否能进入 `production`。

共享版本也必须在扫描通过后，才会真正对外可同步。

### 3.9 Clinic 评测

1. 进入 `Clinic`
2. 选择命名空间
3. 触发评测
4. 打开某次完成的报告

评测结果可用于：

- 发现 skill 质量问题
- 查看多维评分
- 获取修复建议

## 4. 企业功能使用说明

### 4.1 审计日志

进入 `Audit` 页面后可以：

- 按命名空间筛选
- 按动作筛选
- 查看谁在什么时间做了什么操作

常见审计事件包括：

- skill 创建
- 版本发布
- 共享状态变更
- webhook 投递
- 复制执行

### 4.2 Webhook

进入命名空间详情页中的 `Webhooks` 页签后可以：

- 配置 webhook 地址
- 订阅事件
- 查看投递历史

当前建议订阅的重要事件：

- `skill.published`
- `skill.deleted`
- `skill.version.deleted`
- `skill.version.public_shared.updated`
- `scan.completed`
- `scan.failed`
- `registry.sync.changed`

如果你的外部客户端需要加速同步，推荐订阅：

- `registry.sync.changed`

使用方式：

- 收到 webhook 后，不直接信任 webhook 内容做最终状态判断
- 仅把 webhook 当作“立即去拉 `/registry/events`”的触发器

### 4.3 Robot 账号

Robot 账号主要用于：

- CI/CD 发布
- 自动同步
- 无人值守读取 registry

使用流程：

1. 在命名空间的 `Robots` 页签中创建 robot
2. 立即保存返回的 token
3. 在自动化系统中使用该 token

注意：

- token 只会显示一次
- 不再使用的 robot 应及时禁用或删除

### 4.4 配额与保留策略

在命名空间详情页中可以配置：

- 最大 skill 数
- 每个 skill 的最大版本数
- 总版本数
- 总存储上限
- 保留最近 N 个版本
- 保留最近 N 天内版本
- 是否在 GC 时删除被拒绝版本

适用于：

- 控制存储成本
- 控制版本膨胀
- 清理历史垃圾版本

### 4.5 复制策略

复制规则用于把一个命名空间中的 skill 镜像到另一个命名空间。

可用于：

- 公共仓到私有仓
- 一个业务线到另一个业务线
- 按发布节奏自动同步

可配置项通常包括：

- 目标命名空间
- 过滤模式
- 手动触发或发布时触发

## 5. 公共同步与外部消费

### 5.1 什么是公共区域

公共区域不是一个单独的仓库，而是系统根据共享状态动态暴露出来的一组版本。

某个版本进入公共同步面的条件：

1. 该版本被显式共享
2. 该版本状态为 `production`
3. 该版本未被删除或撤回

### 5.2 外部系统如何获取

外部系统可以通过匿名 registry 接口获取共享版本：

- `GET /api/v1/registry/index`
- `GET /api/v1/registry/events`
- `GET /api/v1/registry/namespaces/{namespace}/skills/{skill}/versions/{tag}/manifest`
- `GET /api/v1/registry/namespaces/{namespace}/skills/{skill}/versions/{tag}/download-link`

推荐同步方式：

1. 冷启动时先拉 `index`
2. 记录本地 `cursor`
3. 后续持续拉 `events?since_cursor=...`
4. 收到 `tombstone` 时删除本地版本

### 5.3 公共页面

DuckDock 前端里已经有 `/public` 页面。

这个页面用于：

- 查看当前对外公开的共享版本
- 复制 public slug
- 获取 manifest / download URL
- 验证某个版本是否真的对外可见

## 6. 推荐操作规范

### 6.1 发布规范

建议：

- 使用标准语义化版本号
- 每次发布前先检查 `SKILL.md` 的 front-matter
- 共享前确认该版本确实适合对外分发

### 6.2 共享规范

建议不要把“开发中版本”直接共享。

推荐流程：

1. 在命名空间内部先完成开发
2. 扫描通过后再决定是否共享
3. 共享只针对明确可分发的版本开启

### 6.3 删除规范

删除共享版本前要意识到：

- 外部客户端可能已经同步过该版本
- 删除后会产生 tombstone
- 客户端收到 tombstone 后应删除或停用该版本

## 7. 常见问题

### 7.1 为什么我共享了版本，但外部系统还是拉不到？

常见原因：

- 版本还在 `quarantine` 或 `scanning`
- 扫描未通过，没有进入 `production`
- 共享后来又被关闭

### 7.2 为什么匿名 `index` 看不到某个版本？

因为匿名 `index` 只返回公开且 `live` 的版本。

也就是说，以下版本不会出现在匿名视角：

- 未共享版本
- 扫描未完成版本
- 被撤回版本
- 已删除版本

### 7.3 webhook 能不能代替事件流？

不能。

建议把 webhook 当作加速器，不要当作最终状态源。

最终同步应始终以：

- `index`
- `events`

为准。

### 7.4 token 下载地址为什么会过期？

因为 MinIO 下载使用的是短期签名 URL。

这能降低链接泄漏风险。外部客户端应在 URL 过期后重新请求新的下载链接。

## 8. 文档索引

相关文档：

- 同步协议：`docs/registry-sync-contract.md`
- OpenClaw 对接合同：`docs/openclaw-registry-contract.md`
- 项目总览与部署：`README.md`
