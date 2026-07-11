"""离职交接建议顾问(specs/001 T042 · 宪法原则 V:LLM 产出为建议)。

覆盖:
  · 规则版顾问:默认 MANUAL_REVIEW + 置信度 0;高关键级给显式复核理由;
  · LLM 顾问:解析批量建议、置信度 clamp、非法动作回落 MANUAL_REVIEW、幻觉 asset_id 忽略;
  · LLM 顾问异常 → 禁用并返回空(降级,不阻断);
  · 工厂:显式关闭走规则版;开启且配置完整走 LLM;开启但缺配置仍回落规则版;
  · 端点接线:analyze_handover 用顾问产出填 recommended_action/confidence/rationale,审计记 advisor_mode。
"""
from __future__ import annotations

import json

from sqlalchemy import select

from app.api.v1.endpoints import control_plane as cp
from app.api.v1.endpoints.control_plane import analyze_handover, create_handover
from app.core.config import settings
from app.models.audit import AuditLog
from app.models.namespace import Namespace
from app.models.control_plane import (
    AIAsset,
    AssetOwnership,
    AssetType,
    Criticality,
    HandoverAction,
    HandoverCaseType,
    HandoverItemStatus,
    HandoverStatus,
    OwnerType,
    RuntimeProvider,
)
from app.models.user import SystemRole, User
from app.schemas.control_plane import HandoverCaseCreate
from app.services.handover_advisor_service import (
    AssetContext,
    LLMHandoverAdvisor,
    Recommendation,
    RuleBasedHandoverAdvisor,
    build_ai_assist,
    build_handover_advisor,
)


def _ctx(asset_id, *, criticality="medium", name="A", asset_type="skill", status="active"):
    return AssetContext(
        asset_id=asset_id, name=name, asset_type=asset_type, criticality=criticality, status=status
    )


def _llm_advisor(**over):
    kwargs = dict(api_key="k", base_url="https://llm.local/v1", model="qwen", timeout_seconds=5)
    kwargs.update(over)
    return LLMHandoverAdvisor(**kwargs)


# ── 规则版 ──────────────────────────────────────────────────────

async def test_rule_based_defaults_manual_review_zero_confidence():
    advisor = RuleBasedHandoverAdvisor()
    recs = await advisor.recommend([_ctx(1), _ctx(2)])
    assert advisor.mode == "rule_based"
    assert set(recs) == {1, 2}
    for rec in recs.values():
        assert rec.recommended_action == HandoverAction.MANUAL_REVIEW
        assert rec.confidence == 0.0


async def test_rule_based_high_criticality_reason():
    advisor = RuleBasedHandoverAdvisor()
    recs = await advisor.recommend([_ctx(1, criticality="high")])
    assert "高关键级" in recs[1].rationale


# ── LLM 顾问解析 / 降级 ─────────────────────────────────────────

async def test_llm_advisor_parses_and_sanitizes(monkeypatch):
    advisor = _llm_advisor()

    async def fake_call(prompt):
        return {
            "recommendations": [
                {"asset_id": 1, "recommended_action": "transfer_owner", "confidence": 0.8, "rationale": "活跃资产"},
                {"asset_id": 2, "recommended_action": "DISABLE", "confidence": 1.7, "rationale": "已停用"},
                {"asset_id": 3, "recommended_action": "nonsense_action", "confidence": 0.5, "rationale": "x"},
                {"asset_id": 999, "recommended_action": "archive", "confidence": 0.4, "rationale": "幻觉资产"},
            ]
        }

    monkeypatch.setattr(advisor, "_call", fake_call)
    recs = await advisor.recommend([_ctx(1), _ctx(2), _ctx(3)])

    assert recs[1].recommended_action == HandoverAction.TRANSFER_OWNER
    assert recs[1].confidence == 0.8
    assert recs[2].recommended_action == HandoverAction.DISABLE  # 大写也能匹配
    assert recs[2].confidence == 1.0  # clamp 到 1
    assert recs[3].recommended_action == HandoverAction.MANUAL_REVIEW  # 非法动作回落
    assert 999 not in recs  # 幻觉出的资产 id 被忽略


async def test_llm_advisor_accepts_numeric_and_string_asset_ids(monkeypatch):
    """LLM 常把 JSON 数字给成 float(1.0)或字符串("2");两者都不能被静默丢弃。"""
    advisor = _llm_advisor()

    async def fake_call(prompt):
        return {
            "recommendations": [
                {"asset_id": 1.0, "recommended_action": "archive", "confidence": 0.6, "rationale": "float id"},
                {"asset_id": "2", "recommended_action": "ignore", "confidence": 0.4, "rationale": "string id"},
            ]
        }

    monkeypatch.setattr(advisor, "_call", fake_call)
    recs = await advisor.recommend([_ctx(1), _ctx(2)])
    assert set(recs) == {1, 2}
    assert recs[1].recommended_action == HandoverAction.ARCHIVE
    assert recs[2].recommended_action == HandoverAction.IGNORE


async def test_llm_advisor_failure_disables_and_returns_empty(monkeypatch):
    advisor = _llm_advisor()

    async def boom(prompt):
        raise RuntimeError("network down")

    monkeypatch.setattr(advisor, "_call", boom)
    recs = await advisor.recommend([_ctx(1)])
    assert recs == {}
    assert advisor.is_available() is False  # 失败后禁用,后续直接跳过


async def test_llm_advisor_unavailable_without_key():
    advisor = _llm_advisor(api_key="")
    assert advisor.is_available() is False
    assert await advisor.recommend([_ctx(1)]) == {}


# ── 工厂 ────────────────────────────────────────────────────────

def test_build_advisor_rule_based_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "HANDOVER_LLM_ENABLED", False)
    assert isinstance(build_handover_advisor(), RuleBasedHandoverAdvisor)


def test_build_advisor_llm_when_enabled_and_configured(monkeypatch):
    monkeypatch.setattr(settings, "HANDOVER_LLM_ENABLED", True)
    monkeypatch.setattr(settings, "HANDOVER_LLM_API_KEY", "k")
    monkeypatch.setattr(settings, "HANDOVER_LLM_BASE_URL", "https://llm.local/v1")
    monkeypatch.setattr(settings, "HANDOVER_LLM_MODEL", "qwen")
    assert isinstance(build_handover_advisor(), LLMHandoverAdvisor)


def test_build_advisor_falls_back_when_enabled_but_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "HANDOVER_LLM_ENABLED", True)
    monkeypatch.setattr(settings, "DUCKDOCK_LLM_API_KEY", "")
    monkeypatch.setattr(settings, "HANDOVER_LLM_API_KEY", "")
    monkeypatch.setattr(settings, "CLINIC_LLM_API_KEY", "")
    monkeypatch.setattr(settings, "SKILL_GEN_API_KEY", "")
    assert isinstance(build_handover_advisor(), RuleBasedHandoverAdvisor)


# ── 端点接线 ────────────────────────────────────────────────────

class _FakeAdvisor:
    mode = "fake-llm"

    def __init__(self, action, confidence, rationale):
        self._action, self._confidence, self._rationale = action, confidence, rationale

    async def recommend(self, assets):
        return {
            a.asset_id: Recommendation(self._action, self._confidence, self._rationale) for a in assets
        }


async def _make_user(session, *, username, system_role=SystemRole.USER):
    user = User(username=username, email=f"{username}@duckdock-ai.com", hashed_password="x", system_role=system_role)
    session.add(user)
    await session.flush()
    return user


async def test_analyze_applies_advisor_output_and_audits_mode(async_session, monkeypatch):
    admin = await _make_user(async_session, username="adv-admin", system_role=SystemRole.ADMIN)
    subject = await _make_user(async_session, username="adv-subject")
    asset = AIAsset(
        asset_type=AssetType.SKILL,
        name="Critical Pipeline",
        source_provider=RuntimeProvider.OPENCLAW,
        criticality=Criticality.HIGH,
    )
    async_session.add(asset)
    await async_session.flush()
    async_session.add(
        AssetOwnership(asset_id=asset.id, owner_type=OwnerType.CREATOR, user_id=subject.id, is_primary=True)
    )
    await async_session.flush()
    case = await create_handover(
        HandoverCaseCreate(
            case_type=HandoverCaseType.EMPLOYEE_OFFBOARDING, title="交接", subject_user_id=subject.id
        ),
        async_session,
        admin,
    )

    fake = _FakeAdvisor(HandoverAction.TRANSFER_OWNER, 0.85, "活跃高价值资产,建议转移负责人")
    monkeypatch.setattr(cp, "build_handover_advisor", lambda: fake)

    items = await analyze_handover(case.id, async_session, admin)

    assert len(items) == 1
    assert items[0].recommended_action == HandoverAction.TRANSFER_OWNER
    assert items[0].confidence == 0.85
    assert items[0].risk_reason == "活跃高价值资产,建议转移负责人"

    log = (
        await async_session.execute(
            select(AuditLog).where(AuditLog.action == "handover.analyzed")
        )
    ).scalar_one()
    assert log.details["advisor_mode"] == "fake-llm"
    assert log.details["created_items"] == 1


async def test_destructive_recommendation_stays_proposed_advice(async_session, monkeypatch):
    """原则 V:即便顾问(LLM)建议 DISABLE 且高置信,产出仍只是 PROPOSED 建议,
    case 不进入执行态——任何处置须经审批闸门。"""
    admin = await _make_user(async_session, username="adv-destruct", system_role=SystemRole.ADMIN)
    subject = await _make_user(async_session, username="adv-destruct-subj")
    asset = AIAsset(asset_type=AssetType.SKILL, name="Risky", source_provider=RuntimeProvider.OPENCLAW)
    async_session.add(asset)
    await async_session.flush()
    async_session.add(
        AssetOwnership(asset_id=asset.id, owner_type=OwnerType.CREATOR, user_id=subject.id, is_primary=True)
    )
    await async_session.flush()
    case = await create_handover(
        HandoverCaseCreate(
            case_type=HandoverCaseType.EMPLOYEE_OFFBOARDING, title="交接", subject_user_id=subject.id
        ),
        async_session,
        admin,
    )

    fake = _FakeAdvisor(HandoverAction.DISABLE, 0.99, "建议立即禁用")
    monkeypatch.setattr(cp, "build_handover_advisor", lambda: fake)
    items = await analyze_handover(case.id, async_session, admin)

    assert items[0].recommended_action == HandoverAction.DISABLE
    assert items[0].status == HandoverItemStatus.PROPOSED  # 仍是建议,未执行
    assert case.status == HandoverStatus.PENDING_APPROVAL  # 停在待审批,未进入 EXECUTING


async def test_analyze_scopes_by_namespace(async_session, monkeypatch):
    """analyze_handover 的 namespace 分支:按 AssetOwnership.namespace_id 过滤资产。"""
    admin = await _make_user(async_session, username="adv-ns", system_role=SystemRole.ADMIN)
    ns_in = Namespace(name="adv-in", owner_id=admin.id)
    ns_out = Namespace(name="adv-out", owner_id=admin.id)
    async_session.add_all([ns_in, ns_out])
    await async_session.flush()
    in_ns = AIAsset(asset_type=AssetType.SKILL, name="In-NS", source_provider=RuntimeProvider.OPENCLAW)
    out_ns = AIAsset(asset_type=AssetType.SKILL, name="Out-NS", source_provider=RuntimeProvider.OPENCLAW)
    async_session.add_all([in_ns, out_ns])
    await async_session.flush()
    async_session.add_all(
        [
            AssetOwnership(asset_id=in_ns.id, owner_type=OwnerType.STEWARD, namespace_id=ns_in.id),
            AssetOwnership(asset_id=out_ns.id, owner_type=OwnerType.STEWARD, namespace_id=ns_out.id),
        ]
    )
    await async_session.flush()
    case = await create_handover(
        HandoverCaseCreate(case_type=HandoverCaseType.PROJECT_HANDOVER, title="项目交接", namespace_id=ns_in.id),
        async_session,
        admin,
    )

    # 默认规则版顾问即可——只验资产范围
    items = await analyze_handover(case.id, async_session, admin)
    asset_ids = {item.asset_id for item in items}
    assert in_ns.id in asset_ids
    assert out_ns.id not in asset_ids


# ── 共享降级指示 ai_assist(L2-DEGRADE-SURFACE) ─────────────────

class _FakeLLMAdvisor:
    """mode="llm" 的桩:模拟激活了 LLM 顾问并真正产出建议(非降级路径)。"""

    mode = "llm"

    def __init__(self, action=HandoverAction.TRANSFER_OWNER, confidence=0.8, rationale="LLM 建议"):
        self._action, self._confidence, self._rationale = action, confidence, rationale

    async def recommend(self, assets):
        return {
            a.asset_id: Recommendation(self._action, self._confidence, self._rationale) for a in assets
        }


def test_build_ai_assist_baseline_for_rule_based():
    """规则版顾问(无 key 解析)→ baseline + degraded + 原因。"""
    indicator = build_ai_assist(RuleBasedHandoverAdvisor(), llm_produced=False)
    assert indicator["mode"] == "baseline"
    assert indicator["degraded"] is True
    assert indicator["reason"]  # 非空说明降级原因
    # llm_produced 对规则版无意义,结果一致
    assert build_ai_assist(RuleBasedHandoverAdvisor(), llm_produced=True)["mode"] == "baseline"


def test_build_ai_assist_llm_when_produced():
    """激活 LLM 顾问且真正产出 → mode=llm,未降级。"""
    indicator = build_ai_assist(_FakeLLMAdvisor(), llm_produced=True)
    assert indicator == {"mode": "llm", "degraded": False, "reason": None}


def test_build_ai_assist_llm_silent_fallback_is_degraded():
    """激活 LLM 顾问但本次没产出(调用失败被禁用 → 静默回落)→ baseline + degraded。"""
    indicator = build_ai_assist(_FakeLLMAdvisor(), llm_produced=False)
    assert indicator["mode"] == "baseline"
    assert indicator["degraded"] is True
    assert "llm" in indicator["reason"].lower()


async def _seed_case_with_asset(async_session, *, username):
    admin = await _make_user(async_session, username=f"{username}-admin", system_role=SystemRole.ADMIN)
    subject = await _make_user(async_session, username=f"{username}-subj")
    asset = AIAsset(
        asset_type=AssetType.SKILL,
        name=f"{username}-asset",
        source_provider=RuntimeProvider.OPENCLAW,
        criticality=Criticality.HIGH,
    )
    async_session.add(asset)
    await async_session.flush()
    async_session.add(
        AssetOwnership(asset_id=asset.id, owner_type=OwnerType.CREATOR, user_id=subject.id, is_primary=True)
    )
    await async_session.flush()
    case = await create_handover(
        HandoverCaseCreate(
            case_type=HandoverCaseType.EMPLOYEE_OFFBOARDING, title="交接", subject_user_id=subject.id
        ),
        async_session,
        admin,
    )
    return admin, case


async def test_analyze_surfaces_baseline_ai_assist_without_key(async_session, monkeypatch):
    """无 LLM key → 工厂回落规则版 → analyze 响应携带 ai_assist baseline/degraded(头+审计+返回值)。"""
    admin, case = await _seed_case_with_asset(async_session, username="ai-assist-base")
    # 强制工厂走规则版兜底(无 key 解析出)
    monkeypatch.setattr(cp, "build_handover_advisor", lambda: RuleBasedHandoverAdvisor())

    response = cp.Response()
    items = await analyze_handover(case.id, async_session, admin, response)

    # 返回值携带 ai_assist
    assert items.ai_assist["mode"] == "baseline"
    assert items.ai_assist["degraded"] is True
    assert items.ai_assist["reason"]
    # HTTP 头暴露同一指示
    assert json.loads(response.headers["X-AI-Assist"])["mode"] == "baseline"
    # 审计落库
    log = (
        await async_session.execute(
            select(AuditLog).where(AuditLog.action == "handover.analyzed")
        )
    ).scalar_one()
    assert log.details["ai_assist"]["mode"] == "baseline"
    assert log.details["ai_assist"]["degraded"] is True


async def test_analyze_surfaces_llm_ai_assist_when_advisor_produces(async_session, monkeypatch):
    """monkeypatch 一个 mode=llm 且产出建议的顾问 → ai_assist mode=='llm'、未降级。"""
    admin, case = await _seed_case_with_asset(async_session, username="ai-assist-llm")
    fake = _FakeLLMAdvisor(HandoverAction.TRANSFER_OWNER, 0.9, "活跃资产建议转移")
    monkeypatch.setattr(cp, "build_handover_advisor", lambda: fake)

    response = cp.Response()
    items = await analyze_handover(case.id, async_session, admin, response)

    assert items.ai_assist == {"mode": "llm", "degraded": False, "reason": None}
    assert json.loads(response.headers["X-AI-Assist"]) == {"mode": "llm", "degraded": False, "reason": None}
    log = (
        await async_session.execute(
            select(AuditLog).where(AuditLog.action == "handover.analyzed")
        )
    ).scalar_one()
    assert log.details["ai_assist"]["mode"] == "llm"
    assert log.details["ai_assist"]["degraded"] is False


async def test_analyze_llm_advisor_silent_fallback_surfaces_degraded(async_session, monkeypatch):
    """激活 mode=llm 但 recommend() 返回空(调用失败被禁用)→ ai_assist baseline/degraded,
    杜绝把 MANUAL_REVIEW 兜底冒充成 AI 产出。"""
    admin, case = await _seed_case_with_asset(async_session, username="ai-assist-fallback")

    class _SilentLLM:
        mode = "llm"

        async def recommend(self, assets):
            return {}  # 模拟 LLM 调用失败被禁用后返回空

    monkeypatch.setattr(cp, "build_handover_advisor", lambda: _SilentLLM())

    response = cp.Response()
    items = await analyze_handover(case.id, async_session, admin, response)

    # 仍生成 item(逐项回落 MANUAL_REVIEW),但 ai_assist 诚实标注降级
    assert len(items) == 1
    assert items[0].recommended_action == HandoverAction.MANUAL_REVIEW
    assert items.ai_assist["mode"] == "baseline"
    assert items.ai_assist["degraded"] is True
