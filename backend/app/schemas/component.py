from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


ComponentStatus = Literal["not_installed", "stopped", "starting", "running", "degraded", "unavailable", "error"]


class ComponentServiceStatus(BaseModel):
    name: str
    container_name: str | None = None
    state: str = "unknown"
    status: str | None = None
    health: str | None = None
    required: bool = True


class ComponentDependencyStatus(BaseModel):
    name: str
    ready: bool
    state: str = "unknown"
    health: str | None = None


class ComponentOut(BaseModel):
    key: str
    name: str
    category: str
    description: str
    status: ComponentStatus
    installed: bool
    enabled: bool
    core_ready: bool
    profile: str
    url: str | None = None
    message: str | None = None
    services: list[ComponentServiceStatus] = Field(default_factory=list)
    dependencies: list[ComponentDependencyStatus] = Field(default_factory=list)
    commands: dict[str, str] = Field(default_factory=dict)
    last_checked_at: datetime


class ComponentActionResult(BaseModel):
    ok: bool
    action: Literal["start", "stop"]
    component: ComponentOut
    output: str | None = None
