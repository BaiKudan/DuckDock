from __future__ import annotations

import pytest

from scripts.g2_target_capacity_gate import require_safe_target


def test_target_capacity_gate_requires_credential_free_https_origin() -> None:
    assert (
        require_safe_target("https://duckdock.example.com", allow_http_localhost=False)
        == "https://duckdock.example.com"
    )

    with pytest.raises(ValueError, match="requires HTTPS"):
        require_safe_target("http://duckdock.example.com", allow_http_localhost=False)
    with pytest.raises(ValueError, match="credential-free"):
        require_safe_target("https://user:secret@duckdock.example.com", allow_http_localhost=False)
    with pytest.raises(ValueError, match="credential-free"):
        require_safe_target("https://duckdock.example.com?token=secret", allow_http_localhost=False)


def test_local_http_requires_an_explicit_validation_override() -> None:
    with pytest.raises(ValueError, match="requires HTTPS"):
        require_safe_target("http://127.0.0.1:8801", allow_http_localhost=False)

    assert (
        require_safe_target("http://127.0.0.1:8801", allow_http_localhost=True)
        == "http://127.0.0.1:8801"
    )
