# GA execution runtime-entry interlocks — 2026-08-07

## Outcome

DuckDock's eight repository-provided target-production entrypoints now reject a
run before target I/O or mutation unless the operator supplies the active
execution campaign and the exact action ID from that phase's signed start
statement. This closes the supported-CLI gap left by PGA-16: closure still
independently rejects evidence created before a permit, while the official
execution path now also fails before the action begins.

This is a repository-side process-entry control. It does not claim control over
cloud consoles, Kubernetes administrators, direct imports of internal Python
functions or repository-external tools. Production IAM/RBAC and organizational
change management must independently prevent those privileged paths.

## Runtime binding

`verify_runtime_entry` first performs the complete PGA-16 phase-start
verification, including campaign regeneration, dual authorization, active
window, Operations signature and dependency digests. It then reopens the
campaign and requires all of the following to match exactly:

- campaign ID and current campaign SHA-256;
- signed phase and `action.action_id`;
- `target-production` evidence scope and target ID;
- release commit plus backend and frontend `@sha256` images;
- Kubernetes context and Namespace for network, secrets and HA;
- distinct recovery target for destructive recovery.

Success is represented by the non-authorizing
`duckdock-ga-execution-runtime-entry-v1` / `RUNTIME_ENTRY_AUTHORIZED` verdict.
The verdict does not assert evidence PASS or GA authorization. TLS and capacity
retain an explicit `local-validation` mode without a campaign; that mode remains
ineligible for GA evidence.

## Guarded entrypoints

The verifier runs before the first probe, collection, load or fault-injection
call in:

1. `probe_ga_target_tls.py` (`tls`);
2. `collect_ga_target_network.py` (`network`);
3. `collect_ga_target_secrets.py` (`secrets`);
4. `g2_target_capacity_gate.py` (`capacity`);
5. `collect_ga_target_alerting.py` (`alerting`);
6. `collect_ga_target_recovery.py` (`recovery`);
7. `collect_ga_state_services_ha.py` (`state_services`);
8. `collect_ga_target_ha.py` (`high_availability`).

Each production command requires `--execution-campaign` and
`--phase-action-id`. Repository tests and the production baseline pin the exact
phase mapping, both arguments and ordering of verification before the effect
call, preventing a later apparent-but-post-action check.

## Verified failure paths

The signed campaign fixture proves the successful runtime verdict and rejects:

- an action ID different from the signed statement;
- a different production target;
- a different source commit;
- a different Kubernetes context;
- a different recovery target;
- an expired, tampered or otherwise invalid phase permit through the inherited
  PGA-16 verifier.

All eight CLI parsers load successfully. The existing signed end-to-end fixture
is unchanged at 90 planned artifacts, 87 external artifacts, 111 captured
inputs and 290 explicit references because the runtime verdict is deliberately
ephemeral rather than a second source of authorization truth.

## Quality gates

- backend: 1234 passed, 19 skipped; selected core coverage 81.04% (minimum 65%);
- production-authorization suite: 132 tests;
- production-operations suite: 41 tests;
- affected GA collectors/authorization regression: 237 passed;
- frontend: 11 files / 56 tests, zero lint warnings, production build passed;
- dependency audit: Python and npm reported zero known vulnerabilities;
- static/contract: Ruff, compileall and frozen OpenAPI v2 drift check passed;
- data/config: 63 tracked JSON files, 33 tracked YAML files and four Compose
  configurations parsed successfully;
- type ratchet: 81 mypy errors, below the ceiling of 82;
- repository production baseline: 33/33 PASS.
