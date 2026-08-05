# Tasks: Foundation Tenant Remediation and Contract

- [x] **TC-001 [SPEC]** Record S1-D scope, safety rules and manifest contract.
- [x] **TC-002 [ADR]** Accept explicit per-ID administrator remediation as a typed historical governance fact.
- [x] **TC-003 [TEST]** Add red tests for manifest schema, permissions, validation, replay and atomic failure.
- [x] **TC-004 [CORE]** Implement remediation schema/service.
- [x] **TC-005 [OPS]** Implement dry-run/apply CLI and deterministic report.
- [x] **TC-006 [MYSQL]** Verify apply/replay/conflict transaction behavior on MySQL 8.x.
- [x] **TC-007 [TEST]** Add red tests for contract blocker counts and actionable failure.
- [x] **TC-008 [CORE]** Implement read-only contract preflight service/CLI.
- [x] **TC-009 [DATA]** Resolve the target environment and retain zero-blocker evidence. The Owner classified the 275 unresolved rows as disposable development test data; after a verified full backup and write quiescence they were deleted by exact table/count, then backfill and preflight returned zero blockers.
- [x] **TC-010 [DB]** Add non-null/index contract migration only after retained zero-blocker evidence. Revision `20260728_0029` adds five non-null constraints, four tenant-scoped indexes and the Binding tenant-key unique constraint.
- [x] **TC-011 [DOC]** Update runbook, Foundation tasks and SSOT progress.
- [x] **TC-012 [GATE]** Run full backend, MySQL, migration, type and Compose gates.
