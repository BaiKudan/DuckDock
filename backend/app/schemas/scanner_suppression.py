from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ScannerRuleSuppressionCreate(BaseModel):
    rule_id: str = Field(..., min_length=1, max_length=64)
    file_pattern: str = Field(default="*", max_length=255)
    reason: str = Field(..., min_length=1, max_length=500)


class ScannerRuleSuppressionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    namespace_id: int
    rule_id: str
    file_pattern: str
    reason: str
    created_at: datetime
    created_by: int | None
