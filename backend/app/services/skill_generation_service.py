import json
import re
from dataclasses import dataclass

import httpx
import yaml

from app.core.config import resolve_llm
from app.services.llm_http import LLMConfigError, validate_llm_base_url
from app.services.skill_package_template_service import build_skill_package_template


class SkillGenerationError(Exception):
    pass


@dataclass
class GeneratedSkillDraft:
    description: str | None
    package_files: dict[str, str]
    changelog: str | None
    publish_tags: list[str]
    model: str


class SkillGenerationService:
    async def generate_skill_draft(
        self,
        *,
        namespace: str,
        skill_name: str,
        description: str | None,
        tag: str,
        user_prompt: str,
        template_key: str | None = None,
    ) -> GeneratedSkillDraft:
        # SKILL_GEN_* 优先,缺失回落规范 DUCKDOCK_LLM_*(L2-PROVIDER-UNIFY)。
        api_key, raw_base_url, model = resolve_llm("SKILL_GEN")
        if not api_key:
            raise SkillGenerationError("AI skill generation is not configured")

        # 纵深防御:拒绝指向私网/环回/非 http(s) 的 base_url(见 llm_http）。
        try:
            base_url = validate_llm_base_url(raw_base_url)
        except LLMConfigError as exc:
            raise SkillGenerationError(f"Skill generation base_url is invalid: {exc}") from exc

        payload = {
            "model": model,
            "temperature": 0.3,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You generate production-ready DuckDock/OpenClaw skill packages. "
                        "Return strict JSON only. "
                        "The JSON must contain: description, changelog, publish_tags, package_files. "
                        "package_files must be an object mapping file paths to UTF-8 markdown/text contents. "
                        "Always include SKILL.md. Prefer also generating README.md, examples/quickstart.md, "
                        ".duckdock/validation.yaml, and system_prompt.md when helpful. "
                        "SKILL.md must start with YAML front-matter wrapped in --- lines. "
                        "Include required fields name, version, description. Use the exact provided skill name and tag. "
                        "Do not include dangerous shell execution, prompt injection, or credential exfiltration patterns."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Namespace: {namespace}\n"
                        f"Skill name: {skill_name}\n"
                        f"Description hint: {description or ''}\n"
                        f"Version tag: {tag}\n"
                        f"User intent:\n{user_prompt}\n\n"
                        "Return JSON with this shape:\n"
                        "{"
                        '"description": "short description", '
                        '"changelog": "release notes", '
                        '"publish_tags": ["latest"], '
                        '"package_files": {'
                        '"SKILL.md": "full markdown", '
                        '"README.md": "optional readme", '
                        '"examples/quickstart.md": "optional example", '
                        '"system_prompt.md": "optional system prompt", '
                        '".duckdock/validation.yaml": "optional validation yaml"'
                        "}"
                        "}"
                    ),
                },
            ],
        }

        try:
            timeout = httpx.Timeout(120.0, connect=30.0)
            async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
                response = await client.post(
                    "/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SkillGenerationError(f"Skill generation request failed: {exc}") from exc

        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            parsed = self._extract_json(content)
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise SkillGenerationError("Skill generation response was not valid JSON") from exc

        normalized_description = (parsed.get("description") or description or "").strip() or None
        normalized_changelog = (parsed.get("changelog") or f"Initial AI-generated draft for {tag}").strip() or None
        normalized_tags = self._normalize_publish_tags(parsed.get("publish_tags"))
        package_files = self._normalize_package_files(
            parsed,
            skill_name=skill_name,
            tag=tag,
            description=normalized_description or f"{skill_name} generated skill",
            user_prompt=user_prompt,
            template_key=template_key,
        )

        return GeneratedSkillDraft(
            description=normalized_description,
            package_files=package_files,
            changelog=normalized_changelog,
            publish_tags=normalized_tags,
            model=model,
        )

    def _extract_json(self, text: str) -> dict:
        stripped = text.strip()
        if stripped.startswith("```"):
            stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", stripped, flags=re.S).strip()
        return json.loads(stripped)

    def _normalize_publish_tags(self, raw_tags) -> list[str]:
        values = raw_tags if isinstance(raw_tags, list) else []
        cleaned: list[str] = []
        for item in values:
            value = str(item).strip().lower()
            if not value:
                continue
            if re.match(r"^[a-z0-9][a-z0-9._-]{0,63}$", value):
                cleaned.append(value)
        cleaned = sorted(set(cleaned))
        return cleaned or ["latest"]

    def _normalize_package_files(
        self,
        parsed: dict,
        *,
        skill_name: str,
        tag: str,
        description: str,
        user_prompt: str,
        template_key: str | None,
    ) -> dict[str, str]:
        normalized_files: dict[str, str] = {}
        if template_key:
            normalized_files.update(build_skill_package_template(template_key, skill_name=skill_name))

        raw_files = parsed.get("package_files")
        package_files = raw_files if isinstance(raw_files, dict) else {}
        for path, content in package_files.items():
            clean_path = str(path).replace("\\", "/").strip().strip("/")
            if not clean_path:
                continue
            normalized_files[clean_path] = str(content).strip() + "\n"

        skill_md = normalized_files.get("SKILL.md") or parsed.get("skill_md") or ""
        normalized_files["SKILL.md"] = self._normalize_skill_md(
            str(skill_md),
            skill_name=skill_name,
            tag=tag,
            description=description,
        )

        system_prompt = normalized_files.get("system_prompt.md") or parsed.get("system_prompt") or ""
        if system_prompt.strip():
            normalized_files["system_prompt.md"] = system_prompt.strip() + "\n"

        if not normalized_files.get("README.md"):
            normalized_files["README.md"] = self._default_readme(
                skill_name=skill_name,
                description=description,
            )

        if not normalized_files.get("examples/quickstart.md"):
            normalized_files["examples/quickstart.md"] = self._default_example(skill_name=skill_name)

        if not normalized_files.get(".duckdock/validation.yaml"):
            normalized_files[".duckdock/validation.yaml"] = self._default_validation_yaml(
                skill_name=skill_name,
                user_prompt=user_prompt,
            )

        return dict(sorted(normalized_files.items(), key=lambda item: item[0]))

    def _normalize_skill_md(self, value: str, *, skill_name: str, tag: str, description: str) -> str:
        text = value.strip()
        if not text.startswith("---"):
            default_body = (
                "# "
                + skill_name.replace("_", " ").title()
                + "\n\nDescribe what this skill does and when to use it."
            )
            text = (
                f"---\n"
                f"name: {skill_name}\n"
                f"version: {tag.lstrip('v')}\n"
                f"description: {description}\n"
                f"---\n\n"
                f"{text or default_body}"
            )

        lines = text.splitlines()
        if len(lines) >= 2 and lines[0].strip() == "---":
            in_front = True
            frontmatter: list[str] = []
            body_start = None
            for index, line in enumerate(lines[1:], start=1):
                if in_front and line.strip() == "---":
                    body_start = index + 1
                    break
                frontmatter.append(line)

            mapping: dict[str, str] = {}
            for line in frontmatter:
                if ":" in line:
                    key, _, raw_value = line.partition(":")
                    mapping[key.strip()] = raw_value.strip()
            mapping["name"] = skill_name
            mapping["version"] = tag.lstrip("v")
            mapping["description"] = mapping.get("description") or description

            rebuilt = ["---"] + [f"{key}: {value}" for key, value in mapping.items()] + ["---"]
            body = lines[body_start:] if body_start is not None else []
            return "\n".join(rebuilt + [""] + body).strip() + "\n"

        return text + "\n"

    def _default_readme(self, *, skill_name: str, description: str) -> str:
        title = skill_name.replace("_", " ").replace("-", " ").title()
        return (
            f"# {title}\n\n"
            f"{description}\n\n"
            "## Files\n\n"
            "- `SKILL.md`: OpenClaw skill definition and usage guidance.\n"
            "- `system_prompt.md`: Optional system prompt used by the skill runtime.\n"
            "- `examples/quickstart.md`: Starter usage example.\n"
            "- `.duckdock/validation.yaml`: Runtime validation hints for DuckDock sandbox checks.\n"
        )

    def _default_example(self, *, skill_name: str) -> str:
        return (
            f"# Quickstart for `{skill_name}`\n\n"
            "## Example Request\n\n"
            f"> Use the `{skill_name}` skill to help with this task.\n\n"
            "## Expected Behavior\n\n"
            "- The skill should recognize when it applies.\n"
            "- It should follow the guidance in `SKILL.md`.\n"
            "- It should avoid destructive or unsafe actions unless explicitly requested.\n"
        )

    def _default_validation_yaml(self, *, skill_name: str, user_prompt: str) -> str:
        payload = {
            "version": 1,
            "runner": "openclaw-docker",
            "load": {"skill_name": skill_name},
            "checks": [
                {"type": "file_exists", "path": "SKILL.md"},
                {"type": "file_exists", "path": "README.md"},
                {"type": "file_exists", "path": "examples/quickstart.md"},
                {"type": "file_contains", "path": "SKILL.md", "text": skill_name},
            ],
            "smoke_prompts": [
                {
                    "message": (
                        f"Use the {skill_name} skill if appropriate. "
                        f"Task intent: {user_prompt[:180]}"
                    ),
                    "expect_contains": [skill_name],
                    "reject_contains": ["Skill not found", "not recognized"],
                    "timeout_seconds": 45,
                }
            ],
        }
        return yaml.safe_dump(payload, sort_keys=False, allow_unicode=False)


skill_generation_service = SkillGenerationService()
