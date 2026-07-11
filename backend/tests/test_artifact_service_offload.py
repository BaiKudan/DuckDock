"""对象存储阻塞 I/O 的事件循环卸载(PUSH-03 · 宪法原则 II 性能/不阻塞循环)。

artifact_service 的 head/hash/read 走同步 boto/MinIO 客户端,直接在协程里调用会阻塞
事件循环。本测试覆盖**异步包装**语义:
  · head_object_async / read_object_bytes_async / hash_object_async 是协程(coroutine);
  · 协程返回值与对应同步方法**逐字一致**(签名/返回值不变);
  · 阻塞工作经由 ``asyncio.to_thread`` 派发到工作线程(monkeypatch 底层同步实现 + 断言入线程)。
全程 mock 底层同步客户端,不连真实 MinIO。
"""
from __future__ import annotations

import asyncio
import inspect
import io
import threading

import pytest

from app.services.artifact_service import ArtifactStorageService

_BODY = b"duckdock-pack-bytes" * 4096  # > 1 chunk so streamed hashing iterates


class _FakeBody:
    def __init__(self, data: bytes) -> None:
        self._buf = io.BytesIO(data)

    def read(self, size: int = -1) -> bytes:
        return self._buf.read(size)


class _FakeDataClient:
    """Records the thread each blocking boto call ran on (the 'sync client')."""

    def __init__(self, body: bytes) -> None:
        self._body = body
        self.threads: list[int] = []

    def head_bucket(self, **_kw):  # _ensure_bucket happy path
        self.threads.append(threading.get_ident())
        return {}

    def head_object(self, **_kw):
        self.threads.append(threading.get_ident())
        return {
            "ContentLength": len(self._body),
            "ContentType": "application/gzip",
            "ETag": '"deadbeef"',
            "Metadata": {"namespace": "acme"},
            "LastModified": None,
        }

    def get_object(self, **_kw):
        self.threads.append(threading.get_ident())
        return {"Body": _FakeBody(self._body)}


@pytest.fixture
def service(monkeypatch) -> ArtifactStorageService:
    svc = ArtifactStorageService()
    svc.bucket = "duckdock-test"
    client = _FakeDataClient(_BODY)
    # bucket already ensured so we don't exercise create_bucket branches
    svc._bucket_ensured = True
    svc._data_client = client
    return svc


def test_async_methods_are_coroutine_functions() -> None:
    """The three offload entrypoints must be coroutine functions."""
    assert inspect.iscoroutinefunction(ArtifactStorageService.head_object_async)
    assert inspect.iscoroutinefunction(ArtifactStorageService.read_object_bytes_async)
    assert inspect.iscoroutinefunction(ArtifactStorageService.hash_object_async)


async def test_head_object_async_returns_sync_value(service: ArtifactStorageService) -> None:
    coro = service.head_object_async("registry/x/bundle.tar.gz")
    assert asyncio.iscoroutine(coro)
    result = await coro
    expected = service.head_object("registry/x/bundle.tar.gz")
    assert result == expected
    assert result["size_bytes"] == len(_BODY)
    assert result["bucket"] == "duckdock-test"


async def test_read_object_bytes_async_returns_sync_value(service: ArtifactStorageService) -> None:
    result = await service.read_object_bytes_async("registry/x/bundle.tar.gz")
    assert result == _BODY
    assert result == service.read_object_bytes("registry/x/bundle.tar.gz")


async def test_read_object_bytes_async_forwards_max_bytes(service: ArtifactStorageService) -> None:
    from app.services.artifact_service import ArtifactStorageError

    with pytest.raises(ArtifactStorageError):
        await service.read_object_bytes_async("registry/x/bundle.tar.gz", max_bytes=8)


async def test_hash_object_async_returns_sync_value(service: ArtifactStorageService) -> None:
    result = await service.hash_object_async("registry/x/bundle.tar.gz")
    expected = service.hash_object("registry/x/bundle.tar.gz")
    assert result == expected
    assert result["size_bytes"] == len(_BODY)
    assert len(result["sha256"]) == 64


async def test_offload_dispatches_to_worker_thread(service: ArtifactStorageService) -> None:
    """Blocking boto work must run off the event-loop thread (via to_thread)."""
    loop_thread = threading.get_ident()
    client: _FakeDataClient = service._data_client  # type: ignore[assignment]

    await service.head_object_async("k")
    await service.read_object_bytes_async("k")
    await service.hash_object_async("k")

    assert client.threads, "expected the sync client to have been invoked"
    assert all(tid != loop_thread for tid in client.threads), (
        "blocking object-store calls must be offloaded off the event loop"
    )


async def test_async_wrapper_awaits_to_thread(service: ArtifactStorageService, monkeypatch) -> None:
    """The async wrapper must route the blocking sync method through asyncio.to_thread."""
    import app.services.artifact_service as mod

    calls: list[object] = []
    real_to_thread = asyncio.to_thread

    async def _spy_to_thread(func, /, *args, **kwargs):
        calls.append(func)
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(mod.asyncio, "to_thread", _spy_to_thread)

    await service.head_object_async("k")
    await service.read_object_bytes_async("k", max_bytes=None)
    await service.hash_object_async("k")

    # each wrapper dispatched its corresponding bound sync method through to_thread
    assert service.head_object in calls
    assert service.read_object_bytes in calls
    assert service.hash_object in calls
