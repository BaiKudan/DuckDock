# DuckDock Production Deployment

This runbook covers the single-host Compose + SOPS/age + bundled TLS production
baseline. Cross-fault-domain production uses `ops/kubernetes/ha` and the final
authorization gate described in `docs/ga-production-authorization.zh-CN.md`.

> **GA boundary:** the single-host Compose topology is a hardened staging and
> recovery reference, not a DuckDock 2.0 GA topology. It cannot satisfy fault-
> domain or managed-data-service requirements. Production authorization requires
> the Kubernetes HA overlay, externally operated HA MySQL/Redis/S3/RWX services,
> target evidence, independent assessment and a `GA_AUTHORIZED` result. Bundled
> MySQL/MinIO images must not be promoted merely because application images pass
> the vulnerability gate.

## First deploy (quick checklist)

Ordered path for a from-zero deploy; each step links to its detailed section below.

1. **Host prep** — install Docker + Compose v2, `sops`, and `age` on the host.
2. **Age key** — `age-keygen` an identity; put its public recipient in `.sops.yaml` (see [Secrets](#secrets)).
3. **Fill env** — `cp .env.prod.example .env.prod`, set every core `__CHANGE_ME__` (strong, unique; `prod.sh` preflight rejects weak/default values and MinIO key reuse). Leave `DUCKDOCK_ANALYSIS_WORKER_TOKEN` empty for the first core boot unless you already have a valid worker token.
4. **Seal** — `sops --encrypt .env.prod > .env.prod.enc`; `rm -f .env.prod`; commit/ship only `.env.prod.enc`.
5. **TLS** — provide one certificate covering the application and object-store hostnames. The bundled TLS gateway is the only public listener (see [Network](#network)).
6. **Alerting** — render a real Alertmanager config outside the repo, configure two HTTPS receivers, and exercise firing plus resolved notifications.
7. **Preflight** — `export SOPS_AGE_KEY_FILE=…; bash scripts/prod.sh preflight` (decrypts to a temp file, validates TLS/key matching, alert receivers, backups and strong settings, then exits without starting containers).
8. **Boot** — `bash scripts/prod.sh up` (decrypts → migrates → starts TLS/application/monitoring; hard-fails without the key).
9. **Verify** —
   ```bash
   bash scripts/prod.sh status                      # all services healthy
   curl -fsS https://${DUCKDOCK_PUBLIC_HOST}/health
   ```
   Backend readiness is enforced by the Compose healthcheck against the internal `/readyz`; `scripts/prod.sh status` should show `backend` as healthy.
10. **Analysis worker** (optional) — create a token in `/analysis`, set `DUCKDOCK_ANALYSIS_WORKER_TOKEN`, then run `bash scripts/prod.sh worker` (see [Analysis Worker](#analysis-worker)).
11. **Backup + restore drill** — run `bash scripts/prod.sh backup`; this age-encrypts, signs and uploads the matched set to the configured offsite S3 URI. Rehearse secure bundle restore in staging before production use.

## Kubernetes HA target bundle

Do not directly edit and apply the HA reference as one document. From a clean
release commit, use `scripts/prepare-kubernetes-ha-target.py` with the immutable
backend/frontend registry digests, production DNS name, externally managed
Secret names, RWX StorageClass and approved provider CIDRs. The command writes
outside the repository and emits exact `bootstrap.yaml`, `migration.yaml` and
`applications.yaml` phases plus a content-addressed receipt.

Run `verify` again at the deployment boundary, server-side dry-run all three
files, and then use `scripts/deploy-kubernetes-ha-target.py`: its preflight binds
the reviewed kube-system UID and authenticated principal, requires three Ready
zones, checks the exact Secret metadata/RWX/metrics/RBAC profile, and performs
all three server dry-runs. A fresh preflight plus an exact content-addressed
mutation confirmation is required before the executor applies bootstrap, waits
for RWX, waits for the commit-named migration, and rolls out all four workloads.
Success and partial failure both create immutable non-authorizing receipts.

The receipts deliberately remain outside GA authorization. Release provenance,
Secret values/rotation, external TLS and network enforcement, managed-state/RWX
redundancy, target capacity, recovery/on-call, fault injection, independent
assessment and four-role authorization are still required. Exact commands are
in `ops/kubernetes/ha/README.zh-CN.md`.

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

For the Kubernetes HA GA path, `duckdock-runtime-secrets` must be reconciled by
External Secrets, Vault, SOPS or an equivalent approved manager. A rotation is
not accepted merely because the Secret object changed: run
`backend/scripts/collect_ga_target_secrets.py` during the target exercise. It
reads metadata only, requires separate provider and independent-verifier
OpenSSH signatures, and proves backend/worker/beat rolled to entirely new Pod
UIDs. See `docs/ga-production-authorization.zh-CN.md` for the exact protocol.

## Network

`docker-compose.prod.yml` binds frontend, MinIO, Prometheus and Alertmanager to
`127.0.0.1`; MySQL and Redis have no host binding. `docker-compose.prod-tls.yml`
adds the only public listener on `${HTTPS_PORT:-443}`. It accepts TLS 1.2/1.3,
serves the application hostname through frontend/backend and the separate object
hostname through MinIO. The certificate must cover both names and retain at least
30 days of validity at preflight.

Application, data and observability services use separate internal Docker
networks. Only backend/worker/Alertmanager and explicitly external integrations
join an egress-capable network. Compose remains a single-host baseline; use the
Kubernetes HA reference for cross-fault-domain commitments.

Set:

```env
BACKEND_BASE_URL=https://your-domain.example.com
CORS_ORIGINS=["https://your-domain.example.com"]
DUCKDOCK_PUBLIC_HOST=your-domain.example.com
DUCKDOCK_MINIO_PUBLIC_HOST=objects.your-domain.example.com
MINIO_PUBLIC_ENDPOINT=objects.your-domain.example.com
MINIO_SECURE=true
DUCKDOCK_TLS_CERT_FILE=/etc/duckdock/tls/fullchain.pem
DUCKDOCK_TLS_KEY_FILE=/etc/duckdock/tls/privkey.pem
```

## Alerting and on-call

Copy `ops/alertmanager/alertmanager.example.yml` to a deployment-owned path and
replace both receiver URLs. Keep that rendered file outside git and point
`ALERTMANAGER_CONFIG_FILE` at it. Prometheus includes API availability, error
budget, evidence/timeline/policy latency and Alertmanager delivery meta-alerts.
The production image is rebuilt from the exact upstream `0.33.1` release commit
with post-release `x/crypto` and gRPC security fixes, then reduced to a non-root
scratch runtime; CI generates SBOM/provenance and blocks Critical/High findings.
For the final `v2.0.0` tag, `release-images` waits for backend, frontend, E2E and
Compose gates, verifies GitHub's signed annotated-tag result, pushes GHCR
commit candidates with BuildKit attestations, blocks Critical/High findings,
then promotes all scanned indexes to previously nonexistent final tags. The
separate builder-signed provenance bundle and final GA re-verification remain
mandatory; see `docs/ga-production-authorization.zh-CN.md`.

Before authorization, run `backend/scripts/collect_ga_target_alerting.py` against
the target Alertmanager. It must prove firing and resolved delivery to both
configured channels/receivers, plus a separately signed acknowledgement from an
actual member of the named on-call schedule. The collector and GA gate re-verify
the exact-principal trust policy, raw OpenSSH signatures and active/inactive API
observations. Merely loading the rules does not satisfy this gate.

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

This first creates three temporary artifacts with the same timestamp:

- `duckdock-mysql-<timestamp>.sql.gz`
- `duckdock-repos-<timestamp>.tar.gz`
- `duckdock-minio-<timestamp>.tar.gz`

`scripts/seal-backup.sh` then encrypts every artifact to the deployment age
recipient, writes plaintext/encrypted SHA-256 digests to a manifest, signs that
manifest with the backup OpenSSH key, removes the temporary plaintext on exit and
uploads the bundle to `DUCKDOCK_BACKUP_S3_URI`. A DB-only backup is incomplete
because skill Git repositories and report/package objects live in separate
volumes. Configure retention and object lock/immutability on the remote bucket;
the target receipt must prove those bucket-side controls.

## Restore

Restore the signed encrypted bundle:

```bash
export SOPS_AGE_KEY_FILE=/etc/duckdock/duckdock-prod-age.txt
bash scripts/restore.sh \
  --bundle /secure-media/duckdock-backup-<timestamp> \
  --allowed-signers /etc/duckdock/backup-allowed-signers \
  --signer-identity backup-operator@example.com \
  --age-key /etc/duckdock/backup-age-key.txt
```

The script verifies the manifest OpenSSH signature, verifies encrypted digests,
decrypts into a restrictive temporary directory, verifies plaintext digests,
stops application services, restores MySQL/repos/MinIO, then starts the stack.
The GA recovery evidence must retain the same `manifest.json`,
`manifest.json.sig`, signer identity and allowed-signers file. The production
authorization gate independently verifies the `duckdock-backup` signature and
binds the manifest's release commit and all three artifact metadata records;
a copied `signature_verified` boolean is rejected.
The legacy three-plaintext-file flags remain available only for staging and old
backup migration; they cannot pass the GA offsite/encryption gate.

For the GA target exercise, start
`backend/scripts/collect_ga_target_recovery.py` before running the destructive
restore. It requires an explicitly different non-production target and three
role-separated signed receipts: storage-side object versions/Object Lock,
restore-stage exit codes/log digests, and independent MySQL/object/Git/service
verification. The collector never accepts the age key, database password or
object-store credentials. It matches every offsite object digest/size back to
the signed manifest and derives RPO/RTO from the signed timeline. See
`docs/ga-production-authorization.zh-CN.md` for the command and receipt schemas.

After restore:

```bash
bash scripts/prod.sh status
curl -fsS https://${DUCKDOCK_PUBLIC_HOST}/health
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

The Compose steps above are operational guidance, not DuckDock 2.0 GA evidence.
The GA Kubernetes path additionally requires the signed, metadata-only v2 target
rotation exercise described in the Secrets section.
