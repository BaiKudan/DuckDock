"""Scoped path overrides for portable GA evidence verification."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any


_FILE_RESOLUTION_OVERRIDES: ContextVar[
    tuple[Mapping[str, Path], bool] | None
] = ContextVar(
    "duckdock_ga_file_resolution_overrides",
    default=None,
)


@contextmanager
def ga_file_resolution_overrides(
    overrides: Mapping[str, Path],
    *,
    strict: bool = True,
) -> Iterator[None]:
    """Temporarily resolve original evidence paths to verified archive members."""

    token = _FILE_RESOLUTION_OVERRIDES.set((dict(overrides), strict))
    try:
        yield
    finally:
        _FILE_RESOLUTION_OVERRIDES.reset(token)


def ga_file_resolution_override(raw: Any) -> tuple[bool, Path | None]:
    """Return whether an override context handled raw and its verified target."""

    state = _FILE_RESOLUTION_OVERRIDES.get()
    if state is None:
        return False, None
    paths, strict = state
    if isinstance(raw, str) and raw in paths:
        overridden = paths[raw]
        return True, overridden if overridden.is_file() else None
    return (True, None) if strict else (False, None)
