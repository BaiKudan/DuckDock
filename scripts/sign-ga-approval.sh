#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 --digest <64-hex> --key <ssh-private-key> --output <signature-file>" >&2
}

TASK_DIGEST=""
TASK_KEY=""
TASK_OUTPUT=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --digest) TASK_DIGEST="${2:-}"; shift 2 ;;
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
if [ ! -r "$TASK_KEY" ] || [ -z "$TASK_OUTPUT" ]; then
  usage
  exit 2
fi

TASK_DIR="$(mktemp -d)"
trap 'rm -rf "$TASK_DIR"' EXIT
TASK_STATEMENT="$TASK_DIR/duckdock-ga-statement"
printf 'duckdock-ga-production-authorization-v1:%s\n' "$TASK_DIGEST" > "$TASK_STATEMENT"
ssh-keygen -Y sign -f "$TASK_KEY" -n duckdock-ga "$TASK_STATEMENT"
install -m 0644 "$TASK_STATEMENT.sig" "$TASK_OUTPUT"
echo "Signed DuckDock GA release digest $TASK_DIGEST -> $TASK_OUTPUT"
