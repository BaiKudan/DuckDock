from datetime import datetime
from pydantic import BaseModel, EmailStr
from app.models.user import AuthSource, SystemRole


class UserOut(BaseModel):
    id: int
    username: str
    email: EmailStr
    full_name: str | None = None
    system_role: SystemRole
    auth_source: AuthSource
    enterprise_uid: str | None = None
    external_subject: str | None = None
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None = None

    model_config = {"from_attributes": True}


class UserUpdate(BaseModel):
    email: EmailStr | None = None
    is_active: bool | None = None
    system_role: SystemRole | None = None
