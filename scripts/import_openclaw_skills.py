from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import httpx


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import a local OpenClaw skill directory tree into DuckDock.",
    )
    parser.add_argument("--base-url", required=True, help="DuckDock API base URL, for example http://127.0.0.1:8001/api/v1")
    parser.add_argument("--token", default="", help="DuckDock bearer token")
    parser.add_argument("--username", default="", help="DuckDock username for login")
    parser.add_argument("--password", default="", help="DuckDock password for login")
    parser.add_argument("--namespace", required=True, help="Destination namespace")
    parser.add_argument("--skills-root", required=True, help="Root directory containing OpenClaw skills")
    parser.add_argument("--create-namespace", action="store_true", help="Create the namespace when missing")
    parser.add_argument("--default-tag", default="v1.0.0", help="Fallback version tag when SKILL.md does not include one")
    parser.add_argument("--message", default="chore: import from openclaw", help="Commit message for published versions")
    parser.add_argument("--skip-existing", action="store_true", help="Skip versions that already exist")
    return parser.parse_args()


def parse_front_matter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    metadata: dict[str, str] = {}
    lines = text.splitlines()
    in_front_matter = False
    for line in lines:
        if line.strip() == "---":
            if not in_front_matter:
                in_front_matter = True
                continue
            break
        if in_front_matter and ":" in line:
            key, _, value = line.partition(":")
            metadata[key.strip()] = value.strip()
    return metadata


def normalize_skill_name(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip().lower()).strip("-")
    if not normalized:
        raise RuntimeError(f"Cannot derive a valid skill name from '{value}'")
    return normalized[:128]


def normalize_tag(raw: str, fallback: str) -> str:
    value = (raw or "").strip()
    if not value:
        value = fallback
    if value.startswith("v"):
        return value
    return f"v{value}"


def read_text_file(path: Path) -> str | None:
    try:
        raw = path.read_bytes()
    except UnicodeDecodeError:
        return None
    try:
        text = raw.decode("utf-8").lstrip("\ufeff")
    except UnicodeDecodeError:
        return None
    disallowed_control = [
        char
        for char in text
        if ord(char) < 32 and char not in {"\n", "\r", "\t"}
    ]
    if disallowed_control:
        return None
    return text


def collect_skill_package(skill_dir: Path, default_tag: str) -> dict[str, Any] | None:
    skill_md_path = skill_dir / "SKILL.md"
    if not skill_md_path.exists():
        return None

    skill_md = read_text_file(skill_md_path)
    if skill_md is None:
        raise RuntimeError(f"SKILL.md is not valid UTF-8: {skill_md_path}")

    metadata = parse_front_matter(skill_md)
    name = normalize_skill_name(metadata.get("name") or skill_dir.name)
    description = metadata.get("description")
    tag = normalize_tag(metadata.get("version", ""), default_tag)

    system_prompt_path = skill_dir / "system_prompt.md"
    system_prompt = None
    if system_prompt_path.exists():
        system_prompt = read_text_file(system_prompt_path)
        if system_prompt is None:
            raise RuntimeError(f"system_prompt.md is not valid UTF-8: {system_prompt_path}")

    extra_files: dict[str, str] = {}
    skipped_binary: list[str] = []
    for file_path in sorted(skill_dir.rglob("*")):
        if not file_path.is_file():
            continue
        relative = file_path.relative_to(skill_dir).as_posix()
        if relative in {"SKILL.md", "system_prompt.md"}:
            continue
        text = read_text_file(file_path)
        if text is None:
            skipped_binary.append(relative)
            continue
        extra_files[relative] = text

    return {
        "name": name,
        "description": description,
        "tag": tag,
        "skill_md": skill_md,
        "system_prompt": system_prompt,
        "extra_files": extra_files,
        "skipped_binary": skipped_binary,
    }


def login_if_needed(client: httpx.Client, base_url: str, args: argparse.Namespace) -> str:
    if args.token:
        return args.token
    if not args.username or not args.password:
        raise RuntimeError("Provide either --token or both --username and --password")
    response = client.post(
        f"{base_url}/auth/login",
        json={"username": args.username, "password": args.password},
    )
    response.raise_for_status()
    return response.json()["access_token"]


def ensure_namespace(client: httpx.Client, base_url: str, headers: dict[str, str], namespace: str, create_namespace: bool) -> None:
    response = client.get(f"{base_url}/namespaces/{namespace}", headers=headers)
    if response.status_code == 200:
        return
    if response.status_code != 404 or not create_namespace:
        response.raise_for_status()
    create = client.post(
        f"{base_url}/namespaces",
        json={"name": namespace, "description": "Imported from OpenClaw"},
        headers=headers,
    )
    create.raise_for_status()


def main() -> int:
    args = parse_args()
    base_url = args.base_url.rstrip("/")
    skills_root = Path(args.skills_root)
    if not skills_root.exists():
        raise RuntimeError(f"Skills root not found: {skills_root}")

    with httpx.Client(timeout=120.0) as client:
        token = login_if_needed(client, base_url, args)
        headers = {"Authorization": f"Bearer {token}"}
        ensure_namespace(client, base_url, headers, args.namespace, args.create_namespace)

        summary = {
            "created_skills": 0,
            "published_versions": 0,
            "skipped_versions": 0,
            "skipped_binary_files": 0,
            "skills": [],
        }

        for child in sorted(skills_root.iterdir()):
            if not child.is_dir():
                continue
            package = collect_skill_package(child, args.default_tag)
            if package is None:
                continue

            create_skill = client.post(
                f"{base_url}/namespaces/{args.namespace}/skills",
                json={"name": package["name"], "description": package["description"]},
                headers=headers,
            )
            if create_skill.status_code == 201:
                summary["created_skills"] += 1
            elif create_skill.status_code != 409:
                create_skill.raise_for_status()

            publish = client.post(
                f"{base_url}/namespaces/{args.namespace}/skills/{package['name']}/versions",
                json={
                    "tag": package["tag"],
                    "skill_md": package["skill_md"],
                    "system_prompt": package["system_prompt"],
                    "extra_files": package["extra_files"],
                    "message": args.message,
                },
                headers=headers,
            )
            if publish.status_code == 201:
                summary["published_versions"] += 1
            elif publish.status_code == 409 and args.skip_existing:
                summary["skipped_versions"] += 1
            else:
                publish.raise_for_status()

            summary["skipped_binary_files"] += len(package["skipped_binary"])
            summary["skills"].append(
                {
                    "name": package["name"],
                    "tag": package["tag"],
                    "extra_files": len(package["extra_files"]),
                    "skipped_binary": package["skipped_binary"],
                }
            )

    print(json.dumps(summary, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"import_openclaw_skills failed: {exc}", file=sys.stderr)
        raise
