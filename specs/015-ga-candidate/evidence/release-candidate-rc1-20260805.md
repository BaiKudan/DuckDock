# DuckDock 2.0.0-rc.1 isolated release evidence — 2026-08-05

## Evidence boundary

- Candidate: `2.0.0-rc.1`
- Branch: `codex/release-2.0-rc1`
- Database: fresh MySQL 8.4 volume, migrated from baseline to `20260804_0062`
- Object store/cache/metrics: project-isolated MinIO, Redis and Prometheus volumes
- Runtime provider: the developer machine's live OpenAI-compatible Hermes endpoint
- Data policy: model identity is not copied into this artifact; the inference smoke records only status and vector shape
- Existing `duckdock` development database: not used for release evidence

This record proves the named local candidate and environment only. It is not a production deployment approval.

## Repeated bootstrap

`scripts.bootstrap_release_candidate_dev` created or reconciled the prerequisite graph through live HTTP APIs:

- Namespace id `1`
- Runtime id `1`
- Agent asset id `1`
- PackageVersion `pkgv_7f152328d6ef4d61b9b89db68c2fa742`, status `VERIFIED`
- manifest digest `b243bea0a6d5a188c22faa5b393cfbaff1de98f155803f8d4e3e33e8c337ecd4`
- signing key fingerprint `f5a2e554ae27f0e7a645fd1d872c4702431b7f88359bad37197c0b9160d94733`
- signature, SBOM digest and SBOM component coverage: all valid
- real `/v1/embeddings`: HTTP 200, one numeric vector, 384 dimensions

The validation-only Ed25519 private key was derived in process, never persisted and never transmitted to DuckDock.

## Release Control and rollback

- Policy outcome: `ALLOW`
- Promotion: `SUCCEEDED`
- Runtime promotion receipt: `APPLIED`
- Canary evaluation: `FAIL` (intentional injected metadata-only failed Run)
- Canary promotion final state: `ROLLED_BACK`
- Rollback: `SUCCEEDED`
- Runtime rollback receipt: present

The run-completion path also reproduced a MySQL second-precision edge case. The verifier now derives `ended_at` from the persisted API `started_at`, preventing a fast local completion from being rounded before its start time.

## Handover 2.0

- Snapshot: `hsnap_816c7afaa793470cae893360e3651fb5`
- Initial readiness: `BLOCKED` (`EVALUATION_BASELINE` obligation)
- Current readiness after receipt: `READY`
- Signed package: `hpkg_5b9adc0405e4495ebc26c0706d1d6fc7`
- Archive digest: `479fe207a7addae3749cf170bceb30b86f771130b06926a69bc5a7d6b815b0cc`
- MinIO readback: 2879 bytes
- Independent manifest/archive digest and Ed25519 verification: PASS

## Identity and signing trust

- Workload kind: `DEVICE`
- Heartbeat before directory disable: HTTP 200
- User request after disable: HTTP 401
- Workload heartbeat after disable: HTTP 401
- SCIM replay: idempotent
- Credentials revoked: 1
- Memberships removed: 1
- Handover automatically created: yes
- Signing-key rotation sequence: 2

## Recovery and operations

- MySQL dump/drop/restore rows: 3
- MinIO backup/delete/restore objects: 1
- Recovery status: `PASSED`
- RPO: 0 seconds
- RTO: 1 second
- Prometheus readiness: HTTP 200
- Open operations incidents: 0
- Transactional outbox failed/expired: 0/0

## Final SLO and readiness result

```json
{
  "status": "READY",
  "pass_count": 14,
  "warn_count": 0,
  "block_count": 0,
  "contract_version": "2.0.0-rc.1",
  "contract_digest": "aa260f301acc5c3a8004d14980952a03ce0197f9d70dcdd78cd62e986c3b1a83",
  "database_revision": "20260804_0062",
  "slo": {
    "status": "HEALTHY",
    "request_count": 142,
    "error_count": 0,
    "evidence_ingest_p95_ms": 7.729,
    "run_timeline_p95_ms": 12.636,
    "policy_decision_p95_ms": 54.426
  }
}
```

All readiness keys passed: database revision, frozen contract, Runtime inventory, verified Package, release receipt, signed handover, directory offboarding, signing-key rotation, reconciliation, transactional outbox, recovery, SLO, incidents and Prometheus.

## Regression and supply-chain gates

- Backend Python 3.12 hashed-lock run: `1029` non-MySQL tests passed, `2` skipped; selected core coverage 81.04% (minimum 65%)
- Real MySQL marker lane: `17 passed, 1031 deselected`; exact temporary database and user dropped afterward
- Playwright: `3 passed` (auth guard, login form and seeded approve→execute→receipt→verify→completed closed loop)
- Frontend: npm audit 0; lint 0 warnings; `11` files / `56` tests; production build PASS
- Python runtime lock: pip-audit found no known vulnerabilities
- mypy ratchet: 81 errors, ceiling 82
- OpenAPI drift: clean
- Alembic drift: clean
- Backend/frontend/TLS gateway/custom Alertmanager production images: Docker Scout 0 Critical / 0 High
- Browser: authenticated Operations and Release Control rendered in the application browser with no console warning/error; Operations explicitly distinguishes application readiness from target GA authorization
- Capacity reference: 61,200 Run/Audit/Outbox rows; 60 rps for 900 seconds plus 120 rps for 60 seconds; sustained p95 8.023 ms; post-growth timeline p95 5.187 ms
- Isolated production infrastructure: 10/10 healthy; TLS 1.2/1.3 only, HSTS/hostname valid, alert firing/resolved receipts; exact project removed with zero containers/volumes/networks remaining

The bundled single-host MySQL/MinIO images are not covered by the clean
application-image statement and are explicitly excluded from the GA path. The
GA topology requires independently assessed managed HA MySQL/Redis/S3 services.

## Cleanup

The exact isolated Compose project is disposable. After evidence collection it is removed with its dedicated MySQL, Redis, MinIO, repositories and Prometheus volumes. The existing development project's data and volumes are outside that target and remain unchanged.
