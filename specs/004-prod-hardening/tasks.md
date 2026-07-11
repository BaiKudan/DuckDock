---
description: "生产硬化执行清单——2026-06-17 review remediation"
---

# Tasks: 生产硬化(P0 → P1 → P2 → 决策)

**Input**: specs/004-prod-hardening/spec.md + 2026-06-17 review(findings 编号已内嵌)。
**目标读者**: 维护者与自动化执行工具。每个任务自含文件/根因/改法/测试/验收,可独立完成。

---

## ⚙️ 执行协议(每个任务都遵守)

1. **一次一个任务**,按 P0 → P1 → P2 顺序;同一 Phase 内可按列出顺序做。
2. **改行为必有测试**(宪法原则 III):新增/修改回归测试,先让它复现 bug(红),再改实现(绿)。
3. **每个任务完成后跑完整门禁(下方命令),必须全绿**才能提交;**不得削弱任何门禁**(ruff 规则集、`.mypy-baseline=95`、核心覆盖率 65%、eslint、build)。
4. **一个任务一个提交**,信息首行引用 finding id,例:`fix(auth): normalize naive datetime on MySQL (CORR-01)`。结尾加
   `Co-Authored-By:` 行(执行体身份)。
5. **涉及迁移**:必须空 MySQL 能 `alembic upgrade head`、幂等(列/索引存在性守卫)、含 `downgrade`;新增列在**模型**上也要写 `server_default` 与迁移一致(见 DM-02)。
6. **遇到标 `⚠DECISION` 的任务先停下,向用户确认**,不要擅自实现或猜测产品方向。
7. **不改 specs 决策**(Push-only / 双模式 manual 默认 / Fernet / 权限键隔离);如发现冲突,停下报告。
8. 不引入新依赖除非任务明确要求;新依赖加进 `requirements.txt`/`requirements-dev.txt`/`package.json` 并说明。

### 门禁验证命令(每任务后执行)

```bash
# 后端(在 backend/;CI 同款 env:SECRET_KEY/DATABASE_URL/MINIO_*/COMPONENT_MANAGER_ENABLED=false)
python -m ruff check app alembic tests
python -m mypy app          # 错误数不得 > .mypy-baseline(95)
python -m pytest -q
python -m pytest --cov=app.core.deps --cov=app.services.iam_service \
  --cov=app.services.release_gate_service --cov=app.services.credential_service --cov-fail-under=65
python -c "from app.main import app; print(app.title)"
# 迁移类任务额外:对空库 alembic upgrade head（CI 用真实 MySQL；本地可用 docker compose 的 mysql:3307）
# 前端(在 frontend/)
npm run lint && npm test && npm run build
```

---

## Phase P0 — 崩溃止血 + 安全网(上线绝对阻断)

### [CORR-01] CRITICAL · robot token 过期比较在 MySQL 必崩
- **文件**: `backend/app/core/deps.py`(`_auth_robot`,`robot.expires_at < datetime.now(timezone.utc)` 一行,约 L88)
- **根因**: `expires_at` 列 `DateTime(timezone=True)`,MySQL/asyncmy 读回 **naive**;与 aware `now` 比较 → `TypeError`。SQLite 读回 aware 故测试不暴露。
- **改法**: 比较前归一化:`exp = robot.expires_at; if exp and exp.tzinfo is None: exp = exp.replace(tzinfo=timezone.utc)`,再比较。**复用**仓库既有写法 `registry._as_utc`(`backend/app/api/v1/endpoints/registry.py:164`)或抽到 `app/core/` 的公共 `ensure_utc(dt)` helper 统一用。
- **测试**: `backend/tests/` 加用例:构造 `RobotAccount.expires_at = datetime(...)`(**naive**,模拟 MySQL 读回)走 `_auth_robot`/鉴权依赖,断言不抛 `TypeError` 且过期判定正确(未过期放行、过期 401)。
- **验收**: naive expires_at 鉴权不崩;门禁全绿。

### [CORR-02] CRITICAL · 公开 release 过期比较在 MySQL 必崩(同根因)
- **文件**: `backend/app/services/public_release_service.py`(`is_public_release_expired`,约 L111);消费方 `registry.py:494/575`、`skills.py:225/1096`。
- **根因**: 同 CORR-01。治理默认 `public_share_default_expiry_days=30` → 过期是常态,公开下载/列版本 500。
- **改法**: 在 `is_public_release_expired` 内归一化 `expires_at`(同 CORR-01 的 `ensure_utc`)。
- **测试**: 构造带 **naive** `expires_at`(过期/未过期各一)的 `PublicSkillRelease`,断言 `is_public_release_expired` 返回正确且不抛;补一条公开下载路径在 naive 过期下返回 410/403 而非 500。
- **验收**: naive expires_at 下公开链路不崩;门禁全绿。

### [CORR-TZ-AUDIT] CRITICAL(同类排查)· 全仓 tz-aware 比较审计
- **文件**: 全 `backend/app`。
- **改法**: `grep -rn "datetime.now(timezone.utc)" backend/app` 与所有读自 `DateTime(timezone=True)` 列后参与比较的点(lease_expires_at、token expires、release expires…),逐一确认都经 `ensure_utc` 归一化或在 SQL 侧比较(如 iam_service 的 `expires_at > now` 在 DB 侧,安全)。把仍在 Python 侧裸比较 naive 列的点全部修掉。
- **验收**: 无 Python 侧 naive-vs-aware 比较残留;每个修复点有对应 naive 输入测试。

### [TEST-MYSQL-LANE] CRITICAL(安全网,FR-002)· 增 MySQL 实测测试道
- **文件**: `backend/tests/conftest.py`、`backend/pyproject.toml`、`.github/workflows/ci.yml`。
- **根因**: 全部单测走 SQLite 内存库,tz/方言相关 bug(CORR-01/02 即是)被结构性掩盖。
- **改法**: 加一个 `@pytest.mark.mysql` 标记 + 一个可选 `async_session_mysql` 夹具(当 `TEST_MYSQL_URL` 环境变量存在时连真实 MySQL,否则 `skip`)。把 CORR-01/02 的回归用例同时挂到 MySQL 道。CI backend job 增加一步:用已有 MySQL service 设 `TEST_MYSQL_URL` 跑 `pytest -m mysql`。
- **验收**: CI 上 `pytest -m mysql` 实跑且覆盖两个过期比较路径;本地无 MySQL 时该道自动 skip 不阻断。

---

## Phase P1 — 上线前必修(全部 HIGH)

### [HANDOVER-01] HIGH · 零执行项导致 case 永久卡死 EXECUTING
- **文件**: `backend/app/api/v1/endpoints/control_plane.py`(`execute_handover`,约 L1799-1833)
- **改法**: 查得 `items` 后,若 `not items`:`raise HTTPException(422, "无可执行的交接项")` **且不改 case.status**(保持 APPROVED)。同时在 `submit_handover` 前置:零 `HandoverItem` 的 case 不允许进入审批(或 analyze 必须产出 ≥1 item)。`selected_item_ids` 传入但全部不命中本 case 时同样按空处理 → 422,并校验这些 id 属于本 case。
- **测试**: ①零 item 的 case execute → 422 且 status 仍 APPROVED;②传他 case 的 item_id → 422;③正常路径不受影响。
- **验收**: 不存在进入 EXECUTING 却 0 action 的路径。

### [HANDOVER-03] HIGH · REJECTED 可被翻回 APPROVED(拒绝非终态)
- **文件**: `control_plane.py`(`decide_approval`,约 L1748-1796)
- **改法**: 开头加守卫 `if case.status != HandoverStatus.PENDING_APPROVAL: raise 409`;且 `if task.status != ApprovalStatus.PENDING: raise 409`(已决任务不可重决)。REJECTED 设为终态,翻案须显式 reopen(本期不做 reopen,仅锁死回退)。
- **测试**: ①已 REJECTED 的 case 再 decide → 409;②已 decided 的 task 再 decide → 409;③全票通过仍正常 → APPROVED。
- **验收**: 拒绝不可被静默反转。

### [HANDOVER-02 / FR-005-EXEC-IDEMPOTENCY] HIGH · execute 幂等键不校验
- **文件**: `control_plane.py`(`execute_handover`,约 L1804-1824);`backend/app/models/control_plane.py`(`ExecutionAction`)+ 新迁移。
- **改法**: 进入创建前按 `(handover_case_id, idempotency_key)` 查已存在 ExecutionAction:命中则直接返回原 actions(真幂等);给 `execution_actions` 加 `(handover_case_id, idempotency_key)` 唯一约束(新迁移,幂等守卫 + downgrade)。`_get_handover` 在 execute 路径用 `with_for_update()` 防并发双读 APPROVED。
- **测试**: 同 case+key 重复 execute 返回同一组 action 且不新增;并发模拟(同 key)只产一组。
- **验收**: 重复/并发 execute 不产生重复处置动作。

### [ISO-001] HIGH · 角色权限变更对存量 RoleBinding 不生效(撤权失效)
- **文件**: `backend/app/services/iam_service.py`(`effective_permissions` L268 的 `permission_cache` 短路 / `_role_keys_from_binding`)、`backend/app/api/v1/endpoints/iam.py`(`update_role_permissions` L391-419、`create_binding` L464)
- **改法**(二选一,推荐 A):
  - **A(稳妥)**: 让 `effective_permissions` 不再信任 `permission_cache` 做鉴权——命中绑定后按 `binding.role` 当前 `RolePermission` 计算活权限;`permission_cache` 降级为纯展示快照(或删除其参与鉴权的分支)。
  - **B**: `update_role_permissions` 改动后,批量重算该 `role_id` 下所有 `RoleBinding.permission_cache`。
- **测试**: 给用户绑定含 `evidence.sensitive.read` 的自定义角色 → 校验有权;从角色撤该权限 → 校验**立即**无权(当前会残留)。
- **验收**: 撤权/授权对存量绑定即时生效。

### [SEC-01] HIGH · analysis 读端点裸 CurrentUser 绕过权限门
- **文件**: `backend/app/api/v1/endpoints/analysis.py`(约 L122 list jobs、L241 artifacts、L257 download-link、L277 memory candidates)
- **改法**: 这些读端点改用 system 权限门(复用 `require_asset_reader`,或在 iam 新增 `analysis.read` 权限键并接入 + 加进 `BUILTIN_PERMISSIONS`/enterprise-admin);**download-link** 端点签发前校验调用者对该 job 关联 runtime/资产的读权限,并**拒绝 robot token**(参照 `webhooks.py:34` 的 `get_robot_account` 拒绝模式),且补审计留痕。
- **测试**: 普通用户/robot 调四个端点 → 403;有 `asset.read`/`analysis.read` 者 → 200;download-link 越权 → 403 且留审计。
- **验收**: analysis 读路径不再绕过资产/证据权限模型。

### [PUSH-01] HIGH · 分析 job 租约回收死区(无 reaper)
- **文件**: `backend/app/services/analysis_service.py`(`lease_next_analysis_job` L136-168);`backend/app/workers/celery_app.py`(beat);新 worker 任务。
- **改法**: ①租约**过期回收不消耗 attempts**(或新增独立 `lease_attempts` 计数,业务重试与租约续期分离);②新增 Celery beat 定时 reaper:周期扫 `status in (LEASED,RUNNING) AND lease_expires_at < now`,超 `max_attempts` 的置 FAILED + 释放 session(复用 `_mark_analysis_job_failed`/`_mark_session_failed`),未超的释放回 PENDING。
- **测试**: 模拟租约过期 + attempts 耗尽 → reaper 将其置 FAILED 且 session 不再卡 INGESTING;模拟过期未耗尽 → 回 PENDING 可被重租。
- **验收**: 不存在 worker 崩溃后永久卡死的 job。

### [CORR-03] HIGH · ingest 失败状态被回滚,session 永卡 INGESTING
- **文件**: `backend/app/services/report_upload_service.py`(ingest except,约 L294-312);`backend/app/workers/report_upload_tasks.py`(L23-27)
- **改法**: 在 service 的 except 内 **re-raise 前 `await db.commit()`**(把 FAILED + AdapterError 落库),与 `clinic_tasks.py:183`/`scan_tasks.py` 的写法一致;或在 task 用 try/finally 保证失败路径提交。
- **测试**: 让 ingest 抛异常 → 断言 session.status 持久化为 FAILED 且有 AdapterError(当前会被回滚丢失)。
- **验收**: 摄取失败可见、可查、可重试。

### [CORR-04] HIGH · Celery 任务每次建 engine 不 dispose,连接池泄漏
- **文件**: `backend/app/workers/scan_tasks.py`(L39-41)、`clinic_tasks.py`(L28-30)、`report_upload_tasks.py`(L10-12)
- **改法**: 用 try/finally 包住 `async with AsyncSession()`,finally 里 `await engine.dispose()`(对照 `lifecycle_tasks.py:90`/`webhook_tasks.py:77` 已正确);或抽模块级单例 engine 复用。**顺带修 [CORR-05]**:`webhook_tasks.py` 重试路径(L55 `raise task.retry` 后 L77 dispose 不可达)同样用 try/finally。
- **测试**: 单元验证任务执行后 engine 被 dispose(可 mock dispose 断言调用),或集成跑 N 次任务后连接数不增长。
- **验收**: 重投/高频下无连接泄漏。

### [OPS-01] HIGH · 生产 MinIO 应用密钥与 root 脱钩,backend 连不上
- **文件**: `docker-compose.prod.yml`(minio 段 L55-66)、`.env.prod.example`、`scripts/prod.sh`(预检)
- **改法**(二选一):①加 minio init 容器(类比 `docker-compose.yml:230` 的 langfuse-minio-init)用 `mc` 建专用 service account 并绑定到 `MINIO_ACCESS_KEY/SECRET`;②在 `.env.prod.example` 明确注释「`MINIO_ACCESS_KEY` 必须==`MINIO_ROOT_USER`、`MINIO_SECRET_KEY` 必须==`MINIO_ROOT_PASSWORD`」,并在 `prod.sh` 预检加一致性断言(不一致即拒启)。
- **验收**: 按模板填强口令后 backend 能连上 MinIO(签发 PUT/建桶成功)。

### [OPS-03] HIGH · 生产无 backend/frontend 探活
- **文件**: `docker-compose.prod.yml`(backend/worker/frontend 段)
- **改法**: backend 加 `healthcheck`(`curl -f http://localhost:8801/health`,镜像需含 curl 或用 python one-liner);frontend `depends_on: backend` 改 `condition: service_healthy`;frontend 加 nginx 探活。
- **验收**: nginx 不会在 backend 未就绪时接流量;假死可被重启。

### [OPS-02] HIGH · 生产 CORS_ORIGINS 缺失 + 防误配
- **文件**: `backend/app/core/config.py`(`CORS_ORIGINS` L55)、`.env.prod.example`、`scripts/prod.sh`
- **改法**: `.env.prod.example` 增 `CORS_ORIGINS=["https://your-domain.example.com"]` 必填项;config 加校验器**拒绝 `CORS_ORIGINS` 含 `*`(当 `allow_credentials=True`)**;`prod.sh` 预检它非默认 localhost。
- **测试**: config 校验器对 `["*"]` 抛错的单测。
- **验收**: 生产 CORS 可配且不可误设为通配+credentials。

### [DM-01-LITE] HIGH(降风险版)· 迁移漂移检测闸(替代大重构)
- **文件**: `.github/workflows/ci.yml`、`backend/alembic/`
- **说明**: DM-01(0001 用 `create_all` 致迁移非真相来源)的**彻底修**是把 baseline 固化为静态 DDL——风险大,放 P2([DM-01-FULL])。**本期先加检测闸**:CI 增一步 `alembic upgrade head` 后跑 `alembic check`(env.py 已开 `compare_type`/`compare_server_default`),若模型与迁移产物有 drift 即失败。同时完成 **[DM-02]**:给模型 `ExecutionAction.execution_mode` 补 `server_default="MANUAL"` 与迁移 0016 对齐,消除已知 drift。
- **验收**: `alembic check` 进 CI 且通过(先修 DM-02 让它绿)。

### [FR-019-AUTO-GUARD] HIGH · auto 模式无执行器却静默卡死
- **文件**: `control_plane.py`(`execute_handover`)
- **改法**(最小,符合既定决策「auto=T085 待探针」): 当 `body.execution_mode == ExecutionMode.AUTO` 时 `raise HTTPException(501, "auto 执行(探针 lease)尚未实现,见 specs/001 T085;请用 manual")`,不创建悬空 PENDING 动作。
- **测试**: execute with AUTO → 501;manual 不受影响。
- **验收**: 不存在选 auto 后永久卡 EXECUTING 的路径。

---

## Phase P2 — 应修(MEDIUM,可上线后迭代)

> 每条仍遵守执行协议(测试 + 门禁 + 单提交)。

- **[HANDOVER-04]** 前段状态机守卫:为 `collect/analyze/submit` 加来源态白名单,抽 `_assert_transition(case, allowed_from)`,非法来源 409。(`control_plane.py:1599-1745`)
- **[HANDOVER-05]** 全 action FAILED 仍可 verify→COMPLETED:`verify_handover` 置 COMPLETED 前检测存在 FAILED action/item → 要求 `body.acknowledge_failures=True` 或返回失败清单。(`control_plane.py:1913-1935`)
- **[HANDOVER-06]** `complete_execution_action` 加 `case.status==EXECUTING` 守卫(纵深防御)。
- **[ISO-002]** `create_binding` 校验 `role.scope` 与 `namespace_id/org_unit_id` 一致(NAMESPACE 角色必带 namespace_id…),否则 422。(`iam.py:435-465`)
- **[FR-005-PARTIAL-FAILED]** 让 `JobStatus.PARTIAL_FAILED` 可达:materialize/finalize 累计单产物失败,部分失败置 `PARTIAL_FAILED` 并在 `summary_json` 记明细;`_load_json_items`/`_load_text` 的吞错改为可区分「读失败」与「无数据」。(`analysis_service.py:286`、`analysis_materializer.py:390-418`)
- **[FR-009-EVIDENCE-LINK]** 自动建议回链证据:`analyze_handover` 为生成的 `HandoverItem` 填 `evidence_id`(关联物化阶段证据),或为每条建议落一条 `source_type=DERIVED` 的 EvidenceItem。(`control_plane.py:1661-1720`)
- **[PUSH-02]** finalize 内联 `memory_candidates` 去重(复用 `_ensure_memory_candidate` 的 `(job,type,subject_key,title)` 查重)。(`analysis_service.py:304-322`)
- **[PUSH-03]** 重 I/O 卸载:`artifact_service` 的 `head_object/hash_object/read_object_bytes` 经 `asyncio.to_thread` 卸载,避免阻塞事件循环;或把 finalize 重 I/O 段迁 Celery。
- **[PUSH-05]** 缺 `external_session_id` 的 work_trace 改用内容去重键(`runtime_id+title+started_at+payload hash`),与 raw_records 对齐。(`adapter_collection_service.py:128-167`)
- **[PUSH-06]** ingest 解析前用读到字节重算 sha256 与 `session.actual_sha256` 断言一致(TOCTOU 收口);或 finalize 后撤/缩短 PUT URL。
- **[PUSH-04]** 租约回收时 `result_object_key` 随 attempt 变化,避免新旧 worker 写同一对象竞态。
- **[SEC-02]** 应用层启动防呆:`DEBUG=False` 时 `SECRET_KEY` 为默认/空 或 `DUCKDOCK_CREDENTIAL_KEY` 空 → fail-fast;非 DEBUG 关闭 `/docs` `/redoc`。(`config.py`/`main.py` lifespan)
- **[SEC-03]** 对 `/auth/*` 与 token 鉴权路径加限流(slowapi/redis 令牌桶 + 失败锁定);`/register` 在已有用户后改邀请制或加验证码/限频。
- **[DM-03]** 给 `collection_job`/`ai_asset`/`work_trace` 指向 `runtime_instances`/`ai_assets` 的 4 个 FK 补 `ondelete="SET NULL"`;测试夹具对 SQLite 开 `PRAGMA foreign_keys=ON`。(`models/control_plane.py:348,540,698,699`)
- **[CORR-06]** `governance_service` get-or-create 包 `try/except IntegrityError` 后重查(并发竞态)。(`governance_service.py:38-49`)
- **[CORR-07]** 版本列表 N+1:批量 `select(PublicSkillRelease).where(version_id.in_(...))`。(`skills.py:185,921`)
- **[OPS-04]** 给 mysql/minio/worker 设 `deploy.resources.limits.memory`。
- **[OPS-05]** 结构化日志(`logging.dictConfig` JSON)+ 请求 ID 中间件 + Celery 透传 report_id 入日志。
- **[OPS-06]** `prod.sh backup` 扩展:同时备份 `repos_data`(Git bare)与 `minio_data`(对象),同时间戳归档。
- **[OPS-07]** nginx `proxy_pass` 用变量 + `resolver 127.0.0.11 valid=10s`,避免 backend 重启换 IP 后持续 502。(`frontend/nginx.conf`)
- **[OPS-08]** Celery 设 `task_acks_late=True` + `task_reject_on_worker_lost=True` + broker `visibility_timeout`(任务需幂等,见 PUSH/ingest 已具备)。
- **[ISO-004]** `_auth_robot` 显式 `system_role=SystemRole.USER`(固化隐式契约)。
- **[DM-04]** 0006 迁移的 `postgresql.ENUM` 换 `sa.Enum`(MySQL 死代码,与 DM-01-FULL 一并)。
- **[SEC-04]** `allow_methods`/`allow_headers` 收敛为实际所需集合(非通配)。

### [FR-012-OUTBOUND-PACKAGE] HIGH(功能补全 · 2026-06-17 决策本期做精简版)
- **文件**: 新 `backend/app/services/handover_package_service.py`;`control_plane.py`(新 2 个端点);复用 `artifact_service`(put_object + presigned)、`EvidenceItem`、evidence download-link 模式。预计无新迁移(复用 EvidenceItem;如需 `handover_packages` 表则加幂等迁移)。
- **目标**: 兑现 SC-002「离职交接包」——交接可生成 manifest+sha256+zip 并限时签名下载。
- **改法(精简版,prod-ready)**:
  1. `handover_package_service.build_package(db, case)`:汇总该 case 的 HandoverItem(资产卡:asset 名称/类型/criticality + recommended_action + 回执结果)、关联 WorkTrace 摘要、EvidenceItem 引用 → 组 `manifest.json`(`schema_version: duckdock-handover-pack-v1` + case 元数据 + items[] + generated_at)→ 打 zip(manifest + 分项 json)→ 算 sha256 → `artifact_service.put_object` 到 `handovers/case-{id}/pack-{ts}.zip` → 建 `EvidenceItem`(`source_type` 取合适值、`object_uri`、`sha256`、summary)。**不含明文凭证**;敏感工作历程沿用遮蔽规则(只放摘要)。
  2. 端点 `POST /handovers/{case_id}/package`(`HandoverManagerUser`,要求 case ∈ {APPROVED, VERIFYING, COMPLETED})生成包并返回 EvidenceItem;`GET /handovers/{case_id}/package/download-link`(复用 evidence download-link 的限时签名 URL + 权限 + 审计 + 通用错误文案不泄桶名)。
  3. 可选:`verify_handover` 成功置 COMPLETED 后自动触发一次打包(留 manual 触发亦可)。
- **测试**: 造 COMPLETED case → 打包 → 断言 manifest 含 items 与 sha256、EvidenceItem 落库、download-link 返限时 URL、越权 403 且留审计、敏感内容不泄。
- **验收**: SC-002 出站交接包可生成可下载校验;把 spec/001 FR-012 与 tasks T035 状态更新为「入站+出站均完成」。

---

## ✅ 决策已定（2026-06-17）——执行时无需再次等待确认

- **[FR-012] 出站交接包 → 本期实现(精简版)**:已转为上方可执行任务 `[FR-012-OUTBOUND-PACKAGE]`(归 P2)。依据:SC-002 核心交付物,数据与原语(artifact_service 签名 URL + EvidenceItem + download-link 模式)均已就绪。
- **[FR-019] auto 探针执行器 → 本期不做**:维持 manual-only;P1 `[FR-019-AUTO-GUARD]` 的 501 兜底即最终态。auto(T085)待现场探针就绪后**另立特性**,不在本期。
- **[DM-01] 迁移基线 → 本期只做 LITE**:`[DM-01-LITE]`(P1:`alembic check` 漂移闸 + DM-02 server_default 对齐)为本特性范围;`[DM-01-FULL]`(0001 固化为静态 DDL + 重写 0006 ENUM)风险较高、**独立排期**,不在本特性。

---

## 执行顺序与依赖

```
P0(必须最先,且 TEST-MYSQL-LANE 先于 CORR-01/02 的 MySQL 回归断言)
  CORR-01 ─┐
  CORR-02 ─┼─ 依赖 TEST-MYSQL-LANE 提供 MySQL 道
  CORR-TZ-AUDIT(扫尾)
P1(P0 后,组内可并行;同文件任务注意串行避免冲突)
  control_plane.py 串行组: HANDOVER-01 → HANDOVER-03 → HANDOVER-02 → FR-019-AUTO-GUARD
  其余可并行: ISO-001 / SEC-01 / PUSH-01 / CORR-03 / CORR-04 / OPS-01 / OPS-02 / OPS-03 / DM-01-LITE(含 DM-02)
P2: 上线后迭代,按价值排
DECISION: 等用户拍板
```

**完成定义(整体)**: SC-001~004 全满足;`pytest -m mysql` 在 CI 实跑;review 的全部 CRITICAL+HIGH 关闭且有回归测试;门禁未被削弱。
