from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import yaml

from app.core.config import settings
from app.core.security import create_signed_token
from app.models.sandbox_validation import SandboxValidationStatus


class SandboxValidationError(Exception):
    pass


@dataclass
class SandboxCheckResult:
    name: str
    status: SandboxValidationStatus
    summary: str
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status.value, "summary": self.summary, "details": self.details}


@dataclass
class SandboxValidationResult:
    status: SandboxValidationStatus
    summary: str
    checks: list[SandboxCheckResult]
    logs: list[str]
    engine: str


@dataclass
class CmdResult:
    name: str
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


class BaseRunner:
    engine = "openclaw-backend"

    def __init__(
        self,
        *,
        slug: str,
        skill_key: str,
        tag: str,
        access_subject: str,
        spec: dict[str, Any],
        network_mode: str,
        workspace_mode: str,
        agent_smoke_enabled: bool,
        agent_smoke_timeout_seconds: int | None,
    ) -> None:
        self.slug = slug
        self.skill_key = skill_key
        self.tag = tag
        self.access_subject = access_subject
        self.spec = spec
        self.network_mode = (network_mode or "registry_only").strip().lower()
        self.workspace_mode = (workspace_mode or "ephemeral").strip().lower()
        self.agent_smoke_enabled = bool(agent_smoke_enabled)
        self.agent_smoke_timeout_seconds = (
            int(agent_smoke_timeout_seconds)
            if isinstance(agent_smoke_timeout_seconds, int) and agent_smoke_timeout_seconds > 0
            else None
        )

    def backend_name(self) -> str:
        raise NotImplementedError

    def preflight_checks(self) -> list[SandboxCheckResult]:
        raise NotImplementedError

    def backend_config(self, runtime: dict[str, Path]) -> dict[str, Any]:
        raise NotImplementedError

    def run(self) -> SandboxValidationResult:
        checks = self.preflight_checks()
        if any(c.status == SandboxValidationStatus.FAILED for c in checks):
            return SandboxValidationResult(SandboxValidationStatus.FAILED, "Sandbox backend prerequisites are not satisfied.", checks, [], self.engine)
        with tempfile.TemporaryDirectory(prefix="duckdock-openclaw-") as temp_dir:
            runtime = self._runtime_paths(Path(temp_dir))
            for key in ("workspace", "skills_dir", "state_dir", "sandbox_root"):
                runtime[key].mkdir(parents=True, exist_ok=True)
            runtime["config_path"].write_text(json.dumps(self._config(runtime), indent=2), encoding="utf-8")
            env = self._env(runtime)
            results = self._run_commands(runtime, env)
            checks.extend(self._runtime_checks(results))
            return SandboxValidationResult(self._overall(checks), self._summary(checks), checks, self._logs(results, runtime["config_path"]), self.engine)

    def _runtime_paths(self, temp_root: Path) -> dict[str, Path]:
        if self.workspace_mode == "cached":
            base = Path(settings.SKILL_SANDBOX_CACHE_ROOT).expanduser() / sanitize(self.slug) / sanitize(self.tag)
            workspace = base
            state_dir = base / ".duckdock-openclaw"
            sandbox_root = base / ".duckdock-sandboxes"
        else:
            workspace = temp_root / "workspace"
            state_dir = temp_root / ".openclaw"
            sandbox_root = temp_root / "sandboxes"
        return {
            "workspace": workspace,
            "skills_dir": workspace / "skills",
            "state_dir": state_dir,
            "sandbox_root": sandbox_root,
            "config_path": state_dir / "openclaw.json",
        }

    def _config(self, runtime: dict[str, Path]) -> dict[str, Any]:
        sandbox = {
            "backend": self.backend_name(),
            "mode": settings.SKILL_SANDBOX_MODE,
            "scope": settings.SKILL_SANDBOX_SCOPE,
            "workspaceAccess": "rw" if self.workspace_mode == "cached" else "none",
            "workspaceRoot": runtime["sandbox_root"].as_posix(),
            self.backend_name(): self.backend_config(runtime),
        }
        config: dict[str, Any] = {"agents": {"defaults": {"workspace": runtime["workspace"].as_posix(), "sandbox": sandbox}}}
        smoke_provider = self._smoke_provider_config()
        if smoke_provider is not None:
            config["agents"]["defaults"]["model"] = {"primary": smoke_provider["model"]}
        return config

    def _env(self, runtime: dict[str, Path]) -> dict[str, str]:
        token = create_signed_token({"sub": self.access_subject, "type": "access"}, expires_delta=timedelta(minutes=5))
        home = str(runtime["state_dir"].parent)
        env = os.environ.copy()
        env.update(
            {
                "HOME": home,
                "USERPROFILE": home,
                "OPENCLAW_HOME": home,
                "OPENCLAW_STATE_DIR": str(runtime["state_dir"]),
                "OPENCLAW_CONFIG_PATH": str(runtime["config_path"]),
                "CLAWHUB_SITE": self._registry_url(),
                "CLAWHUB_REGISTRY": self._registry_url(),
                "CLAWHUB_CONFIG_PATH": str(runtime["state_dir"] / "clawhub-config.json"),
                "CLAWHUB_WORKDIR": str(runtime["workspace"]),
                "CLAWHUB_DISABLE_TELEMETRY": "1",
                "DUCKDOCK_SANDBOX_ACCESS_TOKEN": token,
            }
        )
        smoke_provider = self._smoke_provider_config()
        if smoke_provider is not None:
            env.setdefault("QWEN_API_KEY", smoke_provider["api_key"])
            env.setdefault("MODELSTUDIO_API_KEY", smoke_provider["api_key"])
            env.setdefault("DASHSCOPE_API_KEY", smoke_provider["api_key"])
        return env

    def _run_commands(self, runtime: dict[str, Path], env: dict[str, str]) -> list[CmdResult]:
        version = self.tag[1:] if self.tag.startswith("v") else self.tag
        clawhub = self._resolve_command(settings.SKILL_SANDBOX_CLAWHUB_COMMAND, settings.SKILL_SANDBOX_CLAWHUB_PACKAGE)
        openclaw = self._resolve_command(settings.SKILL_SANDBOX_OPENCLAW_COMMAND, settings.SKILL_SANDBOX_OPENCLAW_PACKAGE)
        commands: list[tuple[str, list[str], int | None]] = [
            ("clawhub_login", self._cmd(clawhub, "login", "--token", env["DUCKDOCK_SANDBOX_ACCESS_TOKEN"], "--no-input"), None),
            ("clawhub_install", self._cmd(clawhub, "install", self.slug, "--version", version, "--dir", str(runtime["skills_dir"]), "--force"), None),
            ("openclaw_sandbox_explain", self._cmd(openclaw, "sandbox", "explain", "--json"), None),
            ("openclaw_skill_info", self._cmd(openclaw, "skills", "info", self.skill_key, "--json"), None),
            ("openclaw_skills_check", self._cmd(openclaw, "skills", "check", "--json"), None),
        ]
        smoke_readiness = self._smoke_readiness()
        if smoke_readiness["ready"]:
            for i, prompt in enumerate(self._smoke_prompts(), start=1):
                commands.append(
                    (
                        f"openclaw_agent_smoke_{i}",
                        self._cmd(
                            openclaw,
                            "agent",
                            "--local",
                            "--session-id",
                            f"duckdock-smoke-{i}",
                            "--message",
                            str(prompt["message"]),
                            "--json",
                        ),
                        self._smoke_timeout(prompt),
                    )
                )
        results: list[CmdResult] = []
        for name, command, timeout_s in commands:
            try:
                done = subprocess.run(command, cwd=runtime["workspace"], env=env, capture_output=True, text=True, timeout=timeout_s or settings.SKILL_SANDBOX_TIMEOUT_SECONDS)
                results.append(CmdResult(name, done.returncode, (done.stdout or "").strip(), (done.stderr or "").strip()))
            except subprocess.TimeoutExpired as exc:
                results.append(
                    CmdResult(
                        name,
                        124,
                        (exc.stdout or "").strip() if isinstance(exc.stdout, str) else "",
                        (exc.stderr or "").strip() if isinstance(exc.stderr, str) else "",
                        True,
                    )
                )
            if results[-1].returncode != 0 and not name.startswith("openclaw_agent_smoke_"):
                break
        return results

    def _runtime_checks(self, results: list[CmdResult]) -> list[SandboxCheckResult]:
        checks: list[SandboxCheckResult] = []
        login = self._find(results, "clawhub_login")
        if login is None:
            return [SandboxCheckResult("openclaw_runtime", SandboxValidationStatus.FAILED, "Sandbox runtime did not execute any OpenClaw validation command.")]
        checks.append(SandboxCheckResult("clawhub_login", SandboxValidationStatus.PASSED if login.returncode == 0 else SandboxValidationStatus.FAILED, "ClawHub authentication succeeded for sandbox validation." if login.returncode == 0 else "ClawHub authentication failed for sandbox validation."))
        install = self._find(results, "clawhub_install")
        if install is None or install.returncode != 0:
            checks.append(SandboxCheckResult("openclaw_install", SandboxValidationStatus.FAILED, "ClawHub could not install the package into the validation workspace.", {"exit_code": install.returncode if install else None}))
            return checks
        checks.append(SandboxCheckResult("openclaw_install", SandboxValidationStatus.PASSED, "ClawHub installed the package into the validation workspace."))

        explain = self._find(results, "openclaw_sandbox_explain")
        explain_json = parse_json(explain.stdout if explain else "")
        backend = extract_backend(explain_json)
        backend_ok = backend == self.backend_name() or backend is None
        checks.append(SandboxCheckResult("openclaw_sandbox_backend", SandboxValidationStatus.PASSED if backend == self.backend_name() else SandboxValidationStatus.FAILED, f"OpenClaw is configured to use the official '{self.backend_name()}' sandbox backend." if backend == self.backend_name() else "OpenClaw sandbox backend did not match the configured backend.", {"backend": backend, "network_mode": self.network_mode, "workspace_mode": self.workspace_mode}))
        checks[-1] = SandboxCheckResult(
            "openclaw_sandbox_backend",
            SandboxValidationStatus.PASSED if backend_ok else SandboxValidationStatus.FAILED,
            (
                f"OpenClaw accepted the configured '{self.backend_name()}' sandbox backend."
                if backend_ok
                else "OpenClaw sandbox backend did not match the configured backend."
            ),
            {"backend": backend, "network_mode": self.network_mode, "workspace_mode": self.workspace_mode},
        )
        checks.append(SandboxCheckResult("sandbox_network_policy", SandboxValidationStatus.PASSED, self._network_summary(), {"requested_mode": self.network_mode, "effective_network": self._docker_network()}))
        checks.append(SandboxCheckResult("sandbox_workspace_policy", SandboxValidationStatus.PASSED, self._workspace_summary(), {"requested_mode": self.workspace_mode, "effective_workspace_access": "rw" if self.workspace_mode == "cached" else "none"}))
        if explain is not None and explain.returncode != 0:
            return checks + [SandboxCheckResult("openclaw_sandbox_explain", SandboxValidationStatus.FAILED, "OpenClaw could not explain the effective sandbox configuration.", {"exit_code": explain.returncode})]

        info = self._find(results, "openclaw_skill_info")
        info_json = parse_json(info.stdout if info else "")
        if info is None or info.returncode != 0 or not info_json or info_json.get("error") == "not found":
            checks.append(SandboxCheckResult("openclaw_skill_discovery", SandboxValidationStatus.FAILED, "OpenClaw did not recognize the installed skill package.", {"exit_code": info.returncode if info else None}))
            return checks
        checks.append(SandboxCheckResult("openclaw_skill_discovery", SandboxValidationStatus.PASSED, "OpenClaw discovered the installed skill package.", {"source": info_json.get("source"), "filePath": info_json.get("filePath")}))
        eligible = bool(info_json.get("eligible"))
        missing = info_json.get("missing") or {}
        bad = any(bool(v) for v in missing.values()) if isinstance(missing, dict) else False
        checks.append(SandboxCheckResult("openclaw_requirements", SandboxValidationStatus.PASSED if eligible and not bad else SandboxValidationStatus.FAILED, "Skill requirements are satisfied inside the OpenClaw runtime." if eligible and not bad else "Skill has unmet requirements inside the OpenClaw runtime.", {"eligible": eligible, "missing": missing}))

        skill_check = self._find(results, "openclaw_skills_check")
        skill_check_json = parse_json(skill_check.stdout if skill_check else "")
        match = None
        if isinstance(skill_check_json, dict) and isinstance(skill_check_json.get("skills"), list):
            match = next((item for item in skill_check_json["skills"] if item.get("name") == self.skill_key), None)
        elif isinstance(skill_check_json, dict) and isinstance(skill_check_json.get("eligible"), list):
            eligible_names = skill_check_json.get("eligible", [])
            if self.skill_key in eligible_names:
                match = {"name": self.skill_key, "eligible": True}
        checks.append(SandboxCheckResult("openclaw_skills_check", SandboxValidationStatus.PASSED if match is not None else SandboxValidationStatus.SKIPPED, "OpenClaw skills check completed for the installed package." if match is not None else "OpenClaw skills check did not return a per-skill record for this package.", match))
        checks.extend(self._smoke_checks(results))
        return checks

    def _smoke_checks(self, results: list[CmdResult]) -> list[SandboxCheckResult]:
        prompts = self._smoke_prompts()
        readiness = self._smoke_readiness()
        if not readiness["ready"]:
            return [
                SandboxCheckResult(
                    "agent_smoke",
                    SandboxValidationStatus.SKIPPED,
                    str(readiness["summary"]),
                    dict(readiness["details"]) if isinstance(readiness["details"], dict) else None,
                )
            ]
        checks: list[SandboxCheckResult] = []
        for i, prompt in enumerate(prompts, start=1):
            result = self._find(results, f"openclaw_agent_smoke_{i}")
            if result is None:
                checks.append(SandboxCheckResult(f"agent_smoke_{i}", SandboxValidationStatus.FAILED, "Smoke prompt command did not run."))
                continue
            output = extract_text(result.stdout)
            status, details = eval_smoke(prompt, output)
            if result.returncode != 0:
                status = SandboxValidationStatus.FAILED
                details["exit_code"] = result.returncode
            if result.timed_out:
                details["timed_out"] = True
            details["output_excerpt"] = output[:500]
            checks.append(SandboxCheckResult(f"agent_smoke_{i}", status, "Agent smoke prompt passed." if status == SandboxValidationStatus.PASSED else "Agent smoke prompt failed.", details))
        return checks

    def _summary(self, checks: list[SandboxCheckResult]) -> str:
        if any(c.status == SandboxValidationStatus.FAILED for c in checks):
            return "OpenClaw sandbox validation found runtime issues."
        if any(c.status == SandboxValidationStatus.PASSED for c in checks):
            return "OpenClaw sandbox validation passed."
        return "OpenClaw sandbox validation was skipped."

    def _logs(self, results: list[CmdResult], config_path: Path) -> list[str]:
        logs = [f"OPENCLAW_CONFIG_PATH={config_path}"]
        for r in results:
            if r.stdout:
                logs.append(f"[{r.name}:stdout]\n{r.stdout}")
            if r.stderr:
                logs.append(f"[{r.name}:stderr]\n{r.stderr}")
        return logs

    def _find(self, results: list[CmdResult], name: str) -> CmdResult | None:
        return next((r for r in results if r.name == name), None)

    def _resolve_command(self, preferred: str, package: str) -> str:
        preferred = preferred.strip()
        if preferred and exists(preferred):
            return preferred
        if exists("npx"):
            return f"{npx_command()} -y {package}"
        return preferred or package

    def _cmd(self, executable: str, *args: str) -> list[str]:
        return [*split(executable), *args]

    def _registry_url(self) -> str:
        return settings.SKILL_SANDBOX_REGISTRY_URL.rstrip("/") if settings.SKILL_SANDBOX_REGISTRY_URL else settings.BACKEND_BASE_URL.rstrip("/")

    def _overall(self, checks: list[SandboxCheckResult]) -> SandboxValidationStatus:
        if any(c.status == SandboxValidationStatus.FAILED for c in checks):
            return SandboxValidationStatus.FAILED
        if any(c.status == SandboxValidationStatus.PASSED for c in checks):
            return SandboxValidationStatus.PASSED
        return SandboxValidationStatus.SKIPPED

    def _docker_network(self) -> str:
        return "none" if self.network_mode in {"offline", "registry_only"} else settings.SKILL_SANDBOX_DOCKER_NETWORK

    def _network_summary(self) -> str:
        if self.network_mode == "offline":
            return "Sandbox runtime is executed without outbound network access."
        if self.network_mode == "registry_only":
            return "Sandbox runtime is executed without outbound network access. Registry access is limited to the pre-install step outside the sandbox."
        return f"Sandbox runtime uses the controlled Docker network '{self._docker_network()}'."

    def _workspace_summary(self) -> str:
        return "Sandbox runtime uses a cached writable workspace to preserve validation artifacts between runs." if self.workspace_mode == "cached" else "Sandbox runtime uses an ephemeral isolated workspace with no direct host workspace access."

    def _smoke_prompts(self) -> list[dict[str, Any]]:
        return [
            prompt
            for prompt in (self.spec.get("smoke_prompts") or [])
            if isinstance(prompt, dict) and str(prompt.get("message") or "").strip()
        ]

    def _smoke_timeout(self, prompt: dict[str, Any]) -> int | None:
        if isinstance(prompt.get("timeout_seconds"), int) and prompt["timeout_seconds"] > 0:
            return int(prompt["timeout_seconds"])
        if self.agent_smoke_timeout_seconds:
            return self.agent_smoke_timeout_seconds
        return None

    def _smoke_provider_config(self) -> dict[str, str] | None:
        api_key = settings.SKILL_GEN_API_KEY.strip()
        if not api_key:
            return None
        model = settings.SKILL_SANDBOX_AGENT_MODEL.strip() or settings.SKILL_GEN_MODEL.strip() or "qwen3.6-plus"
        if "/" not in model:
            model = f"qwen/{model}"
        return {
            "provider": "qwen",
            "api_key": api_key,
            "base_url": settings.SKILL_GEN_BASE_URL.strip() or "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "model": model,
        }

    def _smoke_readiness(self) -> dict[str, Any]:
        prompts = self._smoke_prompts()
        if not prompts:
            return {
                "ready": False,
                "summary": "No smoke prompts are defined in .duckdock/validation.yaml.",
                "details": {"prompt_count": 0},
            }
        if not settings.SKILL_SANDBOX_ENABLE_AGENT_SMOKE:
            return {
                "ready": False,
                "summary": "Agent smoke is globally disabled by SKILL_SANDBOX_ENABLE_AGENT_SMOKE.",
                "details": {"prompt_count": len(prompts), "namespace_enabled": self.agent_smoke_enabled},
            }
        if not self.agent_smoke_enabled:
            return {
                "ready": False,
                "summary": "Agent smoke is disabled by namespace governance policy.",
                "details": {"prompt_count": len(prompts), "policy_key": "sandbox_agent_smoke_enabled"},
            }
        provider = self._smoke_provider_config()
        if provider is None:
            return {
                "ready": False,
                "summary": "Agent smoke is skipped because no OpenClaw model provider credentials are configured.",
                "details": {
                    "accepted_env": ["QWEN_API_KEY", "MODELSTUDIO_API_KEY", "DASHSCOPE_API_KEY"],
                    "recommended_source": "SKILL_GEN_API_KEY",
                },
            }
        return {
            "ready": True,
            "summary": f"Agent smoke will run with {provider['model']}.",
            "details": {"prompt_count": len(prompts), "provider": provider["provider"], "model": provider["model"]},
        }


class DockerRunner(BaseRunner):
    engine = "openclaw-docker-backend"

    def backend_name(self) -> str:
        return "docker"

    def preflight_checks(self) -> list[SandboxCheckResult]:
        checks = [
            SandboxCheckResult("docker_available", SandboxValidationStatus.PASSED if exists("docker") else SandboxValidationStatus.FAILED, "Docker is available for OpenClaw sandbox execution." if exists("docker") else "Docker is not installed or not on PATH.")
        ]
        if settings.SKILL_SANDBOX_DOCKER_IMAGE.strip():
            checks.append(SandboxCheckResult("sandbox_backend", SandboxValidationStatus.PASSED, "DuckDock will validate with the OpenClaw official Docker sandbox backend.", {"backend": "docker", "image": settings.SKILL_SANDBOX_DOCKER_IMAGE, "network_mode": self.network_mode, "workspace_mode": self.workspace_mode}))
        else:
            checks.append(SandboxCheckResult("sandbox_backend", SandboxValidationStatus.FAILED, "SKILL_SANDBOX_DOCKER_IMAGE is required for the Docker sandbox backend."))
        return checks

    def backend_config(self, runtime: dict[str, Path]) -> dict[str, Any]:
        config: dict[str, Any] = {"image": settings.SKILL_SANDBOX_DOCKER_IMAGE, "network": self._docker_network()}
        if self.workspace_mode == "cached":
            config["binds"] = [{"source": runtime["workspace"].as_posix(), "target": "/workspace", "readOnly": False}]
        return config


class SshRunner(BaseRunner):
    engine = "openclaw-ssh-backend"

    def backend_name(self) -> str:
        return "ssh"

    def preflight_checks(self) -> list[SandboxCheckResult]:
        return [
            SandboxCheckResult("ssh_target", SandboxValidationStatus.PASSED if settings.SKILL_SANDBOX_SSH_TARGET.strip() else SandboxValidationStatus.FAILED, "DuckDock will validate with the OpenClaw official SSH sandbox backend." if settings.SKILL_SANDBOX_SSH_TARGET.strip() else "SKILL_SANDBOX_SSH_TARGET is required for the SSH sandbox backend.", {"target": settings.SKILL_SANDBOX_SSH_TARGET or None}),
            SandboxCheckResult("ssh_available", SandboxValidationStatus.PASSED if exists("ssh") else SandboxValidationStatus.FAILED, "OpenSSH client is available for the SSH sandbox backend." if exists("ssh") else "OpenSSH client is not installed or not on PATH."),
        ]

    def backend_config(self, runtime: dict[str, Path]) -> dict[str, Any]:
        config: dict[str, Any] = {
            "target": settings.SKILL_SANDBOX_SSH_TARGET,
            "workspaceRoot": settings.SKILL_SANDBOX_SSH_WORKSPACE_ROOT,
            "strictHostKeyChecking": settings.SKILL_SANDBOX_SSH_STRICT_HOST_KEY_CHECKING,
            "updateHostKeys": settings.SKILL_SANDBOX_SSH_UPDATE_HOST_KEYS,
        }
        if settings.SKILL_SANDBOX_SSH_USER.strip():
            config["user"] = settings.SKILL_SANDBOX_SSH_USER
        if settings.SKILL_SANDBOX_SSH_PORT:
            config["port"] = settings.SKILL_SANDBOX_SSH_PORT
        if settings.SKILL_SANDBOX_SSH_IDENTITY_FILE.strip():
            config["identityFile"] = settings.SKILL_SANDBOX_SSH_IDENTITY_FILE
        if settings.SKILL_SANDBOX_SSH_KNOWN_HOSTS_FILE.strip():
            config["knownHostsFile"] = settings.SKILL_SANDBOX_SSH_KNOWN_HOSTS_FILE
        return config


class SandboxValidationService:
    def check_readiness(self) -> dict[str, Any]:
        if not settings.SKILL_SANDBOX_ENABLED:
            return {
                "ready": True,
                "enabled": False,
                "backend": "disabled",
                "checks": [
                    SandboxCheckResult(
                        "sandbox_disabled",
                        SandboxValidationStatus.SKIPPED,
                        "Sandbox validation is disabled by configuration.",
                    ).to_dict()
                ],
                "missing": [],
            }

        try:
            runner = self._runner(
                slug="readiness",
                skill_key="readiness",
                tag="v0",
                access_subject="readiness",
                spec={"smoke_prompts": []},
                network_mode=settings.SKILL_SANDBOX_DOCKER_NETWORK,
                workspace_mode="ephemeral",
                agent_smoke_enabled=False,
                agent_smoke_timeout_seconds=None,
            )
            checks = runner.preflight_checks()
        except SandboxValidationError as exc:
            checks = [
                SandboxCheckResult(
                    "sandbox_backend",
                    SandboxValidationStatus.FAILED,
                    str(exc),
                    {"backend": settings.SKILL_SANDBOX_BACKEND},
                )
            ]

        checks.extend(
            [
                SandboxCheckResult(
                    "clawhub_command",
                    SandboxValidationStatus.PASSED
                    if exists(settings.SKILL_SANDBOX_CLAWHUB_COMMAND)
                    else SandboxValidationStatus.FAILED,
                    "ClawHub command is available."
                    if exists(settings.SKILL_SANDBOX_CLAWHUB_COMMAND)
                    else "ClawHub command is not installed or not on PATH.",
                    {"command": settings.SKILL_SANDBOX_CLAWHUB_COMMAND},
                ),
                SandboxCheckResult(
                    "openclaw_command",
                    SandboxValidationStatus.PASSED
                    if exists(settings.SKILL_SANDBOX_OPENCLAW_COMMAND)
                    else SandboxValidationStatus.FAILED,
                    "OpenClaw command is available."
                    if exists(settings.SKILL_SANDBOX_OPENCLAW_COMMAND)
                    else "OpenClaw command is not installed or not on PATH.",
                    {"command": settings.SKILL_SANDBOX_OPENCLAW_COMMAND},
                ),
            ]
        )
        ready = not any(check.status == SandboxValidationStatus.FAILED for check in checks)
        return {
            "ready": ready,
            "enabled": True,
            "backend": settings.SKILL_SANDBOX_BACKEND,
            "checks": [check.to_dict() for check in checks],
            "missing": [check.name for check in checks if check.status == SandboxValidationStatus.FAILED],
        }

    def validate_version(
        self,
        *,
        namespace: str,
        skill_name: str,
        slug: str,
        tag: str,
        files: dict[str, str],
        access_subject: str,
        network_mode: str = "registry_only",
        workspace_mode: str = "ephemeral",
        agent_smoke_enabled: bool = False,
        agent_smoke_timeout_seconds: int | None = None,
    ) -> SandboxValidationResult:
        if not settings.SKILL_SANDBOX_ENABLED:
            return SandboxValidationResult(SandboxValidationStatus.SKIPPED, "Sandbox validation is disabled.", [SandboxCheckResult("sandbox_disabled", SandboxValidationStatus.SKIPPED, "Sandbox validation is disabled by configuration.")], [], "sandbox-disabled")
        spec = load_spec(files)
        checks = package_checks(files, spec)
        if any(c.status == SandboxValidationStatus.FAILED for c in checks):
            return SandboxValidationResult(SandboxValidationStatus.FAILED, "Package checks failed before runtime validation.", checks, [], "package-preflight")
        runner = self._runner(
            slug=slug,
            skill_key=str((spec.get("load") or {}).get("skill_name") or skill_name),
            tag=tag,
            access_subject=access_subject,
            spec=spec,
            network_mode=network_mode,
            workspace_mode=workspace_mode,
            agent_smoke_enabled=agent_smoke_enabled,
            agent_smoke_timeout_seconds=agent_smoke_timeout_seconds,
        )
        runtime = runner.run()
        checks.extend(runtime.checks)
        return SandboxValidationResult(runner._overall(checks), runtime.summary, checks, runtime.logs, runtime.engine)

    def _runner(self, **kwargs: Any) -> BaseRunner:
        backend = (settings.SKILL_SANDBOX_BACKEND or "docker").strip().lower()
        if backend == "docker":
            return DockerRunner(**kwargs)
        if backend == "ssh":
            return SshRunner(**kwargs)
        raise SandboxValidationError(f"Unsupported sandbox backend '{settings.SKILL_SANDBOX_BACKEND}'.")


def load_spec(files: dict[str, str]) -> dict[str, Any]:
    raw = files.get(".duckdock/validation.yaml")
    if not raw:
        return {"checks": [], "smoke_prompts": [], "load": {}}
    parsed = yaml.safe_load(raw) or {}
    if not isinstance(parsed, dict):
        raise SandboxValidationError(".duckdock/validation.yaml must contain a YAML object")
    return parsed


def package_checks(files: dict[str, str], spec: dict[str, Any]) -> list[SandboxCheckResult]:
    checks: list[SandboxCheckResult] = []
    for item in spec.get("checks") or []:
        if not isinstance(item, dict):
            checks.append(SandboxCheckResult("invalid_check", SandboxValidationStatus.FAILED, "Validation spec contains a non-object check entry."))
            continue
        check_type = str(item.get("type") or "").strip()
        path = str(item.get("path") or "").strip()
        if check_type == "file_exists":
            checks.append(SandboxCheckResult(f"file_exists:{path}", SandboxValidationStatus.PASSED if path in files else SandboxValidationStatus.FAILED, f"Found required file '{path}'." if path in files else f"Missing required file '{path}'.")) 
        elif check_type == "file_contains":
            text = str(item.get("text") or "")
            if path not in files:
                checks.append(SandboxCheckResult(f"file_contains:{path}", SandboxValidationStatus.FAILED, f"Cannot search missing file '{path}'.")) 
            elif text and text in files[path]:
                checks.append(SandboxCheckResult(f"file_contains:{path}", SandboxValidationStatus.PASSED, f"Found expected text in '{path}'.", {"text": text})) 
            else:
                checks.append(SandboxCheckResult(f"file_contains:{path}", SandboxValidationStatus.FAILED, f"Expected text was not found in '{path}'.", {"text": text})) 
        else:
            checks.append(SandboxCheckResult(f"unsupported:{check_type or 'unknown'}", SandboxValidationStatus.SKIPPED, f"Unsupported validation check type '{check_type or 'unknown'}'.")) 
    return checks


def eval_smoke(prompt: Any, output: str) -> tuple[SandboxValidationStatus, dict[str, Any]]:
    if not isinstance(prompt, dict):
        return SandboxValidationStatus.FAILED, {"error": "Smoke prompt entry must be an object."}
    normalized = output.lower()
    expected = [str(x) for x in (prompt.get("expect_contains") or []) if str(x).strip()]
    expected_any = [str(x) for x in (prompt.get("expect_any_contains") or []) if str(x).strip()]
    rejected = [str(x) for x in (prompt.get("reject_contains") or []) if str(x).strip()]
    missing = [x for x in expected if x.lower() not in normalized]
    any_match = True if not expected_any else any(x.lower() in normalized for x in expected_any)
    hits = [x for x in rejected if x.lower() in normalized]
    status = SandboxValidationStatus.PASSED if not missing and any_match and not hits else SandboxValidationStatus.FAILED
    return status, {"missing_expected": missing, "expected_any": expected_any, "expected_any_matched": any_match, "forbidden_hits": hits}


def parse_json(raw: str) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else {"value": value}


def extract_backend(payload: dict[str, Any] | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    candidates = [
        payload.get("backend"),
        ((payload.get("sandbox") or {}).get("backend") if isinstance(payload.get("sandbox"), dict) else None),
        (((payload.get("effective") or {}).get("sandbox") or {}).get("backend") if isinstance(payload.get("effective"), dict) else None),
        (((payload.get("resolved") or {}).get("sandbox") or {}).get("backend") if isinstance(payload.get("resolved"), dict) else None),
    ]
    return next((c for c in candidates if isinstance(c, str) and c.strip()), None)


def extract_text(raw: str) -> str:
    payload = parse_json(raw)
    if payload is None:
        return raw.strip()
    strings: list[str] = []
    collect_strings(payload, strings)
    return "\n".join(strings).strip() if strings else json.dumps(payload, ensure_ascii=False)


def collect_strings(value: Any, out: list[str]) -> None:
    if isinstance(value, str):
        if value.strip():
            out.append(value.strip())
        return
    if isinstance(value, list):
        for item in value:
            collect_strings(item, out)
        return
    if isinstance(value, dict):
        for key in ("reply", "text", "message", "content", "output", "final"):
            if key in value:
                collect_strings(value[key], out)
        for item in value.values():
            collect_strings(item, out)


def exists(command: str) -> bool:
    import shutil

    parts = split(command)
    return bool(parts and shutil.which(parts[0]))


def split(command: str) -> list[str]:
    return shlex.split(command, posix=os.name != "nt")


def npx_command() -> str:
    return "npx.cmd" if os.name == "nt" else "npx"


def sanitize(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    return clean or "unnamed"


sandbox_validation_service = SandboxValidationService()
