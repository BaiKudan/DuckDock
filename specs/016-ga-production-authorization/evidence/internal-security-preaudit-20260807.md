# DuckDock 2.0 internal security pre-audit — 2026-08-07

## Decision

The reproducible internal security pre-audit passed for the source state below.
This result reduces the risk of commissioning an external assessment with known
basic failures. It is **not** an independent security assessment and does not
authorize a GA publication or production deployment.

- Source commit: `17039993b6a43dd17643ae170c4d939b0623c35e`
- Source tree: `231187d0ef7f3eb41f7953f0f67a72900b9502d0`
- Branch: `codex/release-2.0-rc1`
- Candidate version: `2.0.0-rc.1`
- Receipt status: `PASS_INTERNAL_PREAUDIT`
- Checks: 13 PASS / 0 BLOCK
- Receipt SHA-256: `27f442b929f08343f9fef6ebb23795b405cbf434e73fb93f4ece4bbc165757db`
- Local evidence directory:
  `/Users/wower/.duckdock/security-preaudit/1703999-20260807`

The receipt sidecar, all 17 subsidiary artifact digests and all SARIF digests
were independently recomputed after the run. Directories are mode `0700` and
files are mode `0600`.

## Code findings closed before the run

The pre-audit review found and fixed three concrete classes of security debt:

1. Production release and Langfuse evaluation paths no longer use `assert` for
   runtime invariants that disappear under Python optimization. They fail with
   explicit domain errors instead.
2. Best-effort audit staging, Git retention cleanup and GA validation cleanup no
   longer silently swallow unexpected exceptions. They keep the intended main
   flow semantics while emitting content-safe operator diagnostics.
3. Signed/raw nmap evidence is parsed with `defusedxml`; XML entity payloads are
   rejected by negative tests. `defusedxml==0.7.1` is now an explicit hashed
   development dependency.

CI now has a separate high-confidence Ruff security gate for `S101`, `S110` and
`S314`, in addition to the repository's normal lint job.

## Executed checks

| Check | Result | Detail |
|---|---:|---|
| Python runtime dependency audit | PASS | `pip-audit` found 0 known vulnerabilities |
| Frontend dependency audit | PASS | npm found 0 vulnerabilities at the High gate |
| High-confidence SAST | PASS | Ruff `S101,S110,S314`, 0 findings in `backend/app` and `backend/scripts` |
| Security negative tests | PASS | 198 passed / 2 skipped; authn/authz, tenancy, credentials, logging, sensitive filtering, SSO, SSRF, XML and outbox invariants |
| Tracked-source credential heuristic | PASS | 744 non-test tracked text files / 0 findings |
| Backend image build + scan | PASS | current source rebuilt; 0 Critical / 0 High |
| Frontend image build + scan | PASS | current source rebuilt; 0 Critical / 0 High |
| TLS gateway image build + scan | PASS | current source rebuilt; 0 Critical / 0 High |
| Alertmanager image build + scan | PASS | current source rebuilt; 0 Critical / 0 High |

The credential check is deliberately described as a heuristic. It covers the
current tracked non-test source, excludes known test fixtures, prior evidence
and the archive scanner's detection constants, and does not claim Git-history
coverage or equivalence to a dedicated secret-scanning product.

## Rebuilt image identities

| Image | Local image ID | Critical / High |
|---|---|---:|
| `duckdock-backend:2.0.0-rc.1` | `sha256:ff91ffe4eb29e8be6b3b1e65955429b2d02c13c861cd653b8ab9cda66f1dc6c7` | 0 / 0 |
| `duckdock-frontend:2.0.0-rc.1` | `sha256:ba04d749c7e720461e85df80bb57cae28a2f7ca807dc8ea0d32d689c29624c1b` | 0 / 0 |
| `duckdock-tls-gateway:2.0.0-rc.1` | `sha256:bc799f7b9e3ac49d602f2890122ae3509a3bf36c5898e7d764a212562a163907` | 0 / 0 |
| `duckdock-alertmanager:0.33.1-duckdock.1` | `sha256:d8b977eef517c709f768385793b585f622111a51ddf53e299178c1265abf2d51` | 0 / 0 |

Each Docker Scout SARIF contains zero filtered results. The four empty-result
SARIF documents have SHA-256
`025200b6b7fc193784d8f83a822236db3ca0c6195515567ee574d47bab058daf`.

## Additional regression evidence

- Backend full suite: 1260 passed / 19 skipped / 3 warnings.
- Frontend: 11 files / 56 tests passed.
- mypy: 81 findings, below the existing ceiling of 82; this remains debt rather
  than a clean type-check claim.
- Backend compile: PASS.
- Production repository baseline: 37/37 PASS, including the non-independent
  security pre-audit boundary.

## Remaining mandatory external work

The receipt preserves `independent_security_assessment=PENDING_EXTERNAL`.
Before GA, the release authority must still authorize a bounded engagement;
an independent assessor must test the final immutable `v2.0.0` build and real
target environment, report findings, verify remediation/retests and prove
assessment-data deletion. The final target TLS, Secrets, network, backup,
alert/on-call, sustained capacity, fault-domain HA, four-party authorization and
final signed-tag supply-chain gates also remain mandatory.
