from __future__ import annotations

from app.models.control_plane import CollectionJob
from app.services.adapters.base import BaseRuntimeAdapter
from app.services.adapters.contracts import (
    AdapterCapabilities,
    AdapterCollectionResult,
    AdapterConnectionResult,
)


class GenericRuntimeAdapter(BaseRuntimeAdapter):
    adapter_name = "generic"

    async def test_connection(self) -> AdapterConnectionResult:
        return AdapterConnectionResult(
            status="degraded",
            message=(
                "Generic adapter has no live provider connection. "
                "Complete a profile handshake and health probe."
            ),
            details={
                "provider": self.runtime.provider.value,
                "reason_code": "dynamic_handshake_required",
            },
        )

    async def list_capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            provider=self.runtime.provider,
            adapter_name=self.adapter_name,
            asset_sync="adapter_required",
            principal_sync="adapter_required",
            worktrace_sync="adapter_required",
            artifact_sync="adapter_required",
            backup_import="adapter_required",
            backup_create="adapter_required",
            restore="adapter_required",
            browser_fallback="adapter_required",
            version=(self.runtime.metadata_json or {}).get("version"),
        )

    async def collect(self, job: CollectionJob) -> AdapterCollectionResult:
        return AdapterCollectionResult(
            adapter_name=self.adapter_name,
            provider=self.runtime.provider,
            capabilities=await self.list_capabilities(),
        )
