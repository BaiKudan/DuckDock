from pydantic import BaseModel, EmailStr, field_validator
import re


class RegisterRequest(BaseModel):
    username: str
    email: EmailStr
    password: str
    full_name: str | None = None

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        if not re.match(r"^[a-zA-Z0-9_-]{3,64}$", v):
            raise ValueError("Username must be 3-64 chars, alphanumeric/underscore/dash only")
        return v.lower()

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class LoginRequest(BaseModel):
    username: str
    password: str


class LDAPLoginRequest(BaseModel):
    provider_id: int
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class ExchangeTokenRequest(BaseModel):
    token: str


class PublicSSOProviderOut(BaseModel):
    id: int
    name: str
    provider_type: str
    login_url: str | None = None


class RefreshRequest(BaseModel):
    refresh_token: str
