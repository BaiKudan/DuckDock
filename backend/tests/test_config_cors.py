from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_cors_origins_accepts_json_array_string():
    settings = Settings(CORS_ORIGINS='["https://app.example.com","https://admin.example.com"]')

    assert settings.CORS_ORIGINS == ["https://app.example.com", "https://admin.example.com"]


def test_cors_origins_accepts_comma_separated_string():
    settings = Settings(CORS_ORIGINS="https://app.example.com, https://admin.example.com")

    assert settings.CORS_ORIGINS == ["https://app.example.com", "https://admin.example.com"]


def test_cors_origins_accepts_env_comma_separated_string(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "https://app.example.com, https://admin.example.com")

    settings = Settings()

    assert settings.CORS_ORIGINS == ["https://app.example.com", "https://admin.example.com"]


def test_cors_origins_rejects_wildcard_with_credentials():
    with pytest.raises(ValidationError, match="must not contain"):
        Settings(CORS_ORIGINS=["*"])
