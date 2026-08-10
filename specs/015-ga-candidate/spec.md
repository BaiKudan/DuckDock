# DuckDock 2.0 Release Candidate

**Status**: RC validation completed
**Epic**: E09
**Gate**: G6 technical candidate
**Date**: 2026-08-05

## 1. Goal

Close API/UI/migration delivery with one truthful technical readiness gate. The gate reads existing E01–E08 evidence and does not invent a second release state machine.

## 2. Acceptance slices

| ID | Acceptance |
|---|---|
| GA-01 | Freeze a deterministic, self-contained `/api/v2` OpenAPI `2.0.0-rc.1` snapshot and fail CI on drift. |
| GA-02 | Keep v1 operational through DuckDock 2.x while every v1 response advertises deprecation, successor version and no fictional Sunset date. |
| GA-03 | Document endpoint-by-endpoint migration and compatibility rules. |
| GA-04 | Read-only GA readiness checks migration head, contract digest, Runtime, Package, Release receipt, Handover, identity lifecycle, key rotation, v1/v2 reconciliation, Outbox, recovery, SLO, incidents and Prometheus. |
| GA-05 | Overall status is READY, READY_WITH_GAPS or BLOCKED; any integrity/security/recovery breach blocks. |
| GA-06 | Operations UI exposes the complete check matrix with observed values and reasons. |
| GA-07 | A repeatable live script uses local Hermes and valid credentials to generate all SLO traffic categories and requires HEALTHY + READY. |
| GA-08 | Full backend/frontend regression, build, lint, migration drift, OpenAPI drift and local browser validation pass. |

The implementation slices and isolated RC validation are complete. The current evidence is [release-candidate-rc1-20260805.md](evidence/release-candidate-rc1-20260805.md). The 2026-08-04 result remains historical evidence in [ga-candidate-e09-20260804.md](evidence/ga-candidate-e09-20260804.md) and does not override the current `2.0.0-rc.1` identity.
