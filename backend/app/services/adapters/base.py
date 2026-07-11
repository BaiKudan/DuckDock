from __future__ import annotations

from abc import ABC, abstractmethod

from app.models.control_plane import CollectionJob, RuntimeInstance
from app.services.adapters.contracts import (
    AdapterCapabilities,
    AdapterCollectionResult,
    AdapterConnectionResult,
)


class BaseRuntimeAdapter(ABC):
    adapter_name = "base"

    def __init__(self, runtime: RuntimeInstance):
        self.runtime = runtime

    @abstractmethod
    async def test_connection(self) -> AdapterConnectionResult:
        raise NotImplementedError

    @abstractmethod
    async def list_capabilities(self) -> AdapterCapabilities:
        raise NotImplementedError

    @abstractmethod
    async def collect(self, job: CollectionJob) -> AdapterCollectionResult:
        raise NotImplementedError
