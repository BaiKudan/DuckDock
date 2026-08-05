#!/usr/bin/env bash
# Encrypt, sign and optionally upload a matched DuckDock backup set.
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: seal-backup.sh --mysql <sql.gz> --repos <tar.gz> --minio <tar.gz>
  --output-dir <directory> --timestamp <YYYYmmdd-HHMMSS> --release-commit <40hex>
  --age-recipient <age1...> --signing-key <ssh-private-key>
  [--remote-s3-uri s3://bucket/prefix]
EOF
}

TASK_MYSQL=""
TASK_REPOS=""
TASK_MINIO=""
TASK_OUTPUT_DIR=""
TASK_TIMESTAMP=""
TASK_RELEASE_COMMIT=""
TASK_AGE_RECIPIENT=""
TASK_SIGNING_KEY=""
TASK_REMOTE_S3_URI=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --mysql) TASK_MYSQL="${2:-}"; shift 2 ;;
    --repos) TASK_REPOS="${2:-}"; shift 2 ;;
    --minio) TASK_MINIO="${2:-}"; shift 2 ;;
    --output-dir) TASK_OUTPUT_DIR="${2:-}"; shift 2 ;;
    --timestamp) TASK_TIMESTAMP="${2:-}"; shift 2 ;;
    --release-commit) TASK_RELEASE_COMMIT="${2:-}"; shift 2 ;;
    --age-recipient) TASK_AGE_RECIPIENT="${2:-}"; shift 2 ;;
    --signing-key) TASK_SIGNING_KEY="${2:-}"; shift 2 ;;
    --remote-s3-uri) TASK_REMOTE_S3_URI="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
done

for file in "$TASK_MYSQL" "$TASK_REPOS" "$TASK_MINIO" "$TASK_SIGNING_KEY"; do
  if [ -z "$file" ] || [ ! -r "$file" ]; then
    echo "Missing readable backup/signing input: ${file:-<empty>}" >&2
    exit 2
  fi
done
if [ -z "$TASK_OUTPUT_DIR" ] || [ ! -d "$TASK_OUTPUT_DIR" ]; then
  echo "Output directory must already exist: ${TASK_OUTPUT_DIR:-<empty>}" >&2
  exit 2
fi
if ! printf '%s' "$TASK_TIMESTAMP" | grep -Eq '^[0-9]{8}-[0-9]{6}$'; then
  echo "Invalid backup timestamp" >&2
  exit 2
fi
if ! printf '%s' "$TASK_RELEASE_COMMIT" | grep -Eq '^[0-9a-f]{40}$'; then
  echo "Invalid release commit" >&2
  exit 2
fi
if ! printf '%s' "$TASK_AGE_RECIPIENT" | grep -Eq '^age1[0-9a-z]+$'; then
  echo "Invalid age recipient" >&2
  exit 2
fi
if [ -n "$TASK_REMOTE_S3_URI" ] && ! printf '%s' "$TASK_REMOTE_S3_URI" | grep -Eq '^s3://[^/]+(/.*)?$'; then
  echo "Remote backup URI must use s3://" >&2
  exit 2
fi
for command in age ssh-keygen python3; do
  command -v "$command" >/dev/null 2>&1 || {
    echo "$command is required" >&2
    exit 2
  }
done

umask 077
TASK_BUNDLE="$TASK_OUTPUT_DIR/duckdock-backup-$TASK_TIMESTAMP"
if [ -e "$TASK_BUNDLE" ]; then
  echo "Refusing to overwrite existing backup bundle: $TASK_BUNDLE" >&2
  exit 2
fi
mkdir "$TASK_BUNDLE"

cleanup_failed_bundle() {
  if [ "${TASK_COMPLETE:-0}" != "1" ]; then
    rm -rf "$TASK_BUNDLE"
  fi
}
trap cleanup_failed_bundle EXIT

age -r "$TASK_AGE_RECIPIENT" -o "$TASK_BUNDLE/mysql.sql.gz.age" "$TASK_MYSQL"
age -r "$TASK_AGE_RECIPIENT" -o "$TASK_BUNDLE/repos.tar.gz.age" "$TASK_REPOS"
age -r "$TASK_AGE_RECIPIENT" -o "$TASK_BUNDLE/minio.tar.gz.age" "$TASK_MINIO"

export TASK_BUNDLE TASK_TIMESTAMP TASK_RELEASE_COMMIT TASK_MYSQL TASK_REPOS TASK_MINIO
python3 - <<'PY'
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

bundle = Path(os.environ["TASK_BUNDLE"])
sources = {
    "mysql.sql.gz.age": Path(os.environ["TASK_MYSQL"]),
    "repos.tar.gz.age": Path(os.environ["TASK_REPOS"]),
    "minio.tar.gz.age": Path(os.environ["TASK_MINIO"]),
}

def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()

artifacts = {}
for encrypted_name, source in sources.items():
    encrypted = bundle / encrypted_name
    artifacts[encrypted_name] = {
        "encrypted_sha256": digest(encrypted),
        "encrypted_size_bytes": encrypted.stat().st_size,
        "plaintext_sha256": digest(source),
        "plaintext_size_bytes": source.stat().st_size,
    }
manifest = {
    "schema_version": "duckdock-secure-backup-v1",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "timestamp": os.environ["TASK_TIMESTAMP"],
    "release_commit": os.environ["TASK_RELEASE_COMMIT"],
    "encryption": "age-x25519",
    "artifacts": artifacts,
}
(bundle / "manifest.json").write_text(
    json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY

ssh-keygen -Y sign -f "$TASK_SIGNING_KEY" -n duckdock-backup "$TASK_BUNDLE/manifest.json"

if [ -n "$TASK_REMOTE_S3_URI" ]; then
  command -v aws >/dev/null 2>&1 || {
    echo "aws CLI is required for offsite upload" >&2
    exit 2
  }
  TASK_REMOTE_TARGET="${TASK_REMOTE_S3_URI%/}/$(basename "$TASK_BUNDLE")/"
  aws s3 cp "$TASK_BUNDLE" "$TASK_REMOTE_TARGET" --recursive --only-show-errors
  aws s3 ls "$TASK_REMOTE_TARGET" --recursive > "$TASK_BUNDLE/remote-upload-receipt.txt"
  aws s3 cp "$TASK_BUNDLE/remote-upload-receipt.txt" "$TASK_REMOTE_TARGET" --only-show-errors
  echo "Offsite upload verified: $TASK_REMOTE_TARGET"
else
  echo "WARNING: bundle is encrypted and signed but remains local; this cannot satisfy the GA offsite-media gate." >&2
fi

TASK_COMPLETE=1
chmod 0700 "$TASK_BUNDLE"
chmod 0600 "$TASK_BUNDLE"/*
echo "$TASK_BUNDLE"
