# GA phase-start execution interlocks — 2026-08-07

## Outcome

DuckDock now requires a separate signed start interlock for each of the eight
acknowledged risky phases in an external GA execution campaign. The prior
Security/Operations dual authorization remains necessary, but is no longer
sufficient by itself for closure: a phase result is rejected when its actual
probe/provider/load/restore/alert/fault timestamp predates its signed start.

This is a repository-side evidence and official-workflow control. It does not
claim that a real target was exercised, and it cannot replace cloud IAM,
Kubernetes RBAC or organizational change management that prevents a privileged
person from operating outside DuckDock.

## Protocol

`prepare_ga_execution_campaign.py` now plans sixteen additional immutable
artifacts: one `duckdock-ga-execution-phase-start-v1` statement and one OpenSSH
signature for each of TLS, network, secrets, capacity, alerting, recovery,
state-services and high availability. The signature namespace is
`duckdock-ga-execution-phase-start`.

`start_ga_execution_phase.py` accepts only an exact campaign, phase, action ID,
single-line secret-free action description, Operations identity and private-key
path. It exposes no caller-controlled start time or caller-supplied action
digest. The core derives the digest and current UTC time, then:

1. independently regenerates the campaign/request/topology/assembly graph;
2. re-verifies both original Security and Operations authorization signatures;
3. requires the active campaign window;
4. requires the signer identity to equal the original Operations authorizer;
5. resolves and content-addresses every output of every declared dependency;
6. binds the full phase projection, exact acknowledgement, reviewed action,
   dependency ledger, campaign and authorization into the signed statement;
7. creates the two planned files atomically without overwrite and immediately
   re-verifies the persisted signature.

The authorization boundary is
`authorizes_only_one_named_campaign_phase_start_not_GA_or_unlisted_mutation`.
It does not assert evidence PASS or GA authorization.

## Closure timing anchors

Closure v2 re-verifies every original phase statement/signature and dependency
digest, then compares the signed start with the earliest authoritative phase
timestamp:

| Phase | Earliest admitted execution timestamp |
| --- | --- |
| TLS | raw TLS probe `observed_at` |
| network | raw network probe `observed_at` |
| secrets | provider rotation receipt `started_at` |
| capacity | signed load report `started_at` |
| alerting | alert exercise `started_at` |
| recovery | signed restore receipt `started_at` |
| state-services | provider failover receipt `observed_at` |
| high availability | fault injection `started_at` |

Progress inspection also verifies any complete phase-start pair and marks both
artifacts invalid when a statement, signature, dependency or authorization is
wrong. Persisted closure and portable archive verification repeat the same
checks with strict no-host-fallback path mapping.

## Verified failure paths

Automated negative coverage rejects:

- a signer other than the exact Operations co-authorizer;
- phase start before the campaign window or verification after expiry;
- modified action description/digest;
- a signature copied from another phase;
- a dependency changed after the start was signed;
- a fault-injection timestamp earlier than its signed start;
- malformed/cross-campaign statements and missing planned artifacts.

The signed synthetic end-to-end path plans 90 artifacts: 87 external inputs and
three closure outputs. The exact closure captures 111 inputs and 290 explicit
references, reaches `APPROVAL_COLLECTION`, completes four-role
`GA_AUTHORIZED`, and passes deterministic portable archive verification without
reading host evidence paths. Synthetic identities and timestamps are test data,
not production acceptance evidence.

## Quality gates

- backend: 1234 passed, 19 skipped; selected core coverage 81.04% (minimum 65%);
- production-authorization suite: 132 tests included in the passing backend run;
- production-operations suite: 41 passed;
- frontend: 11 files / 56 tests, zero lint warnings, production build passed;
- dependency audit: Python and npm reported zero known vulnerabilities;
- static/contract: Ruff, compileall and frozen OpenAPI v2 drift check passed;
- data/config: 63 tracked JSON files, 33 tracked YAML files and four Compose
  configurations parsed successfully;
- type ratchet: 81 mypy errors, below the ceiling of 82;
- repository production baseline: 32/32 PASS.
