import enum
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class EvalStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


DIMENSIONS = [
    ("skill_completeness", "完整度", "Completeness", 15),
    ("skill_quality", "质量", "Quality", 20),
    ("skill_coherence", "一致性", "Coherence", 15),
    ("security_health", "安全", "Security", 10),
    ("documentation", "文档", "Docs", 10),
    ("version_currency", "时效性", "Currency", 15),
    ("style_consistency", "风格", "Style", 5),
    ("interaction_quality", "交互", "Interaction", 10),
]


class ClinicEvaluation(Base):
    __tablename__ = "clinic_evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[EvalStatus] = mapped_column(
        Enum(EvalStatus),
        default=EvalStatus.PENDING,
        nullable=False,
    )
    overall_score: Mapped[float | None] = mapped_column(Float)
    grade: Mapped[str | None] = mapped_column(String(4))
    dimension_scores: Mapped[dict | None] = mapped_column(JSON)
    recommendations: Mapped[list | None] = mapped_column(JSON)
    # 共享降级指标 {"mode","degraded","reason"}:暴露本次评测是 AI 还是启发式,
    # 供 release-gate / API 透明决策(L2-CLINIC degrade）。
    ai_assist: Mapped[dict | None] = mapped_column(JSON)
    trace_id: Mapped[str | None] = mapped_column(String(128), index=True)
    triggered_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(String(512))
