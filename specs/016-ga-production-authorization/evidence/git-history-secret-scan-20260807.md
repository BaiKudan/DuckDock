# DuckDock full Git-history secret-scan gate — 2026-08-07

## Decision

The repository now has a dedicated, reproducible full-history secret-scanning
gate. The clean source state below passed the expanded internal security
pre-audit with no unbaselined Gitleaks finding.

- Source commit: `e1537117fcd6f346ceba6d6d727a747e24506af2`
- Source tree: `0d0214cff87655d9857208b8a192e1dd86c86037`
- Candidate version: `2.0.0-rc.1`
- Internal pre-audit: 14 PASS / 0 BLOCK
- Receipt SHA-256: `2c5e6aba390034e90ce536de59b7faca1c89c633ef171ddb79a292f51b459644`
- Local evidence directory:
  `/Users/wower/.duckdock/security-preaudit/e153711-20260807`

This remains internal engineering evidence. It is not an independent security
assessment and cannot authorize a GA publication or production deployment.

## Scanner supply chain and sandbox

- Gitleaks release: `v8.30.1`
- Release tag commit: `83d9cd684c87d95d656c1458ef04895a7f1cbd8e`
- Official OCI index:
  `ghcr.io/gitleaks/gitleaks:v8.30.1@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f`
- Execution: current UID/GID, read-only repository/root filesystem, no network,
  all capabilities dropped, `no-new-privileges`, bounded tmpfs.
- Disclosure control: 100% redaction; reports must be new and outside the
  repository.
- Scope: Gitleaks' pinned default `git log -p -U0 --full-history --all
  --diff-filter=tuxdb` behavior; no narrowing `--log-opts` is supplied.

The canonical entrypoint is `scripts/run-gitleaks.sh`. Backend CI now checks out
full history with `fetch-depth: 0`, runs that exact entrypoint and retains its
redacted JSON report for 90 days. The internal pre-audit invokes the same file
and content-addresses its report.

## First-run review and exact baseline

The first redacted scan reported 33 candidates. Each was reviewed without
placing the matched value in logs or evidence. They were all non-secret false
positives in four categories:

1. local test or CI cryptographic fixtures with no authority over an external
   account or production system;
2. schema versions, digests, idempotency values and object keys matched by the
   generic API-key rule;
3. private-key marker constants used by DuckDock's archive rejection scanner;
4. OTLP smoke-test `curl -u` expressions whose values come from runtime
   environment variables rather than the repository.

`.gitleaksignore` contains exactly 33 unique finding fingerprints. There is no
path-wide, commit-wide, test-wide or rule-wide allowlist, so a new value on an
otherwise familiar path remains a blocking finding.

## Verified run

| Check | Result |
|---|---:|
| Git commits scanned | 218 |
| Git diff volume scanned | approximately 19.72 MB |
| Unbaselined findings | 0 |
| Redacted report | JSON empty list |
| Report SHA-256 | `37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570` |
| Full internal pre-audit | 14 PASS / 0 BLOCK |
| Security negative tests | 198 passed / 2 skipped |
| Python / npm dependency audit | 0 known vulnerabilities / 0 vulnerabilities |
| Four first-party image scans | 0 Critical / 0 High for every image |
| Production repository baseline | 37/37 PASS |

The receipt sidecar and all 19 subsidiary artifact digests were recomputed after
the run. The evidence tree contains only mode `0700` directories and mode `0600`
files.

The application source under `backend/app` is unchanged from the immediately
preceding `d79335d` state whose full suite completed with 1260 passed and 19
skipped. This change additionally passed six focused pre-audit tests, shell
syntax, Ruff, YAML parsing, the full-history scan and the expanded internal
security pre-audit.

## Remaining boundary

This gate does not inspect target Secret Manager values, GitHub organization
secrets, runtime environment variables, external logs, backup media or secrets
that evade the pinned rule set. True findings require rotation/revocation and
impact analysis; the project must not rewrite published history merely to make
the scanner green.

The final immutable `v2.0.0` source and CI checkout must rerun the same scan.
Independent assessment of the final build and real target environment remains
`PENDING_EXTERNAL`, together with target Secrets rotation/verification and the
other production authorization evidence.
