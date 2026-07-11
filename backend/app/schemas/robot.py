from datetime import datetime
from pydantic import BaseModel
from app.models.namespace import NamespaceRole


class RobotCreate(BaseModel):
    name: str
    description: str | None = None
    role: NamespaceRole = NamespaceRole.READONLY
    expires_days: int | None = None   # None = no expiry


class RobotOut(BaseModel):
    id: int
    namespace_id: int
    name: str
    description: str | None
    token_prefix: str
    role: NamespaceRole
    is_active: bool
    expires_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class RobotCreated(RobotOut):
    """Returned once on creation — includes the raw token."""
    token: str
