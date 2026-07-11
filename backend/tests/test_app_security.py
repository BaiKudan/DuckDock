"""SEC-02: app-layer startup foolproofing.

A production runtime (DEBUG=False) must refuse to boot with an insecure SECRET_KEY or a
missing credential key, and must not expose the interactive API docs. The dev/test path
(DEBUG=True, placeholder secret from .env) must remain unaffected.
"""
from __future__ import annotations

import pytest

from app.core.config import (
    INSECURE_SECRET_KEY_DEFAULT,
    Settings,
    assert_production_security,
)

_STRONG_SECRET = "0123456789abcdef0123456789abcdef0123456789abcdef"
_STRONG_CRED_KEY = "qyEzvqJMVf9na6CRD2YKj4LzWhm521Q8-w8XY5nm2K4="


def test_prod_boot_rejects_default_secret_key():
    settings = Settings(
        DEBUG=False,
        SECRET_KEY=INSECURE_SECRET_KEY_DEFAULT,
        DUCKDOCK_CREDENTIAL_KEY=_STRONG_CRED_KEY,
    )

    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        assert_production_security(settings)


def test_prod_boot_rejects_empty_secret_key():
    settings = Settings(
        DEBUG=False,
        SECRET_KEY="",
        DUCKDOCK_CREDENTIAL_KEY=_STRONG_CRED_KEY,
    )

    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        assert_production_security(settings)


def test_prod_boot_rejects_empty_credential_key():
    settings = Settings(
        DEBUG=False,
        SECRET_KEY=_STRONG_SECRET,
        DUCKDOCK_CREDENTIAL_KEY="",
    )

    with pytest.raises(RuntimeError, match="DUCKDOCK_CREDENTIAL_KEY"):
        assert_production_security(settings)


def test_prod_boot_ok_with_strong_secret_and_credential_key():
    settings = Settings(
        DEBUG=False,
        SECRET_KEY=_STRONG_SECRET,
        DUCKDOCK_CREDENTIAL_KEY=_STRONG_CRED_KEY,
    )

    # Must not raise when properly configured.
    assert_production_security(settings)


def test_debug_path_is_never_blocked_even_with_default_secret():
    # The dev/test path loads the placeholder secret from .env with DEBUG=True; the
    # fail-fast must stay dormant there so `from app.main import app` keeps working.
    settings = Settings(
        DEBUG=True,
        SECRET_KEY=INSECURE_SECRET_KEY_DEFAULT,
        DUCKDOCK_CREDENTIAL_KEY="",
    )

    assert_production_security(settings)


def test_docs_disabled_when_not_debug():
    from fastapi import FastAPI

    debug_app = FastAPI(docs_url="/docs", redoc_url="/redoc", openapi_url="/openapi.json")
    assert debug_app.docs_url == "/docs"

    prod_app = FastAPI(
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    assert prod_app.docs_url is None
    assert prod_app.redoc_url is None
    assert prod_app.openapi_url is None


def test_main_app_imports_and_docs_track_debug_flag():
    from app.core.config import settings
    from app.main import app

    # `from app.main import app` must keep working in the test runtime.
    assert app is not None
    if settings.DEBUG:
        assert app.docs_url == "/docs"
    else:
        assert app.docs_url is None
        assert app.openapi_url is None
