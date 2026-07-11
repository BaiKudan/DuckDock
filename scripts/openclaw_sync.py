from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any

import httpx


STATE_SCHEMA = "duckdock-openclaw-sync-state/v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Incrementally sync DuckDock registry artifacts into a local OpenClaw-compatible cache.",
    )
    parser.add_argument("--base-url", required=True, help="DuckDock API base URL, for example http://127.0.0.1:8001/api/v1")
    parser.add_argument("--token", default="", help="DuckDock bearer token or robot token")
    parser.add_argument(
        "--namespace",
        action="append",
        dest="namespaces",
        required=True,
        help="Namespace to sync. Repeat for multiple namespaces.",
    )
    parser.add_argument("--cache-root", default=".openclaw-cache", help="Local cache root")
    parser.add_argument("--active-root", default=".openclaw-active", help="Activated skill root")
    parser.add_argument("--state-file", default=".openclaw-sync-state.json", help="Incremental sync state file")
    parser.add_argument("--expires-in", type=int, default=900, help="Requested signed URL TTL in seconds")
    parser.add_argument("--all-versions", action="store_true", help="Sync every available version instead of only the latest one")
    parser.add_argument("--production-only", action="store_true", help="Ignore non-production versions during sync")
    parser.add_argument("--no-activate", action="store_true", help="Do not switch active skills after download")
    return parser.parse_args()


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": STATE_SCHEMA, "namespaces": {}}
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("schema_version") != STATE_SCHEMA:
        raise RuntimeError(f"Unsupported state schema: {state.get('schema_version')}")
    state.setdefault("namespaces", {})
    return state


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=True, indent=2), encoding="utf-8")


def auth_headers(token: str) -> dict[str, str]:
    if not token:
        raise RuntimeError("A bearer token or robot token is required")
    return {"Authorization": f"Bearer {token}"}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_json(client: httpx.Client, url: str, headers: dict[str, str], params: dict[str, Any] | None = None) -> dict[str, Any]:
    response = client.get(url, headers=headers, params=params)
    response.raise_for_status()
    return response.json()


def download_if_needed(client: httpx.Client, url: str, target: Path, expected_sha256: str | None) -> bool:
    if target.exists():
        existing_bytes = target.read_bytes()
        if expected_sha256 and sha256_bytes(existing_bytes) == expected_sha256:
            return False

    response = client.get(url)
    response.raise_for_status()
    data = response.content
    if expected_sha256 and sha256_bytes(data) != expected_sha256:
        raise RuntimeError(f"Checksum mismatch for {target.name}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return True


def extract_bundle(bundle_path: Path, extract_root: Path) -> None:
    if extract_root.exists():
        shutil.rmtree(extract_root)
    extract_root.mkdir(parents=True, exist_ok=True)
    with tarfile.open(bundle_path, "r:gz") as tar:
        tar.extractall(extract_root)


def activate_skill(source_dir: Path, target_dir: Path, metadata: dict[str, Any]) -> None:
    if not source_dir.exists():
        raise RuntimeError(f"Extracted skill path not found: {source_dir}")
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="duckdock-activate-", dir=str(target_dir.parent)) as tmp_dir:
        staging = Path(tmp_dir) / target_dir.name
        shutil.copytree(source_dir, staging)
        (staging / ".duckdock-active.json").write_text(
            json.dumps(metadata, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        if target_dir.exists():
            shutil.rmtree(target_dir)
        shutil.move(str(staging), str(target_dir))


def sync_namespace(
    client: httpx.Client,
    *,
    base_url: str,
    headers: dict[str, str],
    namespace: str,
    cache_root: Path,
    active_root: Path,
    state: dict[str, Any],
    expires_in: int,
    include_non_production: bool,
    latest_only: bool,
    activate: bool,
) -> dict[str, int]:
    ns_state = state["namespaces"].setdefault(namespace, {"cursor": None, "active": {}})
    params = {
        "namespace": namespace,
        "include_urls": "true",
        "include_non_production": str(include_non_production).lower(),
        "latest_only": str(latest_only).lower(),
        "expires_in": str(expires_in),
    }
    if ns_state.get("cursor"):
        params["changed_since"] = ns_state["cursor"]

    index = fetch_json(client, f"{base_url}/registry/index", headers, params=params)
    stats = {"downloaded": 0, "activated": 0, "skipped": 0}

    for item in index.get("items", []):
        if not item.get("distribution_ready"):
            stats["skipped"] += 1
            continue
        artifact_url = item.get("artifact_url")
        manifest_url = item.get("manifest_url")
        if not artifact_url or not manifest_url:
            stats["skipped"] += 1
            continue

        version_root = cache_root / namespace / item["skill"] / item["tag"]
        bundle_path = version_root / "bundle.tar.gz"
        extract_root = version_root / "extract"
        manifest_path = version_root / "manifest.json"

        downloaded = download_if_needed(client, artifact_url, bundle_path, item.get("artifact_sha256"))
        manifest_json = fetch_json(client, manifest_url, {})
        version_root.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest_json, ensure_ascii=True, indent=2), encoding="utf-8")
        if downloaded or not extract_root.exists():
            extract_bundle(bundle_path, extract_root)
            stats["downloaded"] += 1

        if not activate:
            continue
        if item["status"] != "production":
            continue

        source_dir = extract_root / item["skill"]
        target_dir = active_root / namespace / item["skill"]
        active_meta = {
            "namespace": namespace,
            "skill": item["skill"],
            "tag": item["tag"],
            "commit_sha": item["commit_sha"],
            "updated_at": item["updated_at"],
            "sync_cursor": item["sync_cursor"],
        }
        activate_skill(source_dir, target_dir, active_meta)
        ns_state["active"][item["skill"]] = active_meta
        stats["activated"] += 1

    ns_state["cursor"] = index.get("next_cursor") or ns_state.get("cursor")
    return stats


def main() -> int:
    args = parse_args()
    cache_root = Path(args.cache_root)
    active_root = Path(args.active_root)
    state_path = Path(args.state_file)
    state = load_state(state_path)
    headers = auth_headers(args.token)
    base_url = args.base_url.rstrip("/")

    with httpx.Client(timeout=120.0) as client:
        summaries: dict[str, dict[str, int]] = {}
        for namespace in args.namespaces:
            summaries[namespace] = sync_namespace(
                client,
                base_url=base_url,
                headers=headers,
                namespace=namespace,
                cache_root=cache_root,
                active_root=active_root,
                state=state,
                expires_in=args.expires_in,
                include_non_production=not args.production_only,
                latest_only=not args.all_versions,
                activate=not args.no_activate,
            )

    save_state(state_path, state)
    print(json.dumps({"schema_version": STATE_SCHEMA, "namespaces": summaries}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"openclaw_sync failed: {exc}", file=sys.stderr)
        raise
