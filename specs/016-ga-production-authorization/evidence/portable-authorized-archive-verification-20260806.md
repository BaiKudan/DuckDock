# Portable GA authorized archive verification — 2026-08-06

## Outcome

The repository can now independently verify a transported GA authorization
archive without reading any evidence, trust store or signature from the
original host. This is a repository capability, not a real production GA
authorization; no final tag, target campaign, third-party decision or human
approval was fabricated.

## Gaps found and corrected

1. The v1 archive proved member integrity but had no exact raw-reference to
   content-addressed-member index, so an offline evaluator could not safely
   reconstruct absolute paths.
2. Nested archive discovery and the authoritative evaluator did not explicitly
   share one portable path contract. A receiver could otherwise accidentally
   read still-present files from the original host.
3. The recovery backup manifest used `allowed_signers_path` without a matching
   trust-store digest. Its signature could not be self-contained in an
   immutable archive.
4. A caller-controlled deterministic timestamp needed a bounded relationship
   to actual archive time so it could not backdate stale evidence.

## Implemented contract

- `duckdock-ga-authorized-archive-manifest-v2` records every discovered
  `path+sha256`, `path+manifest_sha256`,
  `allowed_signers_path+allowed_signers_sha256`, and `signature_path` edge as
  an exact source-member to target-member mapping.
- A single raw path may map to only one target. Relative evidence discovery
  follows the authorization evaluator contract; official collectors emit
  absolute paths.
- `--created-at` may be at most 300 seconds before actual archive start. The
  archive must pass both current-time evaluation and canonical-time evaluation.
- Before emitting bytes, the archiver enables strict reference overrides and
  reruns the complete evaluator using only the captured closure. A missing edge
  cannot fall back to the host filesystem.
- Release-provenance and independent-security validators now honor the same
  scoped strict mapping for their nested source/SLSA/SPDX/SARIF/PDF files.
- Recovery collection emits `backup.allowed_signers_sha256`; the evaluator now
  requires and recomputes that digest before accepting the backup signature.
- `verify_ga_authorized_archive.py` requires either a digest sidecar or an
  explicit SHA-256 expected from an independent channel. It validates exact
  manifest fields, member set/type/mode/mtime/header size/content digest,
  reference-index completeness, bounded totals, private-key exclusion and
  detached-versus-embedded manifest equality.
- The verifier materializes only safe content-addressed member names into a
  temporary directory, installs strict path overrides and reruns the full
  evaluator at the archive's canonical historical time. Success requires the
  same release digest and a zero-block `GA_AUTHORIZED` result.

## Adversarial evidence

- A full signed fixture archives deterministically and verifies through both a
  trusted digest sidecar and a naked expected SHA-256.
- All original authorization, policy, evidence, trust-store and signature files
  are deleted before the successful portable verification.
- Wrong external digest, detached manifest modification, archive-member byte
  modification and signature-target reference substitution all fail closed.
- An explicitly referenced private key, an out-of-root content reference and a
  canonical timestamp backdated by 301 seconds all fail without outputs.
- A nested relative-path fixture proves archive discovery uses the same
  authorization-relative semantics instead of the nested JSON directory.
- Recovery evidence without `backup.allowed_signers_sha256` is blocked.

## Verification record

```text
backend full pytest: 1199 passed, 19 skipped, 3 warnings
production authorization suite: 98 passed
recovery collector suite: 6 passed
production operations config suite: 40 passed, 2 warnings
production baseline: 24 PASS / 0 BLOCK
mypy: 81 errors <= ratchet ceiling 82
Ruff: clean
compileall: clean
archiver/verifier CLI and CI YAML: clean
git diff --check: clean
```

## Remaining external authority work

The release authority must still produce the signed `v2.0.0` tag and immutable
registry/provenance artifacts, execute the target TLS/secrets/network/capacity/
HA/recovery/alerting campaign, obtain the independent security assessment and
four human approvals, reach real `GA_AUTHORIZED`, then publish the resulting
archive digest through an independent immutable channel.
