# DuckDock 2.0 release provenance gate — 2026-08-06

## Scope and boundary

This record covers the repository implementation of the final release-build
trust protocol. It is not a `v2.0.0` build receipt, registry attestation or
production authorization. No final tag, final GHCR digest, target evidence or
organizational approval is asserted here.

## Implemented protocol

- `v2.0.0` tag pushes run backend, frontend, E2E and Compose jobs before the
  release image job. It pushes commit-addressed candidates first; only after
  all four scans pass and all final tags are confirmed absent are the indexes
  promoted to four previously unused final tags; the authorization still uses
  immutable digest coordinates rather than trusting tag immutability.
- The release job requires an annotated tag whose GitHub tag-verification
  result is verified, and BuildKit emits provenance/SBOM attestations for all
  four release images. Docker Scout remains fail-closed for Critical/High and
  the four raw SARIF reports are retained as a 90-day immutable Actions artifact.
- A separate content-addressed policy fixes exact builder signer identities,
  public keys, source repositories, builder IDs, workflow refs and the required
  backend/frontend artifacts.
- The approved builder signs a raw report that binds the captured tag object,
  tag-verification output, deterministic source archive, source tree digest,
  frozen contract, build timeline and exact immutable images.
- Each application image has unique SLSA v1 and SPDX 2.3 Statement subjects,
  unmodified registry predicates, original SARIF and a machine-readable vulnerability report.
  The collector and final gate reopen and hash every file, require structurally
  identical predicates, require the normalized report to bind the SARIF digest,
  driver/version and zero result count, recompute finding counts, enforce scan
  freshness and reject open Critical/High.
- The final authorization evaluator treats this as a `FOUNDATION` check. The
  builder identity and key must be disjoint from all approval and independent
  assessor identities/keys. The provenance reference is included in the
  release digest, so replacement invalidates every approval.

## Negative coverage

The tests reject:

- raw build report changes after signing;
- unapproved builders/workflows/source repositories;
- cross-image SLSA/SPDX subjects, modified registry predicates and vulnerability reports;
- forged vulnerability totals, an unbound/malformed SARIF or a raw High result;
- forged/failed tag-verification output;
- public-key reuse across builder identities or between builder and approver;
- content-addressed SLSA modification after wrapper collection.

## BuildKit compatibility check

- Docker Engine `29.5.3` and Buildx `0.34.1-desktop.1` produced a local
  `mode=max,version=v1` OCI attestation for an offline `FROM scratch` probe.
  Its subject used the real
  `pkg:docker/<registry>/<repository>@<tag>?platform=linux%2Famd64` form and
  its empty optional `resolvedDependencies` and `byproducts` fields were
  omitted. The validator and fixtures now accept those SLSA v1 optional-field
  semantics while still requiring exact `configSource` tag/commit binding.
- Current official `docker/buildkit-syft-scanner` source at
  `25dc3d0e94eaafcb55f316ed8cb236fcca620337` and its scratch fixture show that
  the default SPDX document name is `sbom`, not an image coordinate. The
  protocol therefore retains the content-addressed registry SPDX predicate and
  binds it to the exact image through an in-toto Statement subject, rather
  than trusting the document name.
- The live SBOM probe could not pull `docker/buildkit-syft-scanner:stable-1`
  because Docker Hub returned `DeadlineExceeded`. This is recorded as an
  external-network limitation, not as a passing final SBOM build. The real
  final registry export remains mandatory.
- Local Docker Scout `1.21.0` also did not produce SARIF within 30 seconds
  because its external analysis path did not return; the probe was cancelled
  and no report was counted as evidence. CI is pinned to the current Scout
  Action `1.24.0` commit and the final workflow must produce all four non-missing
  SARIF files before image promotion.

## Verification performed

```text
backend pytest: 1182 passed, 19 skipped, 3 warnings
production authorization suite: 82 passed
production operations config suite: 39 passed
Ruff: PASS
Python compile: PASS
GA JSON templates: PASS
CI YAML parse: PASS
production baseline: 21 PASS / 0 BLOCK
```

The remaining work is execution owned by the release organization and target
operators: create and verify the real signed `v2.0.0` tag, publish final
digest-addressed images, export/sign the real provenance bundle, collect target
evidence and obtain the four organizational approvals.
