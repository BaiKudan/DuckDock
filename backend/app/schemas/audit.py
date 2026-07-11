from datetime import datetime
from pydantic import BaseModel


class AuditLogOut(BaseModel):
    id: int
    user_id: int | None
    username: str | None
    action: str
    resource_type: str | None
    resource_id: int | None
    namespace_id: int | None
    details: dict | None
    ip_address: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
