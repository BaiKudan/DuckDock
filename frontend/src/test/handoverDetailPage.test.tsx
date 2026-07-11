import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { AIAsset, EvidenceItem, ExecutionAction, HandoverItem } from "../api/client";
import { EvidenceRow } from "../pages/HandoverDetailPage";
import { actionRequiresEvidence } from "../utils/handoverEvidence";

const evidenceBase: EvidenceItem = {
  id: 7,
  source_type: "user_confirm",
  source_provider: "custom",
  collection_job_id: null,
  object_uri: null,
  sha256: null,
  summary: "manual confirmation",
  confidence: 1,
  visibility: "normal",
  created_by: 1,
  created_at: "2026-06-25T00:00:00Z",
};

const actionBase: ExecutionAction = {
  id: 3,
  handover_case_id: 2,
  handover_item_id: 4,
  action_type: "transfer_owner",
  provider: "custom",
  status: "pending",
  execution_mode: "manual",
  request_json: null,
  result_json: null,
  evidence_ids: [],
  requires_evidence: false,
  idempotency_key: null,
  created_at: "2026-06-25T00:00:00Z",
  updated_at: "2026-06-25T00:00:00Z",
};

const itemBase: HandoverItem = {
  id: 4,
  handover_case_id: 2,
  asset_id: 5,
  recommended_action: "transfer_owner",
  receiver_user_id: null,
  risk_reason: null,
  confidence: 1,
  evidence_id: null,
  status: "executing",
  requires_evidence: false,
  created_at: "2026-06-25T00:00:00Z",
};

const assetBase: AIAsset = {
  id: 5,
  asset_type: "skill",
  name: "Customer skill",
  description: null,
  source_provider: "openclaw",
  source_runtime_id: null,
  external_id: null,
  status: "active",
  criticality: "medium",
  metadata_json: null,
  content_hash: null,
  first_seen_at: "2026-06-25T00:00:00Z",
  last_seen_at: "2026-06-25T00:00:00Z",
  created_at: "2026-06-25T00:00:00Z",
  updated_at: "2026-06-25T00:00:00Z",
};

describe("HandoverDetailPage helpers", () => {
  it("trusts the backend requires_evidence flag over criticality-only inference", () => {
    expect(
      actionRequiresEvidence(
        { ...actionBase, requires_evidence: true },
        { ...itemBase, requires_evidence: true },
        assetBase
      )
    ).toBe(true);
  });

  it("hides the download action when evidence has no object", () => {
    render(<EvidenceRow evidence={evidenceBase} disabled={false} onDownload={vi.fn()} />);

    expect(screen.queryByRole("button", { name: "下载" })).not.toBeInTheDocument();
    expect(screen.getByText("无可下载对象")).toBeInTheDocument();
  });

  it("shows the download action when object_uri is present", () => {
    render(
      <EvidenceRow
        evidence={{ ...evidenceBase, object_uri: "s3://duckdock-artifacts/handovers/case-2/evidence/proof.txt" }}
        disabled={false}
        onDownload={vi.fn()}
      />
    );

    expect(screen.getByRole("button", { name: "下载" })).toBeInTheDocument();
  });
});
