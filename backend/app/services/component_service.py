from __future__ import annotations

import json
import shlex
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import PROJECT_ROOT, settings
from app.schemas.component import (
    ComponentActionResult,
    ComponentDependencyStatus,
    ComponentOut,
    ComponentServiceStatus,
)


class ComponentCommandError(RuntimeError):
    def __init__(self, message: str, output: str | None = None) -> None:
        super().__init__(message)
        self.output = output


@dataclass(frozen=True)
class ComposeService:
    name: str
    required: bool = True
    one_shot: bool = False


class ComponentService:
    langfuse_services = [
        ComposeService("postgres"),
        ComposeService("clickhouse"),
        ComposeService("langfuse-db-init", one_shot=True),
        ComposeService("langfuse-minio-init", one_shot=True),
        ComposeService("langfuse-worker"),
        ComposeService("langfuse-web"),
    ]
    langfuse_start_services = [service.name for service in langfuse_services]
    langfuse_stop_services = ["langfuse-web", "langfuse-worker", "clickhouse", "postgres"]
    core_dependencies = ["mysql", "redis", "minio"]
    profile = "observability"

    def get_langfuse_component(self) -> ComponentOut:
        if not settings.COMPONENT_MANAGER_ENABLED:
            now = datetime.now(timezone.utc)
            return ComponentOut(
                key="langfuse",
                name="Langfuse Observability",
                category="observability",
                description="可选的 LLM Trace、诊断评估和 Prompt 审计组件。",
                status="unavailable",
                installed=False,
                enabled=False,
                core_ready=False,
                profile=self.profile,
                url=settings.COMPONENT_LANGFUSE_URL,
                message="Component manager is disabled by configuration.",
                last_checked_at=now,
            )

        try:
            component_rows = self._compose_ps([service.name for service in self.langfuse_services], with_profile=True)
            core_rows = self._compose_ps(self.core_dependencies, with_profile=False)
        except ComponentCommandError as exc:
            now = datetime.now(timezone.utc)
            return ComponentOut(
                key="langfuse",
                name="Langfuse Observability",
                category="observability",
                description="可选的 LLM Trace、诊断评估和 Prompt 审计组件。",
                status="unavailable",
                installed=False,
                enabled=False,
                core_ready=False,
                profile=self.profile,
                url=settings.COMPONENT_LANGFUSE_URL,
                message=str(exc),
                services=[],
                dependencies=[],
                commands=self._commands(),
                last_checked_at=now,
            )

        dependencies = [self._dependency_status(name, core_rows.get(name)) for name in self.core_dependencies]
        services = [
            self._service_status(service, component_rows.get(service.name)) for service in self.langfuse_services
        ]
        installed = any(row is not None for row in component_rows.values())
        core_ready = all(dependency.ready for dependency in dependencies)
        web_row = component_rows.get("langfuse-web")
        worker_row = component_rows.get("langfuse-worker")
        web_running = self._is_running(web_row)
        worker_running = self._is_running(worker_row)
        web_healthy = self._http_ok(settings.COMPONENT_LANGFUSE_URL) if web_running else False
        any_running = any(self._is_running(row) for row in component_rows.values())

        if not installed:
            status = "not_installed"
            enabled = False
            message = "Langfuse 未安装或从未启动。"
        elif web_running and worker_running and web_healthy:
            status = "running"
            enabled = True
            message = "Langfuse 正在运行。"
        elif any_running and (web_running or worker_running):
            status = "starting"
            enabled = True
            message = "Langfuse 容器已启动，Web 健康检查仍在等待。"
        elif any_running:
            status = "degraded"
            enabled = True
            message = "部分 Langfuse 依赖容器正在运行，但核心服务未就绪。"
        else:
            status = "stopped"
            enabled = False
            message = "Langfuse 已安装但当前未运行。"

        if not core_ready and status in {"not_installed", "stopped"}:
            message = "请先启动 DuckDock 核心服务：MySQL、Redis、MinIO。"

        return ComponentOut(
            key="langfuse",
            name="Langfuse Observability",
            category="observability",
            description="可选的 LLM Trace、诊断评估和 Prompt 审计组件。",
            status=status,
            installed=installed,
            enabled=enabled,
            core_ready=core_ready,
            profile=self.profile,
            url=settings.COMPONENT_LANGFUSE_URL,
            message=message,
            services=services,
            dependencies=dependencies,
            commands=self._commands(),
            last_checked_at=datetime.now(timezone.utc),
        )

    def start_langfuse(self) -> ComponentActionResult:
        component = self.get_langfuse_component()
        if not component.core_ready:
            raise ComponentCommandError("Core DuckDock services are not ready. Start MySQL, Redis and MinIO first.")
        output = self._compose(
            ["up", "-d", *self.langfuse_start_services],
            with_profile=True,
            timeout=settings.COMPONENT_DOCKER_TIMEOUT_SECONDS,
        )
        return ComponentActionResult(
            ok=True,
            action="start",
            component=self.get_langfuse_component(),
            output=output,
        )

    def stop_langfuse(self) -> ComponentActionResult:
        output = self._compose(
            ["stop", *self.langfuse_stop_services],
            with_profile=True,
            timeout=settings.COMPONENT_DOCKER_TIMEOUT_SECONDS,
        )
        return ComponentActionResult(
            ok=True,
            action="stop",
            component=self.get_langfuse_component(),
            output=output,
        )

    def _commands(self) -> dict[str, str]:
        compose_file = settings.COMPONENT_DOCKER_COMPOSE_FILE
        start_services = " ".join(self.langfuse_start_services)
        stop_services = " ".join(self.langfuse_stop_services)
        return {
            "start": f"docker compose -f {compose_file} --profile {self.profile} up -d {start_services}",
            "stop": f"docker compose -f {compose_file} --profile {self.profile} stop {stop_services}",
        }

    def _dependency_status(self, name: str, row: dict[str, Any] | None) -> ComponentDependencyStatus:
        return ComponentDependencyStatus(
            name=name,
            ready=self._is_ready(row),
            state=self._state(row),
            health=self._health(row),
        )

    def _service_status(self, service: ComposeService, row: dict[str, Any] | None) -> ComponentServiceStatus:
        return ComponentServiceStatus(
            name=service.name,
            container_name=self._container_name(row),
            state=self._state(row),
            status=self._status(row),
            health=self._health(row),
            required=service.required,
        )

    def _compose_ps(self, services: list[str], *, with_profile: bool) -> dict[str, dict[str, Any] | None]:
        output = self._compose(["ps", "-a", "--format", "json", *services], with_profile=with_profile, timeout=30)
        rows = self._parse_ps_json(output)
        by_service: dict[str, dict[str, Any] | None] = {service: None for service in services}
        for row in rows:
            service = str(row.get("Service") or row.get("service") or "")
            if service in by_service:
                by_service[service] = row
        return by_service

    def _compose(self, args: list[str], *, with_profile: bool, timeout: int) -> str:
        compose_file = self._resolve_path(settings.COMPONENT_DOCKER_COMPOSE_FILE)
        project_dir = self._resolve_path(settings.COMPONENT_DOCKER_PROJECT_DIRECTORY)
        command = self._docker_compose_command(compose_file, with_profile=with_profile) + args
        try:
            completed = subprocess.run(
                command,
                cwd=project_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise ComponentCommandError("Docker command is not available on this host.") from exc
        except subprocess.TimeoutExpired as exc:
            raise ComponentCommandError("Docker compose command timed out.", output=(exc.stdout or "") + (exc.stderr or "")) from exc

        output = "\n".join(part for part in [completed.stdout.strip(), completed.stderr.strip()] if part)
        if completed.returncode != 0:
            raise ComponentCommandError(output or f"Docker compose exited with code {completed.returncode}.", output=output)
        return output

    def _docker_compose_command(self, compose_file: Path, *, with_profile: bool) -> list[str]:
        base = shlex.split(settings.COMPONENT_DOCKER_COMMAND)
        if not base:
            base = ["docker"]
        command = base if base[-1] == "compose" else [*base, "compose"]
        command.extend(["-f", str(compose_file)])
        if with_profile:
            command.extend(["--profile", self.profile])
        return command

    def _resolve_path(self, value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else PROJECT_ROOT / path

    def _parse_ps_json(self, output: str) -> list[dict[str, Any]]:
        text = output.strip()
        if not text:
            return []
        try:
            data = json.loads(text)
            if isinstance(data, list):
                return [row for row in data if isinstance(row, dict)]
            if isinstance(data, dict):
                return [data]
        except json.JSONDecodeError:
            rows: list[dict[str, Any]] = []
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(data, dict):
                    rows.append(data)
            return rows
        return []

    def _http_ok(self, url: str) -> bool:
        if not url:
            return False
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                return response.status < 500
        except (urllib.error.URLError, TimeoutError, ValueError):
            return False

    def _is_ready(self, row: dict[str, Any] | None) -> bool:
        if not self._is_running(row):
            return False
        health = self._health(row)
        return health in {None, "", "healthy"}

    def _is_running(self, row: dict[str, Any] | None) -> bool:
        return self._state(row).lower() == "running"

    def _state(self, row: dict[str, Any] | None) -> str:
        if row is None:
            return "missing"
        return str(row.get("State") or row.get("state") or "unknown")

    def _health(self, row: dict[str, Any] | None) -> str | None:
        if row is None:
            return None
        value = row.get("Health") or row.get("health")
        return str(value) if value not in {None, ""} else None

    def _status(self, row: dict[str, Any] | None) -> str | None:
        if row is None:
            return None
        value = row.get("Status") or row.get("status")
        return str(value) if value not in {None, ""} else None

    def _container_name(self, row: dict[str, Any] | None) -> str | None:
        if row is None:
            return None
        value = row.get("Name") or row.get("Names") or row.get("name")
        return str(value) if value not in {None, ""} else None


component_service = ComponentService()
