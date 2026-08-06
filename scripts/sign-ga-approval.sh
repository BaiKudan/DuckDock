#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 --digest <64-hex> --role <role> --identity <principal> --approved-at <RFC3339> --key <ssh-private-key> --output <signature-file>" >&2
}

TASK_DIGEST=""
TASK_ROLE=""
TASK_IDENTITY=""
TASK_APPROVED_AT=""
TASK_KEY=""
TASK_OUTPUT=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --digest) TASK_DIGEST="${2:-}"; shift 2 ;;
    --role) TASK_ROLE="${2:-}"; shift 2 ;;
    --identity) TASK_IDENTITY="${2:-}"; shift 2 ;;
    --approved-at) TASK_APPROVED_AT="${2:-}"; shift 2 ;;
    --key) TASK_KEY="${2:-}"; shift 2 ;;
    --output) TASK_OUTPUT="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
done

if ! printf '%s' "$TASK_DIGEST" | grep -Eq '^[0-9a-f]{64}$'; then
  echo "Invalid release digest" >&2
  exit 2
fi
case "$TASK_ROLE" in
  Product|Architecture|Security|Operations) ;;
  *) echo "Invalid approval role" >&2; exit 2 ;;
esac
if [ -z "$TASK_IDENTITY" ] || [ -z "$TASK_APPROVED_AT" ] || [ ! -r "$TASK_KEY" ] || [ -z "$TASK_OUTPUT" ]; then
  usage
  exit 2
fi

TASK_DIR="$(mktemp -d)"
trap 'rm -rf "$TASK_DIR"' EXIT
TASK_STATEMENT="$TASK_DIR/duckdock-ga-statement"
python3 - "$TASK_DIGEST" "$TASK_ROLE" "$TASK_IDENTITY" "$TASK_APPROVED_AT" "$TASK_STATEMENT" <<'PY'
import json
import sys
from datetime import datetime
from pathlib import Path

digest, role, identity, approved_at, output = sys.argv[1:]
try:
    parsed = datetime.fromisoformat(approved_at.replace("Z", "+00:00"))
except ValueError as exc:
    raise SystemExit(f"Invalid approved-at timestamp: {exc}") from exc
if parsed.tzinfo is None:
    raise SystemExit("Invalid approved-at timestamp: timezone is required")
statement = {
    "schema_version": "duckdock-ga-approval-statement-v1",
    "authorization_schema_version": "duckdock-ga-production-authorization-v2",
    "release_digest": digest,
    "role": role,
    "identity": identity,
    "decision": "APPROVED",
    "approved_at": approved_at,
}
Path(output).write_text(
    json.dumps(statement, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n",
    encoding="utf-8",
)
PY
ssh-keygen -Y sign -f "$TASK_KEY" -n duckdock-ga "$TASK_STATEMENT"
install -m 0644 "$TASK_STATEMENT.sig" "$TASK_OUTPUT"
echo "Signed DuckDock GA $TASK_ROLE approval for $TASK_IDENTITY over $TASK_DIGEST -> $TASK_OUTPUT"
