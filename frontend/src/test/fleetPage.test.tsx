import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import FleetPage from "../pages/FleetPage";
import { useI18nStore } from "../store/i18n";

const summary = vi.hoisted(() => ({
  namespace_id: 7,
  runtime_count: 1,
  healthy_count: 1,
  stale_count: 0,
  degraded_count: 0,
  drifted_count: 0,
  runtimes: [
    {
      runtime_public_id: `rt_${"a".repeat(32)}`,
      provider: "custom",
      name: "Hermes Production",
      runtime_status: "active",
      profile: "hermes-reporter",
      adapter_id: "hermes-reporter-pilot",
      adapter_version: "0.2.0",
      certified_capability_level: "DD-C1",
      accepted_capabilities: [
        "session_control",
        "run_control",
      ],
      rejected_capabilities: [],
      handshake_status: "ACTIVE",
      heartbeat_state: "HEALTHY",
      last_heartbeat_at: "2026-07-30T04:00:00Z",
      handshake_expires_at: "2026-07-31T04:00:00Z",
      config_drift: "NONE",
      collector_status: "not_applicable",
      collector_version: null,
      heartbeat_history: [
        {
          status: "ACTIVE",
          config_drift: "NONE",
          collector_status: "not_applicable",
          collector_version: null,
          observed_at: "2026-07-30T04:00:00Z",
        },
      ],
      namespace_telemetry_sink_count: 1,
      namespace_active_telemetry_sink_count: 1,
      latest_run_at: "2026-07-30T03:59:00Z",
      pending_pack_import_count: 0,
      quarantined_item_count: 0,
    },
  ],
}));

vi.mock("../api/client", () => ({
  namespacesApi: {
    list: vi.fn().mockResolvedValue({
      data: [{ id: 7, name: "production" }],
    }),
  },
  fleetApi: {
    summary: vi.fn().mockResolvedValue({ data: summary }),
  },
}));

describe("FleetPage", () => {
  beforeEach(() => {
    useI18nStore.setState({ locale: "zh" });
  });

  it("renders negotiated capability, drift and heartbeat history", async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <FleetPage />
      </QueryClientProvider>
    );

    expect(
      await screen.findByText("Hermes Production")
    ).toBeInTheDocument();
    expect(
      screen.getByText(`rt_${"a".repeat(32)}`)
    ).toBeInTheDocument();
    expect(screen.getByText("DD-C1")).toBeInTheDocument();
    expect(screen.getByText("hermes-reporter")).toBeInTheDocument();
    expect(screen.getByText("run_control")).toBeInTheDocument();
    expect(screen.getByText("最近心跳历史")).toBeInTheDocument();
    expect(screen.queryByText("Runtime #1")).not.toBeInTheDocument();
  });
});
