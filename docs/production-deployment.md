# DuckDock Production Deployment

This runbook covers the Compose + SOPS/age production target.

## First deploy (quick checklist)

Ordered path for a from-zero deploy; each step links to its detailed section below.

1. **Host prep** — install Docker + Compose v2, `sops`, and `age` on the host.
2. **Age key** — `age-keygen` an identity; put its public recipient in `.sops.yaml` (see [Secrets](#secrets)).
3. **Fill env** — `cp .env.prod.example .env.prod`, set every core `__CHANGE_ME__` (strong, unique; `prod.sh` preflight rejects weak/default values and MinIO key reuse). Leave `DUCKDOCK_ANALYSIS_WORKER_TOKEN` empty for the first core boot unless you already have a valid worker token.
4. **Seal** — `sops --encrypt .env.prod > .env.prod.enc`; `rm -f .env.prod`; commit/ship only `.env.prod.enc`.
5. **TLS** — terminate TLS in front of the frontend container; set `BACKEND_BASE_URL` / `CORS_ORIGINS` / `MINIO_PUBLIC_ENDPOINT` to real HTTPS origins (see [Network](#network)).
6. **Preflight** — `export SOPS_AGE_KEY_FILE=…; bash scripts/prod.sh preflight` (decrypts to a temp file, rejects weak/default values, then exits without starting containers).
7. **Boot** — `bash scripts/prod.sh up` (decrypts → migrates → starts; hard-fails without the key).
8. **Verify** —
   ```bash
   bash scripts/prod.sh status                      # all services healthy
   curl -fsS http://127.0.0.1:${HTTP_PORT:-80}/health    # frontend/nginx entry is up
   ```
   Backend readiness is enforced by the Compose healthcheck against the internal `/readyz`; `scripts/prod.sh status` should show `backend` as healthy.
9. **Analysis worker** (optional) — create a token in `/analysis`, set `DUCKDOCK_ANALYSIS_WORKER_TOKEN`, then run `bash scripts/prod.sh worker` (see [Analysis Worker](#analysis-worker)).
10. **Backup + restore drill** — run `bash scripts/prod.sh backup`, rehearse `scripts/restore.sh` in staging, and record the result in the [Restore Drill Log](#restore-drill-log) before production use.

## Secrets

Production must not use a plaintext `.env.prod` checked into git or left as the source of truth. Keep only `.env.prod.enc` in the repo or deployment bundle.

1. Install `sops` and `age` on the deployment host.
2. Generate an age identity on the deployment host:

```bash
age-keygen -o /etc/duckdock/duckdock-prod-age.txt
age-keygen -y /etc/duckdock/duckdock-prod-age.txt
```

1. Put the public recipient from the second command into `.sops.yaml`.
2. Create and encrypt the production env:

```bash
cp .env.prod.example .env.prod
$EDITOR .env.prod
SOPS_AGE_KEY_FILE=/etc/duckdock/duckdock-prod-age.txt sops --encrypt .env.prod > .env.prod.enc
rm -f .env.prod
```

1. Start DuckDock:

```bash
export SOPS_AGE_KEY_FILE=/etc/duckdock/duckdock-prod-age.txt
bash scripts/prod.sh preflight
bash scripts/prod.sh up
```

`scripts/prod.sh preflight` is the safe first command on a new host: it fails hard if `.env.prod.enc`, `sops`, or an age key is missing, then validates strong production values without starting containers. All production commands decrypt to a temporary `.env.prod` only for the command and remove it on exit.

## Network

Terminate TLS in front of the frontend container and forward HTTP to `${HTTP_PORT:-80}`. The bundled nginx config serves the SPA and reverse proxies `/api/` to backend. It sends HSTS, frame denial, nosniff, referrer policy, and a same-origin CSP. HSTS assumes the public entrypoint is HTTPS.

Set:

```env
BACKEND_BASE_URL=https://your-domain.example.com
CORS_ORIGINS=["https://your-domain.example.com"]
MINIO_PUBLIC_ENDPOINT=your-minio-domain.example.com
```

## Analysis Worker

Core production boot does not require an analysis worker token. After the first admin account is created, create an analysis worker token in the admin UI under `/analysis`, then set:

```env
DUCKDOCK_ANALYSIS_API_BASE=http://backend:8801/api/v1
DUCKDOCK_ANALYSIS_WORKER_TOKEN=<token>
DUCKDOCK_ANALYSIS_MODE=baseline
```

Re-encrypt `.env.prod.enc`, then start the optional worker profile:

```bash
export SOPS_AGE_KEY_FILE=/etc/duckdock/duckdock-prod-age.txt
bash scripts/prod.sh worker
```

Use `DUCKDOCK_ANALYSIS_MODE=auto` or `llm` only after configuring the Qwen/OpenAI-compatible key and model.

Scale throughput by running the optional profile with a decrypted env file:

```bash
export SOPS_AGE_KEY_FILE=/etc/duckdock/duckdock-prod-age.txt
sops -d .env.prod.enc > .env.prod
docker compose -f docker-compose.prod.yml --env-file .env.prod --profile analysis-worker up -d --scale analysis-worker=3 analysis-worker
rm -f .env.prod
```

Using one token with `--scale` creates a homogeneous worker pool. For per-replica disable/audit, create one worker token per replica and run separately named worker services. Watch `/analysis` or `GET /api/v1/analysis/queue/metrics`; `backlog_count > 0` with `online_worker_count = 0` is saturated, and non-zero `expired_lease_count` means leases are being reclaimed by the existing lease/reaper path. Temporal/Prefect is not part of the default deployment; add it only if future analysis becomes a multi-step workflow that needs durable per-step recovery beyond DuckDock's lease protocol.

## Langfuse

To link Clinic evaluations to Langfuse traces:

```env
CLINIC_LANGFUSE_ENABLED=true
LANGFUSE_PUBLIC_KEY=<public-key>
LANGFUSE_SECRET_KEY=<secret-key>
LANGFUSE_BASE_URL=https://cloud.langfuse.com
LANGFUSE_ENVIRONMENT=production
```

New Clinic evaluations persist `clinic_evaluations.trace_id`; the Clinic detail API returns `langfuse_trace_id` and, when the Langfuse client is configured, `langfuse_trace_url`.

## Backup

Create a matched backup set:

```bash
export SOPS_AGE_KEY_FILE=/etc/duckdock/duckdock-prod-age.txt
bash scripts/prod.sh backup
```

This creates three artifacts with the same timestamp:

- `duckdock-mysql-<timestamp>.sql.gz`
- `duckdock-repos-<timestamp>.tar.gz`
- `duckdock-minio-<timestamp>.tar.gz`

Store the three files together. A DB-only backup is incomplete because skill Git repositories and report/package objects live in separate Docker volumes.

## Restore

Restore the matched set:

```bash
export SOPS_AGE_KEY_FILE=/etc/duckdock/duckdock-prod-age.txt
bash scripts/restore.sh \
  --db duckdock-mysql-<timestamp>.sql.gz \
  --repos duckdock-repos-<timestamp>.tar.gz \
  --minio duckdock-minio-<timestamp>.tar.gz
```

The script stops application services, keeps stateful services available, restores MySQL, replaces `repos_data`, replaces `minio_data`, then starts the full production stack.

After restore:

```bash
bash scripts/prod.sh status
curl -fsS http://127.0.0.1:${HTTP_PORT:-80}/health
```

Confirm the backend service is `healthy`; its Compose healthcheck calls the internal backend `/readyz` endpoint. The public nginx entry exposes `/health`, not backend root `/readyz`.

Record each restore drill with timestamp, backup set, operator, result, and any manual remediation.

## Restore Drill Log

| Date | Backup Set | Operator | Result | Notes |
| --- | --- | --- | --- | --- |
| YYYY-MM-DD | `<matched backup set>` | `<operator>` | Pass / Fail | Record restore target, row/object checks, duration, remediation and rollback decision. |

The repository does not ship an environment-specific drill result. Each deployment owner must complete and retain a restore record for its own staging or production-like environment before production use.

## Rotation

To rotate a secret:

1. Decrypt `.env.prod.enc` on a trusted host.
2. Change the secret.
3. Re-encrypt to `.env.prod.enc` with the current age recipient.
4. Run `bash scripts/prod.sh up` to recreate containers with the new environment.

To rotate the age identity, add the new recipient to `.sops.yaml`, re-encrypt `.env.prod.enc`, distribute the new private key to the deployment host, then remove the old recipient in a second re-encryption.
