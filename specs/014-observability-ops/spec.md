# DuckDock 2.0 Observability / Operations

**Status**: Completed
**Epic**: E08
**Gate**: G6 Operations lane
**Date**: 2026-08-04

## 1. Goal

Provide content-safe route-template metrics, explicit GA SLO evaluation, auditable incident lifecycle and a real MySQL/MinIO backup→delete→restore drill. Prometheus remains a replaceable optional component; DuckDock owns only the stable metrics and evidence contracts.

## 2. Acceptance slices

| ID | Acceptance |
|---|---|
| OPS-01 | Public `/metrics` exposes bounded method/route-template/status-class metrics without Namespace, public ID, prompt, output or credential labels. |
| OPS-02 | Versioned GA thresholds cover 5xx ratio, evidence ingest, run timeline, policy decision, Outbox age/failure and recovery RPO/RTO. |
| OPS-03 | Immutable idempotent SLO evaluations derive HEALTHY/DEGRADED/BREACHED and typed reason codes. |
| OPS-04 | BREACHED opens an auditable WARNING/CRITICAL incident; acknowledge and resolve are explicit operations. |
| OPS-05 | Prometheus config, scrape target and four alert rules run as a separate Compose profile. |
| OPS-06 | Recovery script performs real isolated MySQL and MinIO backup, deletion, restoration and independent digest verification. |
| OPS-07 | Recovery receipt derives PASSED/FAILED from measured RPO/RTO and restored counts. |
| OPS-08 | `/operations` presents thresholds, evidence, metrics, incidents and recovery receipts using real API data. |

All slices are complete. Evidence is recorded in [observability-ops-e08-20260804.md](evidence/observability-ops-e08-20260804.md).
