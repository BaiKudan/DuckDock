# GA target-cluster identity binding — 2026-08-07

## Outcome

DuckDock execution campaign v3 now binds the real Kubernetes target through the
`kube-system` Namespace UID and the exact authenticated principal in addition
to the kubeconfig context name and application Namespace. A context that is
silently repointed to another cluster, or a credential that changes after
review, is rejected before the supported Kubernetes-facing production tools
probe or mutate the target.

This is PGA-18's repository-side identity control. It proves which cluster and
principal the guarded process observed; it does not prove least privilege or
control cloud consoles, direct cluster administrators, internal-function calls
or repository-external tools. Target IAM/RBAC, audit and change management
remain release-authority obligations.

## Campaign and signed observation

`duckdock-ga-execution-campaign-request-v3` requires:

- a lowercase RFC 4122 `kubernetes_cluster_uid` obtained from
  `kube-system.metadata.uid`;
- one printable, non-wildcard `kubernetes_principal` obtained from
  `kubectl auth whoami`;
- the existing exact context and application Namespace.

The campaign plans a read-only `target_cluster_identity` phase after dual
execution authorization and release provenance. Inside the active window,
`collect_ga_target_cluster_identity.py` runs both live kubectl queries itself,
requires the exact Operations identity that co-signed execution authorization,
derives the observation time, and writes one immutable report/signature pair
under the `duckdock-ga-target-cluster-identity` OpenSSH namespace. The caller
cannot supply observed identity fields or backdate the receipt.

## Admission and runtime enforcement

The network phase depends on the two signed identity artifacts, so its phase
start statement content-addresses them. Progress inspection validates both as
one signed unit. Closure v3 projects their digests and exact observed identity,
re-verifies them at the canonical closure time, and the deterministic archive
re-verifies the same result with strict no-host-fallback path mapping.

After the PGA-17 runtime permit succeeds and before the first effect call, the
official network, secrets and HA CLIs repeat the live UID/principal query. The
repository baseline pins this exact ordering:

```text
signed phase permit -> live cluster UID/principal -> target effect
```

## Verified failure paths

Regression tests reject:

- a non-RFC-4122 cluster identifier;
- wildcard or ambiguous principals;
- collection by an identity other than the original Operations authorizer;
- a live cluster UID or authenticated principal different from the signed
  receipt;
- a changed signed report or detached signature;
- invalid identity artifacts during progress, closure and portable archive
  verification.

The end-to-end fixture now contains 92 planned artifacts, 89 externally
produced artifacts, 113 captured inputs and 292 explicit references. The
closure projection deliberately records content digests rather than host-local
absolute paths, preserving offline portability.

## Quality gates

- backend: 1236 passed, 19 skipped; selected core coverage 81.04% (minimum 65%);
- production-authorization suite: 134 tests;
- production-operations suite: 41 tests;
- focused PGA-18 regression: 5 passed;
- frontend: 11 files / 56 tests, zero lint warnings, production build passed;
- dependency audit: Python and npm reported zero known vulnerabilities;
- static/contract: Ruff, compileall and frozen OpenAPI v2 drift check passed;
- data/config: 63 tracked JSON files, 33 tracked YAML files, shell syntax and
  four Compose configurations passed;
- type ratchet: 81 mypy errors, below the ceiling of 82;
- repository production baseline: 34/34 PASS.
