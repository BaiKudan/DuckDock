from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.clinic import DIMENSIONS, EvalStatus


class DimensionEvidence(BaseModel):
    skill: str | None = None
    reason: str
    snippet: str | None = None


class AiAssist(BaseModel):
    """共享降级指标:本次评测是真实 LLM 评审(llm)还是启发式回落(baseline）。"""

    mode: Literal["llm", "baseline"]
    degraded: bool
    reason: str | None = None


class DimensionScore(BaseModel):
    score: float
    weight: int
    issues: list[str] = Field(default_factory=list)
    details: str = ""
    trend: Literal["up", "down", "stable"] = "stable"
    confidence: float | None = None
    reasoning_summary: str | None = None
    evidence: list[DimensionEvidence] = Field(default_factory=list)
    # 每维分数来源:deterministic(纯启发式) | llm(已混入 LLM 评分)。
    source: Literal["deterministic", "llm"] = "deterministic"


class Recommendation(BaseModel):
    dimension: str
    priority: Literal["critical", "high", "medium", "low"]
    title: str
    description: str
    action: Literal["auto", "suggest", "manual"]
    auto_fixable: bool = False


class EvaluationOut(BaseModel):
    id: int
    namespace_id: int
    status: EvalStatus
    overall_score: float | None
    grade: str | None
    dimension_scores: dict[str, DimensionScore] | None
    recommendations: list[Recommendation] | None
    ai_assist: AiAssist | None = None
    langfuse_trace_id: str | None = None
    langfuse_trace_url: str | None = None
    triggered_by: int | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None

    model_config = {"from_attributes": True}


class EvaluationSummary(BaseModel):
    id: int
    status: EvalStatus
    overall_score: float | None
    grade: str | None
    created_at: datetime
    completed_at: datetime | None

    model_config = {"from_attributes": True}


class DimensionMeta(BaseModel):
    id: str
    name_zh: str
    name_en: str
    weight: int


DIMENSION_META = [
    DimensionMeta(id=dimension_id, name_zh=name_zh, name_en=name_en, weight=weight)
    for dimension_id, name_zh, name_en, weight in DIMENSIONS
]
