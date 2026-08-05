import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import OperationsPage from "../pages/OperationsPage";

const mocks = vi.hoisted(() => ({
  getGAReadiness: vi.fn(),
  getOverview: vi.fn(),
  listEvaluations: vi.fn(),
  listIncidents: vi.fn(),
  listRecoveryDrills: vi.fn(),
  evaluate: vi.fn(),
  acknowledgeIncident: vi.fn(),
  resolveIncident: vi.fn(),
}));

const evaluation = {
  public_id: "oslo_test",
  idempotency_key: "ops-test-evaluation",
  profile_version: "duckdock-ga-slo-v1",
  window_minutes: 15,
  request_count: 75,
  error_count: 0,
  http_error_ratio: 0,
  evidence_ingest_p95_ms: 50,
  run_timeline_p95_ms: 180,
  policy_decision_p95_ms: null,
  outbox_failed_count: 0,
  outbox_oldest_pending_age_seconds: 0,
  status: "DEGRADED",
  reason_codes: ["POLICY_DECISION_NO_TRAFFIC"],
  evidence_digest: "a".repeat(64),
  evaluated_by_user_id: 1,
  evaluated_at: "2026-08-04T08:00:00Z",
  created_at: "2026-08-04T08:00:00Z",
};

const incident = {
  public_id: "oinc_test",
  slo_evaluation_id: 1,
  severity: "CRITICAL",
  status: "OPEN",
  reason_codes: ["OUTBOX_FAILED_EVENTS_PRESENT"],
  evidence_digest: "b".repeat(64),
  acknowledged_by_user_id: null,
  acknowledged_at: null,
  resolved_by_user_id: null,
  resolved_at: null,
  created_at: "2026-08-04T08:01:00Z",
  updated_at: "2026-08-04T08:01:00Z",
};

const drill = {
  public_id: "odrl_test",
  idempotency_key: "ops-test-drill",
  environment: "local-dev",
  git_head: "674bc928ee0e213ce1356db4e20f98efca5921ea",
  backup_set_digest: "c".repeat(64),
  mysql_digest: "d".repeat(64),
  object_store_digest: "e".repeat(64),
  mysql_row_count: 3,
  object_count: 1,
  rpo_seconds: 0,
  rto_seconds: 1,
  status: "PASSED",
  reason_codes: [],
  evidence_digest: "f".repeat(64),
  executed_by_user_id: 1,
  started_at: "2026-08-04T08:00:00Z",
  finished_at: "2026-08-04T08:00:01Z",
  created_at: "2026-08-04T08:00:01Z",
};

vi.mock("../api/client", () => ({
  operationsApi: mocks,
}));

describe("OperationsPage", () => {
  beforeEach(() => {
    mocks.getGAReadiness.mockResolvedValue({
      data: {
        profile_version: "duckdock-2-ga-readiness-v1",
        contract_version: "2.0.0-rc.1",
        contract_digest: "9".repeat(64),
        expected_db_revision: "20260804_0062",
        current_db_revision: "20260804_0062",
        status: "READY_WITH_GAPS",
        pass_count: 11,
        warn_count: 1,
        block_count: 0,
        checked_at: "2026-08-04T08:02:00Z",
        checks: [
          {
            key: "database_revision",
            title: "Database migration head",
            status: "PASS",
            observed: "20260804_0062",
            expected: "20260804_0062",
            detail: "The live database matches the GA schema head.",
          },
          {
            key: "ga_slo",
            title: "Release SLO evaluation",
            status: "WARN",
            observed: "DEGRADED, samples=75, age=120s",
            expected: "HEALTHY within 1h",
            detail: "DEGRADED is a visible gap; BREACHED blocks the candidate.",
          },
        ],
      },
    });
    mocks.getOverview.mockResolvedValue({
      data: {
        profile_version: "duckdock-ga-slo-v1",
        metrics_path: "/metrics",
        thresholds: {
          http_error_ratio_max: 0.01,
          evidence_ingest_p95_ms_max: 200,
          run_timeline_p95_ms_max: 2000,
          policy_decision_p95_ms_max: 100,
          outbox_failed_count_max: 0,
          outbox_pending_age_seconds_max: 300,
          recovery_rpo_seconds_max: 900,
          recovery_rto_seconds_max: 14400,
        },
        route_metrics: [
          { route: "/api/v2/agent-runs", method: "GET", request_count: 25, error_count: 0, p95_ms: 180 },
        ],
        outbox: {
          pending_count: 0,
          leased_count: 0,
          expired_lease_count: 0,
          failed_count: 0,
          published_count: 99,
          oldest_pending_age_seconds: null,
        },
        latest_evaluation: evaluation,
        open_incidents: [incident],
        latest_recovery_drill: drill,
      },
    });
    mocks.listEvaluations.mockResolvedValue({ data: [evaluation] });
    mocks.listIncidents.mockResolvedValue({ data: [incident] });
    mocks.listRecoveryDrills.mockResolvedValue({ data: [drill] });
    mocks.evaluate.mockResolvedValue({ data: evaluation });
    mocks.acknowledgeIncident.mockResolvedValue({ data: { ...incident, status: "ACKNOWLEDGED" } });
    mocks.resolveIncident.mockResolvedValue({ data: { ...incident, status: "RESOLVED" } });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("renders SLO, incident, route metrics and real recovery evidence", async () => {
    const user = userEvent.setup();
    render(<OperationsPage />);

    expect(await screen.findByText("Operations & SLO")).toBeInTheDocument();
    expect(screen.getByText("Release Readiness")).toBeInTheDocument();
    expect(screen.getByText("READY_WITH_GAPS")).toBeInTheDocument();
    expect(screen.getByText("Database migration head")).toBeInTheDocument();
    expect(screen.getByText("duckdock-ga-slo-v1")).toBeInTheDocument();
    expect(screen.getByText("POLICY_DECISION_NO_TRAFFIC")).toBeInTheDocument();
    expect(screen.getByText("/api/v2/agent-runs")).toBeInTheDocument();
    expect(screen.getByText("OUTBOX_FAILED_EVENTS_PRESENT")).toBeInTheDocument();
    expect(screen.getByText("odrl_test")).toBeInTheDocument();
    expect(screen.getAllByText("RPO 0s · RTO 1s")).toHaveLength(2);

    await user.click(screen.getByRole("button", { name: "立即评估 SLO" }));
    await waitFor(() => expect(mocks.evaluate).toHaveBeenCalledWith(expect.stringContaining("ops-ui-slo")));

    await user.click(screen.getByRole("button", { name: "确认" }));
    await waitFor(() => expect(mocks.acknowledgeIncident).toHaveBeenCalledWith("oinc_test", expect.any(String)));
  });
});
