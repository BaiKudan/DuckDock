# Foundation Tenant Backfill Runbook (S1-B)

**Command**: `backend/scripts/backfill_foundation_tenants.py`
**Schema prerequisite**: Alembic revision `20260717_0027` or later
**Checkpoint schema**: `1`

## 1. Scope and Safety Boundary

This command resolves and conditionally fills a bounded batch of NULL
`namespace_id` values in this dependency order:

1. `ai_assets`
2. `runtime_instances`
3. `runtime_bindings`
4. `work_traces`
5. `evidence_items`

It is an expand-phase NULL backfill and blocker inventory, not the final tenant
invariant validator.

- Rows with an existing non-NULL direct Namespace are not changed or scanned.
- Existing direct values have transitional rerun priority. FND-013 and FND-019
  must validate non-NULL values against every related row before the contract
  migration.
- A soft-deleted Namespace row remains a valid historical tenant identity for
  backfill. FND-018 separately controls whether an active Namespace may receive
  a new write.
- `EvidenceItem` has the nullable typed `work_trace_id` approved by
  [ADR-0211](../../docs/adr/0211-evidence-worktrace-typed-link.md). A NULL
  Evidence tenant resolves only when that link targets a deterministically
  resolved WorkTrace. Evidence without the typed link remains `unresolved`;
  CollectionJob, creator Membership, object URI, reverse AssetOwnership and
  JSON fields are not substitutes.
- The resolver never guesses from Membership, names, email domains, provider
  labels, URLs, metadata, a default Namespace or query order.
- Apply mode is permitted only while writes that mutate AssetOwnership,
  RuntimeBinding or the five target relationship fields are quiesced. The
  required `--ack-write-quiescence` flag is an operator assertion, not a lock.

## 2. Resolution Outcomes

| Status | Meaning | Database action |
|---|---|---|
| `resolved` | Exactly one approved typed evidence path resolves | CAS update when not a dry run |
| `unresolved` | No sufficient approved evidence exists | Leave NULL and report reason |
| `conflict` | Approved evidence yields two or more Namespaces | Leave NULL and report sorted candidates |

Resolved writes use this compare-and-set shape:

```sql
UPDATE <target_table>
SET namespace_id = :resolved_namespace_id
WHERE id = :target_id
  AND namespace_id IS NULL;
```

If another writer assigns the row first, the command never overwrites it. A
different concurrent value is reported as a conflict.

## 3. Preflight

1. Take the normal database backup/snapshot required by the environment.
2. Confirm the application points at the intended database through
   `DATABASE_URL`; the CLI deliberately accepts no database password argument.
3. Confirm `alembic current` is at `20260717_0027` or later.
4. Start in staging with `--dry-run`.
5. Use different checkpoint/report paths for dry-run and apply. Checkpoint v1
   binds the cursor to its mode, a password-free hash of the physical database
   identity and the current Alembic revision; cross-mode/database/revision reuse
   is rejected.
6. Before apply, pause legacy writers that can create NULL target rows or mutate
   AssetOwnership, RuntimeBinding, WorkTrace relations and target tenant keys.
   Keep them paused for the complete pass.

## 4. Run One Bounded Batch

From `backend/`:

```powershell
python scripts/backfill_foundation_tenants.py `
  --dry-run `
  --batch-size 500 `
  --checkpoint .state/staging-tenant-backfill.dry-run.checkpoint.json `
  --json
```

Apply mode with a durable checkpoint:

```powershell
python scripts/backfill_foundation_tenants.py `
  --apply `
  --ack-write-quiescence `
  --batch-size 500 `
  --checkpoint .state/staging-tenant-backfill.apply.checkpoint.json `
  --json
```

Each invocation scans at most `batch-size` rows across all five tables. Repeat
the same command to resume. The checkpoint is atomically replaced only after a
successful database commit. A crash after commit but before checkpoint replace
may repeat a few rows; NULL-only selection and CAS writes make that retry safe.
Dry-run cursors cannot be used by apply, so rows observed but not written in a
dry-run can never be skipped accidentally by a later apply.

For a complete audit pass, archive the JSON from every batch and continue until
an invocation reports `scanned_count` smaller than `batch-size` (including
zero). Aggregate unresolved/conflict findings across the pass. A clean final
small batch alone does not erase blockers reported by earlier batches.

Concurrent inserts into an entity type that the cursor has already passed are
not part of that pass. This is why apply requires write quiescence and why the
final FND-019 preflight must start from the beginning after FND-018 enforces
Namespace-safe new writes.

To re-audit current blockers after remediation, start without the old
checkpoint. Already resolved rows are skipped; remaining NULL rows are scanned
again.

## 5. Transaction Semantics

- Apply mode commits the entire bounded batch once.
- Dry-run mode always rolls the transaction back.
- A resolver/database exception rolls the batch back and does not advance the
  checkpoint.
- JSON/console output is emitted after transaction handling and checkpoint
  persistence.
- The command creates no per-row `AuditLog`; the deterministic JSON report is
  the idempotent migration evidence.

## 6. Output and Exit Codes

`--json` emits deterministic report schema version `1` only on stdout. Without
it, the command emits a human-readable summary and one line per finding.

| Exit code | Meaning |
|---:|---|
| `0` | This bounded batch has no unresolved/conflict finding |
| `2` | This bounded batch contains unresolved or conflict findings; also argparse usage errors |
| `3` | Checkpoint is unreadable, invalid or has an unsupported schema |
| `4` | Database, transaction, report or checkpoint I/O failure |
| `130` | Interrupted by the operator |

Important: code `0` describes the current bounded batch. Contract migration
approval still requires a complete all-table audit plus FND-013/FND-019
non-NULL relationship validation.

## 7. Remediation Rules

- `AIAsset unresolved`: create or correct explicit `AssetOwnership` evidence.
  `VERIFIED_TYPED_SOURCE` is reserved until a concrete source FK/rule is
  approved and implemented; never insert a guessed tenant.
- `AIAsset conflict`: reconcile the explicit AssetOwnership records through an
  approved ownership decision.
- `Runtime unresolved/conflict`: remediate every bound Asset first.
- `RuntimeBinding unresolved/conflict`: Runtime and Asset must both resolve and
  agree.
- `WorkTrace unresolved/conflict`: resolve its linked Asset first; Runtime is a
  fallback only when no Asset relation exists.
- `EvidenceItem unresolved`: do not infer. Populate the ADR-0211
  `work_trace_id` only from trustworthy lineage evidence, then rerun the
  resolver. If no trustworthy WorkTrace exists, leave the row unresolved.

After remediation, discard the completed pass cursor, rerun dry-run from the
beginning, and archive the reports. Do not run FND-019 contract migration until
the full preflight reports zero blockers and non-NULL relationships have also
been validated.
