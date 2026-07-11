# OpenClaw Registry Contract

This document defines the first integration contract between DuckDock and an OpenClaw-side sync client.

## Goals

- DuckDock remains the publishing and governance control plane.
- Git remains the source of truth for authored skill contents.
- MinIO / S3 distributes immutable release bundles.
- OpenClaw consumes signed download URLs and keeps its own local cache and active skill directory.

## Auth

OpenClaw should use a DuckDock bearer token.

Recommended production flow:

- Create a namespace-scoped robot account in DuckDock.
- Give the robot `readonly` role.
- Use that robot token when calling `/api/v1/registry/*`.

The returned signed URLs are short-lived and can be fetched directly without forwarding the DuckDock token to MinIO.

## Index Endpoint

`GET /api/v1/registry/index`

Recommended query parameters for OpenClaw:

- `namespace=<namespace>`
- `include_urls=true`
- `include_non_production=true`
- `latest_only=true`
- `changed_since=<previous next_cursor>`

### Response shape

```json
{
  "schema_version": "duckdock-registry-index/v1",
  "generated_at": "2026-04-07T18:16:01.000000+00:00",
  "changed_since": "2026-04-07T18:10:00.000000+00:00",
  "next_cursor": "2026-04-07T18:16:05.000000+00:00",
  "include_urls": true,
  "expires_in": 900,
  "latest_only": true,
  "include_non_production": true,
  "items": [
    {
      "namespace": "team-a",
      "skill": "summarizer",
      "description": "Summarize user text",
      "tag": "v1.2.0",
      "commit_sha": "abc123...",
      "status": "production",
      "created_at": "2026-04-07T18:12:00.000000+00:00",
      "updated_at": "2026-04-07T18:14:00.000000+00:00",
      "sync_cursor": "2026-04-07T18:14:00.000000+00:00",
      "distribution_ready": true,
      "manifest_path": "registry/namespaces/team-a/skills/summarizer/versions/v1.2.0/manifest.json",
      "artifact_path": "registry/namespaces/team-a/skills/summarizer/versions/v1.2.0/bundle.tar.gz",
      "artifact_sha256": "deadbeef...",
      "artifact_size_bytes": 2048,
      "manifest_url": "https://minio.example/...signed...",
      "artifact_url": "https://minio.example/...signed...",
      "urls_expire_at": "2026-04-07T18:29:00.000000+00:00",
      "skill_metadata": {
        "name": "summarizer",
        "description": "Summarize user text",
        "version": "1.2.0"
      }
    }
  ]
}
```

### OpenClaw sync rules

- Treat `schema_version` as a compatibility contract.
- Persist `next_cursor` and pass it back as `changed_since` on the next sync.
- Download and cache every changed item with `distribution_ready=true`.
- Only activate items with `status=production`.
- If a newer item is `quarantine`, `scanning`, or `rejected`, keep the last active production version until a new production version appears.

## Manifest Endpoint

`GET /api/v1/registry/namespaces/{namespace}/skills/{skill}/versions/{tag}/manifest`

### Response shape

```json
{
  "manifest": {
    "schema_version": "duckdock-registry/v1",
    "generated_at": "2026-04-07T18:16:01.000000+00:00",
    "namespace": "team-a",
    "skill": "summarizer",
    "tag": "v1.2.0",
    "description": "Summarize user text",
    "commit_sha": "abc123...",
    "status": "production",
    "files": [
      "SKILL.md",
      "system_prompt.md",
      "examples/basic.md"
    ],
    "skill_metadata": {
      "name": "summarizer",
      "description": "Summarize user text",
      "version": "1.2.0"
    },
    "artifact": {
      "bucket": "duckdock",
      "object_key": "registry/namespaces/team-a/skills/summarizer/versions/v1.2.0/bundle.tar.gz",
      "filename": "summarizer-v1.2.0.tar.gz",
      "media_type": "application/gzip",
      "sha256": "deadbeef...",
      "size_bytes": 2048
    }
  },
  "manifest_url": "https://minio.example/...signed...",
  "artifact_url": "https://minio.example/...signed...",
  "expires_at": "2026-04-07T18:29:00.000000+00:00",
  "expires_in": 900
}
```

## Activation Layout

Recommended local layout on the OpenClaw side:

```text
.openclaw-cache/
  <namespace>/<skill>/<tag>/
    bundle.tar.gz
    manifest.json
    extract/

.openclaw-active/
  <namespace>/<skill>/
    SKILL.md
    system_prompt.md
    ...
    .duckdock-active.json
```

`extract/` is the immutable cache for a specific version.  
`.openclaw-active/` is the currently activated view used by OpenClaw runtime loading.

## Import Contract

When importing existing OpenClaw skills into DuckDock:

- `SKILL.md` is required.
- `system_prompt.md` is optional.
- Other UTF-8 text files can be uploaded through `extra_files`.
- Binary files are not included in the first importer implementation and are reported as skipped.

## ClawHub-Compatible Package Rules

DuckDock now enforces a stricter ClawHub-style package validation path for compatibility uploads:

- `SKILL.md` must exist at the package root.
- `SKILL.md` front-matter must include:
  - `name`
  - `description`
- `name` must be `lower_snake_case`.
- `version` may be omitted by ClawHub-style publishers:
  - DuckDock injects the published semver into stored metadata when missing.
  - If `version` is present, it must match the published semver.
- `SKILL.md` must include non-empty markdown body content after front-matter.
- `system_prompt.md` is optional.
- File paths must be UTF-8 text and cannot target `.git` or contain `..`.

## ClawHub-Compatible Package Metadata

DuckDock now stores and exposes package-level metadata per version:

- `changelog`
- `tags`
- `fingerprint`
- `fileCount`

These fields are available in:

- `GET /api/v1/skills/{slug}`
- `GET /api/v1/skills/{slug}/versions`
- `GET /api/v1/skills/{slug}/versions/{version}`

`latest` is also treated as a valid version alias for compatibility reads.

## Current Limitations

- Binary files are still skipped by the first importer implementation.
- Activation uses directory copy, not symlink switching, for Windows compatibility.
- Restore semantics now exist on the DuckDock side, but client-side rollback policy is still left to the integrator.
