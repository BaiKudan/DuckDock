from datetime import datetime

from pydantic import BaseModel, field_validator

from app.core.identifiers import normalize_resource_identifier
from app.models.namespace import NamespaceRole


class NamespaceCreate(BaseModel):
    name: str
    description: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return normalize_resource_identifier(value, "Namespace name")


class NamespaceUpdate(BaseModel):
    description: str | None = None


class NamespaceOut(BaseModel):
    id: int
    name: str
    description: str | None
    owner_id: int
    created_at: datetime
    deleted_at: datetime | None = None

    model_config = {"from_attributes": True}


class MemberAdd(BaseModel):
    username: str
    role: NamespaceRole = NamespaceRole.DEVELOPER


class MemberOut(BaseModel):
    user_id: int
    username: str
    role: NamespaceRole
    created_at: datetime
