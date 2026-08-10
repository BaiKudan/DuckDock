# GA authorized archive gate — 2026-08-06

## Outcome

The repository-side `archive_authorized_bundle` transition is implemented and
fail closed. A real production bundle was not created: the final signed tag,
target evidence, independent assessment and four human approvals remain
external release-authority work.

## Implemented boundary

- `backend/scripts/archive_ga_authorized_bundle.py` accepts only an already
  persisted authorization that the authoritative evaluator reports as
  `GA_AUTHORIZED`, `campaign_stage=AUTHORIZED`, zero blocks, complete foundation,
  evidence and approvals, and `next_action=archive_authorized_bundle`.
- The same evaluator runs before collection and after collection at the actual
  operation time. `--created-at` controls deterministic manifest metadata only;
  it cannot backdate evidence freshness.
- Collection starts from the authorization, out-of-band approval policy and
  explicitly named supplemental receipts/results. It follows only three
  reference forms: `path+sha256`,
  `allowed_signers_path+allowed_signers_sha256`, and `signature_path`.
- No directory enumeration or glob scan is performed. Every resolved file must be
  a regular non-symlink beneath the authorization/policy roots or an explicitly
  allowed `label=/absolute/root`; adjacent signer, assessor and builder private
  keys are therefore not collected. Common OpenSSH, PEM and age private-key
  payload markers are also rejected even when explicitly referenced.
- Declared digests, conflicting references, per-file/total byte limits and a
  final byte-for-byte TOCTOU pass are enforced before archive construction.
- Tar member paths are content addressed. uid/gid, owner/group, mode and mtime
  are fixed and gzip mtime/filename are empty, so equal inputs with an equal
  creation time produce byte-identical archives.
- Archive, external manifest and SHA-256 sidecar use immutable no-overwrite
  writes. Persisted outputs are reopened; member set, type, metadata, size,
  digest and embedded/external manifest equality are reverified before success.

## Adversarial coverage

- A full fixture containing the production authorization's real OpenSSH
  signatures, nested raw receipts, policies, trust stores, source archive,
  SLSA/SPDX/SARIF artifacts and approval signatures archives successfully.
- A repeated run with the same inputs/time is byte identical.
- The test opens every tar member and recomputes every manifest size and digest.
- No `*_key`, `.pub`, or OpenSSH private-key payload is present in the archive.
- A changed content-addressed readiness artifact blocks all outputs.
- A content-addressed reference outside the allowed roots is rejected.
- Existing outputs are rejected instead of overwritten.

## Verification record

```text
backend full pytest: 1192 passed, 19 skipped, 3 warnings
production authorization suite: 91 passed
production operations config suite: 40 passed, 2 warnings
production baseline: 23 PASS / 0 BLOCK
mypy: 81 errors <= ratchet ceiling 82
Ruff: clean
compileall: clean
archiver CLI/import: clean
git diff --check: clean
```

## Remaining GA authority work

This evidence proves that the repository can safely archive an authorization;
it is not itself a production authorization. The release authority must still
create and externally publish a signed `v2.0.0`, immutable image/provenance
bundle, target capacity/HA/recovery/alerting evidence, independent security
assessment, four human approvals, final `GA_AUTHORIZED` result and the resulting
archive digest.
