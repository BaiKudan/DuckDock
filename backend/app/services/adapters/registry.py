from __future__ import annotations

from app.models.control_plane import RuntimeInstance, RuntimeProvider
from app.services.adapters.base import BaseRuntimeAdapter
from app.services.adapters.generic import GenericRuntimeAdapter
from app.services.adapters.openclaw import OpenClawAdapter


def get_runtime_adapter(runtime: RuntimeInstance) -> BaseRuntimeAdapter:
    if runtime.provider == RuntimeProvider.OPENCLAW:
        return OpenClawAdapter(runtime)
    return GenericRuntimeAdapter(runtime)
