from __future__ import annotations

from typing import Any


class DatasetReplayTargetError(ValueError):
    pass


class DatasetReplayTargetAdapter:
    """Replay provider-hosted actual output without copying it into DuckDock."""

    TARGET_TYPE = "dataset-replay"
    TARGET_REF = "provider://dataset-replay"

    def execute(
        self,
        *,
        target_type: str,
        target_ref: str,
        target_digest: str,
        item_input: Any,
        item_metadata: dict[str, Any] | None,
    ) -> Any:
        del target_digest, item_metadata
        if (
            target_type != self.TARGET_TYPE
            or target_ref != self.TARGET_REF
        ):
            raise DatasetReplayTargetError(
                "No execution adapter is registered for this target"
            )
        if not isinstance(item_input, dict) or "actual_output" not in item_input:
            raise DatasetReplayTargetError(
                "Dataset replay item does not contain an actual output"
            )
        return item_input["actual_output"]
