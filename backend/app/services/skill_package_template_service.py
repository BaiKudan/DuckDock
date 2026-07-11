from __future__ import annotations


def _skill_title(skill_name: str) -> str:
    return skill_name.replace("_", " ").replace("-", " ").title()


def _basic_skill(skill_name: str) -> dict[str, str]:
    title = _skill_title(skill_name)
    return {
        "SKILL.md": (
            f"---\n"
            f"name: {skill_name}\n"
            f"version: 1.0.0\n"
            f"description: {title} skill\n"
            f"---\n\n"
            f"# {title}\n\n"
            "Explain what this skill does and when to use it.\n"
        ),
        "README.md": (
            f"# {title}\n\n"
            "Document the package intent, constraints, and supported workflow.\n"
        ),
        "examples/quickstart.md": (
            f"# Quickstart\n\n"
            f"> Use the `{skill_name}` skill when this task matches its purpose.\n"
        ),
        ".duckdock/validation.yaml": (
            "version: 1\n"
            "runner: openclaw-docker\n"
            "load:\n"
            f"  skill_name: {skill_name}\n"
            "checks:\n"
            "  - type: file_exists\n"
            "    path: SKILL.md\n"
            "  - type: file_exists\n"
            "    path: README.md\n"
            "smoke_prompts:\n"
            f"  - message: Use the {skill_name} skill if it applies.\n"
            f"    expect_contains:\n"
            f"      - {skill_name}\n"
            "    reject_contains:\n"
            "      - Skill not found\n"
            "      - not recognized\n"
            "    timeout_seconds: 45\n"
        ),
    }


def _workflow_assistant(skill_name: str) -> dict[str, str]:
    files = _basic_skill(skill_name)
    files["system_prompt.md"] = (
        "You are a workflow assistant skill. Break work into clear steps, check constraints, "
        "and produce concise, operational guidance.\n"
    )
    files["examples/workflow.md"] = (
        "# Workflow Example\n\n"
        "1. Confirm the requested outcome.\n"
        "2. Identify required inputs and boundaries.\n"
        "3. Produce an actionable plan.\n"
    )
    return files


def _content_generator(skill_name: str) -> dict[str, str]:
    files = _basic_skill(skill_name)
    files["system_prompt.md"] = (
        "You are a content generation skill. Produce structured output, surface assumptions, "
        "and keep tone consistent with the requested audience.\n"
    )
    files["examples/brief.md"] = (
        "# Content Brief Example\n\n"
        "- Audience\n"
        "- Objective\n"
        "- Constraints\n"
        "- Desired output format\n"
    )
    return files


def list_skill_package_templates() -> list[dict[str, object]]:
    return [
        {
            "key": "basic-skill",
            "name": "Basic Skill",
            "description": "Minimal OpenClaw-compatible package with README, example, and validation spec.",
            "recommended_files": ["SKILL.md", "README.md", "examples/quickstart.md", ".duckdock/validation.yaml"],
            "builder": _basic_skill,
        },
        {
            "key": "workflow-assistant",
            "name": "Workflow Assistant",
            "description": "Package tuned for procedural or operations-oriented skills.",
            "recommended_files": [
                "SKILL.md",
                "README.md",
                "system_prompt.md",
                "examples/quickstart.md",
                "examples/workflow.md",
                ".duckdock/validation.yaml",
            ],
            "builder": _workflow_assistant,
        },
        {
            "key": "content-generator",
            "name": "Content Generator",
            "description": "Package tuned for drafting, rewriting, and content production skills.",
            "recommended_files": [
                "SKILL.md",
                "README.md",
                "system_prompt.md",
                "examples/quickstart.md",
                "examples/brief.md",
                ".duckdock/validation.yaml",
            ],
            "builder": _content_generator,
        },
    ]


def build_skill_package_template(template_key: str, *, skill_name: str) -> dict[str, str]:
    normalized = template_key.strip().lower()
    for item in list_skill_package_templates():
        if item["key"] == normalized:
            builder = item["builder"]
            return builder(skill_name)
    raise ValueError(f"Unknown package template '{template_key}'")
