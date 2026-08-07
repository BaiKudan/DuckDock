#!/usr/bin/env bash
set -euo pipefail

GITLEAKS_IMAGE='ghcr.io/gitleaks/gitleaks:v8.30.1@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f'

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <new-report.json>" >&2
  exit 2
fi

repo_root="$(git rev-parse --show-toplevel)"
report_path="$1"
report_name="$(basename "$report_path")"
report_parent="$(dirname "$report_path")"

if [[ ! "$report_name" =~ ^[A-Za-z0-9._-]+\.json$ ]]; then
  echo "Report filename must be a simple .json name." >&2
  exit 2
fi
if [[ -e "$report_path" || -L "$report_path" ]]; then
  echo "Report path already exists; refusing overwrite: $report_path" >&2
  exit 2
fi
if [[ ! -f "$repo_root/.gitleaksignore" ]]; then
  echo "Missing reviewed fingerprint baseline: $repo_root/.gitleaksignore" >&2
  exit 2
fi

if [[ ! -d "$report_parent" ]]; then
  echo "Report parent directory must already exist: $report_parent" >&2
  exit 2
fi
report_parent="$(cd "$report_parent" && pwd -P)"
repo_root="$(cd "$repo_root" && pwd -P)"
case "$report_parent/" in
  "$repo_root/"*)
    echo "Gitleaks reports must be written outside the repository." >&2
    exit 2
    ;;
esac

echo "gitleaks_image=$GITLEAKS_IMAGE"
echo "gitleaks_scope=git full-history all-refs (Gitleaks default)"
echo "gitleaks_redaction=100%"

docker run --rm \
  --network none \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --user "$(id -u):$(id -g)" \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  --env HOME=/tmp \
  --env GIT_CONFIG_COUNT=1 \
  --env GIT_CONFIG_KEY_0=safe.directory \
  --env GIT_CONFIG_VALUE_0=/repo \
  --volume "$repo_root:/repo:ro" \
  --volume "$report_parent:/out" \
  --workdir /repo \
  "$GITLEAKS_IMAGE" \
  git /repo \
  --gitleaks-ignore-path /repo/.gitleaksignore \
  --platform github \
  --redact=100 \
  --no-banner \
  --no-color \
  --timeout 600 \
  --report-format json \
  --report-path "/out/$report_name"

test -f "$report_parent/$report_name"
chmod 0600 "$report_parent/$report_name"
