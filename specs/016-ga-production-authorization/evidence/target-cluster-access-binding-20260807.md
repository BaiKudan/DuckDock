# PGA-19 target cluster access and change-scope binding evidence

Date: 2026-08-07

## Result

Repository-side implementation is complete and fail closed. The production execution campaign is
now v4 and content-addresses an external change-request ID plus the exact Kubernetes Secret, CNI
DaemonSet, trusted/monitoring/untrusted probe Namespaces and Pods, and HA drain zone. This protocol
does not authorize GA or prove that the external ticket was approved.

After campaign dual authorization, release provenance and target-cluster identity are available, the
same Operations authorizer must run `collect_ga_target_cluster_access.py`. The collector performs a
fixed `kubectl auth can-i` allow/deny matrix, rejects any result that differs from the required profile,
and signs an immutable in-window receipt bound to the campaign, release provenance, cluster identity,
change request and operational scope.

Progress inspection, closure v4 and portable archive verification reopen the report and OpenSSH
signature. Every phase-start action ID must begin with the campaign-bound change-request ID. The
network, secrets and high-availability entrypoints enforce this order before their target effect:

```text
signed phase permit
  -> exact operational-scope match
  -> live cluster UID/principal check
  -> complete live allow/deny permission recheck
  -> target probe or mutation
```

Permission or identity drift, a different Secret/CNI/probe/zone, wrong Operations signer, malformed
change request, signature/report tampering and closure substitution all fail closed.

## Verification

- Backend: 1238 passed, 19 skipped; core-module coverage 81.04%.
- GA production authorization: 136 passed.
- Production operations configuration: 41 passed.
- Frontend: 11 files / 56 tests; lint and production build passed.
- Production repository baseline: 35/35 PASS.
- Campaign fixture: 94 planned artifacts, 91 external artifacts, 115 captured closure inputs and 294 references.
- Ruff and compile passed; mypy remained 81 errors against ceiling 82.
- Python runtime lock and npm audit reported no known vulnerabilities.
- Frozen OpenAPI v2 contract remained at SHA-256 `aa260f301acc5c3a8004d14980952a03ce0197f9d70dcdd78cd62e986c3b1a83`.
- 72 repository JSON and 48 YAML documents parsed successfully; default, analysis-worker,
  observability and production Compose configurations rendered cleanly.

## Truthful boundary

The receipt proves only the observed principal's results for the explicit allow/deny probes at the
recorded and runtime instants. Kubernetes RBAC is additive, so this is not an exhaustive proof of all
effective privileges. DuckDock also does not authenticate or approve the external change ticket and
cannot constrain cloud/Kubernetes administrators or repository-external tools. The release authority
must still provide independently governed ticket approval, cloud IAM, complete RBAC review, audit-log
retention and target-side prevention/detection of out-of-band actions.

No customer production cluster was contacted by this implementation verification. Real target access,
external security assessment, target TLS/network/secrets/alerting/recovery/capacity/HA evidence and four
human organizational approvals remain mandatory before `GA_AUTHORIZED` is possible.
