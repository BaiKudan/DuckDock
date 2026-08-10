from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app.services.langfuse_service import LangfuseService


def test_trace_lookup_uses_v4_observations_api(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _Observations:
        def get_many(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                data=[
                    SimpleNamespace(
                        trace_id="a" * 32,
                        metadata={"clinic_evaluation_id": 42},
                    )
                ]
            )

    client = SimpleNamespace(
        api=SimpleNamespace(observations=_Observations()),
        get_trace_url=lambda *, trace_id: f"https://langfuse.test/t/{trace_id}",
    )
    service = LangfuseService()
    monkeypatch.setattr(service, "client", lambda: client)

    result = service.get_trace_link_for_evaluation(
        evaluation_id=42,
        created_at=datetime(2026, 7, 31, 8, tzinfo=timezone.utc),
    )

    assert result == {
        "trace_id": "a" * 32,
        "trace_url": "https://langfuse.test/t/" + "a" * 32,
    }
    assert captured["fields"] == "core,basic,metadata"
    assert captured["name"] == "clinic-evaluation"
    assert captured["limit"] == 20
    assert captured["from_start_time"] == datetime(
        2026, 7, 31, 7, 58, tzinfo=timezone.utc
    )
    assert captured["to_start_time"] == datetime(
        2026, 7, 31, 9, tzinfo=timezone.utc
    )


def test_trace_lookup_ignores_nonmatching_or_missing_trace_ids(monkeypatch) -> None:
    response = SimpleNamespace(
        data=[
            SimpleNamespace(
                trace_id="b" * 32,
                metadata={"clinic_evaluation_id": 7},
            ),
            SimpleNamespace(
                trace_id=None,
                metadata={"clinic_evaluation_id": 42},
            ),
        ]
    )
    client = SimpleNamespace(
        api=SimpleNamespace(
            observations=SimpleNamespace(get_many=lambda **_: response)
        ),
        get_trace_url=lambda *, trace_id: f"https://langfuse.test/t/{trace_id}",
    )
    service = LangfuseService()
    monkeypatch.setattr(service, "client", lambda: client)

    assert service.get_trace_link_for_evaluation(evaluation_id=42) is None


def test_trace_url_failure_isolated_and_public_host_rewritten(monkeypatch) -> None:
    service = LangfuseService()
    client = SimpleNamespace(
        get_trace_url=lambda *, trace_id: (
            f"http://langfuse-web:3000/project/project-1/traces/{trace_id}"
        )
    )
    monkeypatch.setattr(service, "client", lambda: client)
    monkeypatch.setattr(
        "app.services.langfuse_service.settings.LANGFUSE_PUBLIC_BASE_URL",
        "http://localhost:3200",
    )

    assert service.get_trace_url(trace_id="c" * 32) == (
        "http://localhost:3200/project/project-1/traces/" + "c" * 32
    )

    def _raise(*, trace_id):
        raise RuntimeError(f"provider unavailable for {trace_id}")

    client.get_trace_url = _raise
    assert service.get_trace_url(trace_id="d" * 32) is None
