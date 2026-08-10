# Quickstart: Tenant Remediation and Contract

## 1. Inventory

```bash
docker compose exec -T -e DEBUG=false backend \
  python scripts/backfill_foundation_tenants.py --dry-run --batch-size 500 --json
```

## 2. Prepare and validate a manifest

Create a JSON document conforming to
`contracts/tenant-remediation-manifest-v1.schema.json`. Every assignment must name one target ID;
there is no default or wildcard.

```bash
docker compose exec -T -e DEBUG=false backend \
  python scripts/remediate_foundation_tenants.py \
  --manifest /app/.state/tenant-remediation.json --dry-run --json
```

## 3. Apply

After pausing relationship writers and reviewing the dry-run:

```bash
docker compose exec -T -e DEBUG=false backend \
  python scripts/remediate_foundation_tenants.py \
  --manifest /app/.state/tenant-remediation.json \
  --apply --ack-write-quiescence --json
```

## 4. Derive related ownership

Run the existing backfill in apply mode until a complete pass is exhausted, then restart a dry-run from the
beginning.

### Disposable non-production data

If the Owner explicitly classifies unresolved rows as disposable test data, an environment-specific cleanup
may replace manifest assignment only after all of the following are recorded:

1. a verified full database backup and checksum;
2. a write-quiescence window;
3. exact target tables, row IDs/counts and expected FK cascades;
4. a post-cleanup backfill plus contract preflight from the beginning.

This exception does not apply to production or data whose provenance is uncertain.

## 5. Contract preflight

```bash
docker compose exec -T -e DEBUG=false backend \
  python scripts/check_foundation_tenant_contract.py --json
```

Do not apply contract DDL unless every blocker count is zero and the report is retained with the change record.

The preflight covers the five NULL counts, Binding/WorkTrace/Evidence tenant mismatches and duplicate Binding
tenant keys.

## 6. Apply the contract

After retaining the zero-blocker report, keep relationship writers paused and apply revision
`20260728_0029`:

```bash
docker compose exec -T -e DEBUG=false backend alembic upgrade head
docker compose exec -T -e DEBUG=false backend alembic check
```

Restart writers only after the schema reports five non-null Namespace columns, the expected tenant-scoped
indexes and a second zero-blocker preflight.
