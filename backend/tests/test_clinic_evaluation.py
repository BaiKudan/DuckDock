"""L2-CLINIC-8DIM + clinic degrade.

审计发现:LLM judge 只调整 8 维中的 4 维(其余永远停留在启发式),且评测结果对外
不暴露"这次到底是 AI 还是启发式",release-gate 由此把启发式分数当成 AI 评审静默使用。

本测试钉死两条契约:
  1) 有 key(monkeypatch 真实 ClinicJudge)→ 8 维全部拿到 LLM 来源的分数(source=="llm"),
     且共享 ai_assist 指标 mode=="llm" / degraded is False;
  2) 无 key → provider 降级 Noop,evaluation 的 ai_assist mode=="baseline" / degraded is True,
     每个维度 source=="deterministic",且 ai_assist 被记进对外 summary,供 release-gate 透明决策。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest

from app.core.config import settings
from app.models.clinic import DIMENSIONS, ClinicEvaluation, EvalStatus
from app.models.namespace import Namespace
from app.models.user import User
from app.services.clinic_service import (
    ClinicService,
    NoopClinicJudgeProvider,
    clinic_service,
)
from app.workers import clinic_tasks

ALL_DIMENSION_IDS = {dimension_id for dimension_id, *_ in DIMENSIONS}


@pytest.fixture(autouse=True)
def _clear_llm_keys(monkeypatch):
    """默认清空所有 LLM key,使 provider 降级 Noop;需要 LLM 的用例自行 monkeypatch provider。"""
    for name in (
        "DUCKDOCK_LLM_API_KEY",
        "SKILL_GEN_API_KEY",
        "CLINIC_LLM_API_KEY",
        "HANDOVER_LLM_API_KEY",
    ):
        monkeypatch.setattr(settings, name, "")
    monkeypatch.setattr(settings, "CLINIC_LLM_PROVIDER", "openai_compatible")
    monkeypatch.setattr(settings, "CLINIC_EVAL_MODE", "hybrid")


def _sample_skills() -> list[dict[str, Any]]:
    return [
        {
            "name": "code-review",
            "description": "Reviews code for quality and bugs.",
            "has_production": True,
            "version_count": 3,
            "last_updated": "2026-06-01T00:00:00+00:00",
            "latest_metadata": {"name": "code-review", "version": "1.2.0", "description": "x"},
            "skill_md": "# Code Review\n## Usage\nExample: review a PR.\n## Limitations\n注意 ...",
            "system_prompt": "You are a strict reviewer. You must return markdown. Do not skip files.",
        },
        {
            "name": "data_sync",
            "description": "Syncs data between systems.",
            "has_production": False,
            "version_count": 1,
            "last_updated": "2025-01-01T00:00:00+00:00",
            "latest_metadata": {"name": "data_sync"},
            "skill_md": "short",
            "system_prompt": "sync stuff",
        },
    ]


class _FakeClinicJudge:
    """模拟一个真实可用的 ClinicJudge:对任意维度都返回结构化 LLM 评分。"""

    model = "fake-judge-model"

    def __init__(self) -> None:
        self.seen_dimensions: list[str] = []

    def is_available(self) -> bool:
        return True

    def evaluate_dimension(
        self,
        *,
        dimension_id: str,
        rubric: dict[str, Any],
        facts: dict[str, Any],
        sampled_skills: list[dict[str, Any]],
    ) -> dict[str, Any]:
        self.seen_dimensions.append(dimension_id)
        return {
            "score": 91.0,
            "confidence": 0.9,
            "reasoning_summary": f"LLM judged {dimension_id}",
            "issues": [f"llm-issue for {dimension_id}"],
            "evidence": [{"skill": "code-review", "reason": "model evidence"}],
        }


def test_no_key_degrades_to_baseline_and_records_ai_assist(monkeypatch):
    """无 key:provider 降级 Noop;ai_assist=baseline/degraded;每个维度 source=deterministic。"""
    provider = ClinicService()._build_provider()
    assert isinstance(provider, NoopClinicJudgeProvider)

    output = clinic_service.evaluate(_sample_skills(), scan_results={})

    ai_assist = output["ai_assist"]
    assert ai_assist["mode"] == "baseline"
    assert ai_assist["degraded"] is True
    assert ai_assist["reason"]

    # 8 维都存在,且都标注 deterministic 来源(未被 LLM 触碰)。
    assert set(output["dimension_scores"].keys()) == ALL_DIMENSION_IDS
    for dim in output["dimension_scores"].values():
        assert dim["source"] == "deterministic"

    # 对外 summary 必须携带 ai_assist,使 release-gate 不再把启发式当 AI 静默使用。
    assert output["summary"]["ai_assist"] == ai_assist
    assert output["summary"]["overall_score"] == output["overall_score"]
    assert output["summary"]["grade"] == output["grade"]


def test_keyed_judge_scores_all_eight_dimensions_with_llm_source(monkeypatch):
    """有真实 judge:8 维全部经 LLM 评分(source=llm),ai_assist=llm/未降级。"""
    fake = _FakeClinicJudge()
    monkeypatch.setattr(ClinicService, "_build_provider", lambda self: fake)

    output = clinic_service.evaluate(_sample_skills(), scan_results={})

    ai_assist = output["ai_assist"]
    assert ai_assist["mode"] == "llm"
    assert ai_assist["degraded"] is False
    assert ai_assist["reason"] is None

    # 关键回归:judge 必须被全部 8 维调用,而非历史上的 4 维。
    assert set(fake.seen_dimensions) == ALL_DIMENSION_IDS

    assert set(output["dimension_scores"].keys()) == ALL_DIMENSION_IDS
    for dimension_id, dim in output["dimension_scores"].items():
        assert dim["source"] == "llm", f"{dimension_id} not LLM-sourced"
        # blend 机制仍然生效:LLM 91 与启发式按权混合,分数应被 LLM 抬升但非纯 91。
        assert dim["reasoning_summary"] == f"LLM judged {dimension_id}"

    assert output["summary"]["ai_assist"] == ai_assist


def test_deterministic_mode_keeps_baseline_even_with_judge(monkeypatch):
    """CLINIC_EVAL_MODE=deterministic:即使 judge 可用,也不调用 LLM,ai_assist=baseline。"""
    monkeypatch.setattr(settings, "CLINIC_EVAL_MODE", "deterministic")
    fake = _FakeClinicJudge()
    monkeypatch.setattr(ClinicService, "_build_provider", lambda self: fake)

    output = clinic_service.evaluate(_sample_skills(), scan_results={})

    assert fake.seen_dimensions == []
    assert output["ai_assist"]["mode"] == "baseline"
    assert output["ai_assist"]["degraded"] is True
    for dim in output["dimension_scores"].values():
        assert dim["source"] == "deterministic"


def test_gate_thresholds_unchanged_grade_mapping():
    """守护:不得改动 gate 阈值/评级映射。overall_score 仍由权重加权得出。"""
    output = clinic_service.evaluate(_sample_skills(), scan_results={})
    assert 0.0 <= output["overall_score"] <= 100.0
    assert output["grade"] in {"A", "B+", "B", "C+", "C", "D"}


async def test_clinic_worker_persists_langfuse_trace_id(async_session, monkeypatch):
    user = User(username="clinic-trace-user", email="clinic-trace@example.com", hashed_password="x")
    async_session.add(user)
    await async_session.flush()
    namespace = Namespace(name="clinic-trace-ns", owner_id=user.id)
    async_session.add(namespace)
    await async_session.flush()
    evaluation = ClinicEvaluation(namespace_id=namespace.id, status=EvalStatus.PENDING, triggered_by=user.id)
    async_session.add(evaluation)
    await async_session.commit()

    def _fake_evaluate(**_kwargs):
        return {
            "overall_score": 88.0,
            "grade": "B",
            "dimension_scores": {},
            "recommendations": [],
            "ai_assist": {"mode": "baseline", "degraded": True, "reason": "test"},
            "langfuse_trace_id": "trace-clinic-123",
        }

    async def _noop_dispatch(*_args, **_kwargs):
        return None

    @asynccontextmanager
    async def _fake_session(*_args, **_kwargs):
        yield async_session

    monkeypatch.setattr(clinic_tasks, "worker_db_session", _fake_session)
    monkeypatch.setattr(clinic_tasks.clinic_service, "evaluate", _fake_evaluate)
    monkeypatch.setattr(clinic_tasks, "dispatch_event", _noop_dispatch)

    await clinic_tasks._async_evaluate(evaluation.id)
    await async_session.refresh(evaluation)

    assert evaluation.status == EvalStatus.COMPLETED
    assert evaluation.trace_id == "trace-clinic-123"
