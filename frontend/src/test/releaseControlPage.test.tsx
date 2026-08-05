import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ReleaseControlPage from "../pages/ReleaseControlPage";

const mocks = vi.hoisted(() => ({
  listEnvironments: vi.fn(),
  listPolicies: vi.fn(),
  listCandidates: vi.fn(),
  listDecisions: vi.fn(),
  listExceptions: vi.fn(),
  listPromotions: vi.fn(),
  listRollbacks: vi.fn(),
  listCanaryEvaluations: vi.fn(),
  listEnvironmentReleases: vi.fn(),
  listReceipts: vi.fn(),
  listReceiptCredentials: vi.fn(),
  evaluateCandidate: vi.fn(),
}));

const environment = {
  public_id: "renv_staging",
  namespace_id: 7,
  name: "staging",
  kind: "STAGING",
  promotion_order: 20,
  protected: true,
  minimum_approvals: 1,
  requires_canary: false,
  status: "ACTIVE",
  created_by_user_id: 1,
  created_at: "2026-08-04T07:00:00Z",
};

const candidate = {
  public_id: "rcand_test",
  namespace_id: 7,
  package_public_id: "pkg_test",
  package_name: "hermes-agent",
  package_version_public_id: "pkgv_test",
  package_version: "1.2.3",
  package_manifest_digest: "a".repeat(64),
  deployment_public_id: "dep_test",
  deployment_revision: "hermes-2026.08.04",
  deployment_configuration_digest: "b".repeat(64),
  runtime_id: 9,
  target_environment_public_id: environment.public_id,
  target_environment_name: environment.name,
  target_environment_kind: environment.kind,
  baseline_candidate_public_id: null,
  idempotency_key: "candidate-test",
  candidate_digest: "c".repeat(64),
  schema_name: "duckdock.release-candidate",
  schema_version: "1.0",
  created_by_user_id: 1,
  created_at: "2026-08-04T07:01:00Z",
};

const decision = {
  public_id: "rpdec_test",
  namespace_id: 7,
  candidate_public_id: candidate.public_id,
  policy_public_id: "rpol_test",
  policy_version_public_id: "rpolv_test",
  policy_version: 1,
  policy_mode: "ENFORCE",
  raw_outcome: "PASS",
  enforcement_outcome: "ALLOW",
  would_block: false,
  reason_codes: ["all_rules_passed"],
  evidence_snapshot_digest: "d".repeat(64),
  decision_digest: "e".repeat(64),
  evaluation_duration_ms: 17,
  schema_name: "duckdock.release-policy-decision",
  schema_version: "1.0",
  evaluated_by_user_id: 2,
  created_at: "2026-08-04T07:02:00Z",
  rule_results: [
    {
      position: 0,
      rule_id: "package-evidence",
      rule_type: "PACKAGE_EVIDENCE_VERIFIED",
      verdict: "PASS",
      reason_code: "package_evidence_verified",
      evidence_kind: "package-verification",
      evidence_ref: "pkgv_test",
      evidence_digest: "f".repeat(64),
      metrics: {},
      observed_at: "2026-08-04T07:02:00Z",
    },
  ],
};

vi.mock("../api/client", () => ({
  namespacesApi: {
    list: vi.fn().mockResolvedValue({ data: [{ id: 7, name: "production" }] }),
  },
  deploymentApi: {
    list: vi.fn().mockResolvedValue({
      data: [
        {
          public_id: "dep_registered",
          namespace_id: 7,
          runtime_id: 9,
          package_version_public_id: "pkgv_test",
          environment: "staging",
          revision: "hermes-next",
          status: "REGISTERED",
          components: [],
        },
      ],
    }),
  },
  packageRegistryApi: {
    listPackages: vi.fn().mockResolvedValue({ data: [{ public_id: "pkg_test" }] }),
    listVersions: vi.fn().mockResolvedValue({
      data: [
        {
          public_id: "pkgv_test",
          package_name: "hermes-agent",
          version: "1.2.3",
        },
      ],
    }),
  },
  releaseControlApi: {
    listEnvironments: mocks.listEnvironments,
    listPolicies: mocks.listPolicies,
    listCandidates: mocks.listCandidates,
    listDecisions: mocks.listDecisions,
    listExceptions: mocks.listExceptions,
    listPromotions: mocks.listPromotions,
    listRollbacks: mocks.listRollbacks,
    listCanaryEvaluations: mocks.listCanaryEvaluations,
    listEnvironmentReleases: mocks.listEnvironmentReleases,
    listReceipts: mocks.listReceipts,
    listReceiptCredentials: mocks.listReceiptCredentials,
    evaluateCandidate: mocks.evaluateCandidate,
    createEnvironment: vi.fn(),
    createPolicy: vi.fn(),
    createPolicyVersion: vi.fn(),
    createCandidate: vi.fn(),
    approveCandidate: vi.fn(),
    createException: vi.fn(),
    reviewException: vi.fn(),
    createPromotion: vi.fn(),
    evaluateCanary: vi.fn(),
    requestRollback: vi.fn(),
    createReceiptCredential: vi.fn(),
    revokeReceiptCredential: vi.fn(),
  },
}));

describe("ReleaseControlPage", () => {
  beforeEach(() => {
    mocks.listEnvironments.mockResolvedValue({ data: [environment] });
    mocks.listPolicies.mockResolvedValue({
      data: [
        {
          public_id: "rpol_test",
          name: "protected-staging",
          versions: [
            {
              public_id: "rpolv_test",
              target_environment_public_id: environment.public_id,
              target_environment_name: environment.name,
              version: 1,
              mode: "ENFORCE",
            },
          ],
        },
      ],
    });
    mocks.listCandidates.mockResolvedValue({ data: [candidate] });
    mocks.listDecisions.mockResolvedValue({ data: [decision] });
    mocks.listExceptions.mockResolvedValue({ data: [] });
    mocks.listPromotions.mockResolvedValue({
      data: [
        {
          public_id: "rprom_test",
          dispatch_public_id: "rdisp_test",
          candidate_public_id: candidate.public_id,
          policy_decision_public_id: decision.public_id,
          target_environment_name: "staging",
          strategy: "CANARY",
          status: "OBSERVING",
          updated_at: "2026-08-04T07:03:00Z",
        },
      ],
    });
    mocks.listRollbacks.mockResolvedValue({
      data: [
        {
          public_id: "rrb_test",
          dispatch_public_id: "rdisp_rollback_test",
          namespace_id: 7,
          promotion_public_id: "rprom_test",
          environment_public_id: environment.public_id,
          source_candidate_public_id: candidate.public_id,
          target_environment_release_public_id: "renvr_previous",
          target_candidate_public_id: "rcand_previous",
          status: "SUCCEEDED",
          reason_code: "canary_policy_failed",
          dispatch_digest: "1".repeat(64),
          requested_by_user_id: 2,
          created_at: "2026-08-04T07:03:30Z",
          completed_at: "2026-08-04T07:04:00Z",
        },
      ],
    });
    mocks.listCanaryEvaluations.mockResolvedValue({
      data: [
        {
          public_id: "rcan_test",
          namespace_id: 7,
          promotion_public_id: "rprom_test",
          outcome: "FAIL",
          reason_codes: ["failure_rate_exceeded"],
          window_start: "2026-08-04T07:03:00Z",
          window_end: "2026-08-04T07:04:00Z",
          completed_run_count: 1,
          failed_run_count: 1,
          untrusted_run_count: 0,
          failure_rate: 1,
          untrusted_rate: 0,
          evidence_digest: "2".repeat(64),
          decision_digest: "3".repeat(64),
          evaluated_by_user_id: 2,
          created_at: "2026-08-04T07:04:00Z",
        },
      ],
    });
    mocks.listEnvironmentReleases.mockResolvedValue({
      data: [
        {
          public_id: "renvr_test",
          environment_public_id: environment.public_id,
          candidate_public_id: candidate.public_id,
          receipt_public_id: "rrcpt_applied",
          status: "ACTIVE",
          activated_at: "2026-08-04T07:00:00Z",
        },
      ],
    });
    mocks.listReceipts.mockResolvedValue({
      data: [
        {
          public_id: "rrcpt_mismatch",
          runtime_id: 9,
          kind: "PROMOTION",
          dispatch_public_id: "rdisp_bad",
          status: "MISMATCH",
          observed_package_version_public_id: "pkgv_test",
          observed_deployment_revision: "unexpected-revision",
          error_code: "runtime_release_evidence_mismatch",
          occurred_at: "2026-08-04T07:04:00Z",
        },
      ],
    });
    mocks.listReceiptCredentials.mockResolvedValue({
      data: [
        {
          id: 88,
          runtime_id: 9,
          name: "Hermes release reporter",
          token_prefix: "abcd1234",
          scopes: ["release.receipt"],
          is_active: true,
          expires_at: "2026-09-04T07:00:00Z",
        },
      ],
    });
    mocks.evaluateCandidate.mockResolvedValue({ data: decision });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("renders the release evidence loop and can evaluate an exact candidate", async () => {
    const user = userEvent.setup();
    render(<ReleaseControlPage />);

    expect(await screen.findByText("Agent Release Control")).toBeInTheDocument();
    expect(
      await screen.findByRole("option", { name: /hermes-agent v1\.2\.3 · hermes-2026\.08\.04/ }),
    ).toBeInTheDocument();
    expect(screen.getByText("package_evidence_verified")).toBeInTheDocument();
    expect(screen.getByText("OBSERVING")).toBeInTheDocument();
    expect(screen.getByText("MISMATCH")).toBeInTheDocument();
    expect(screen.getByText(/canary_policy_failed/)).toBeInTheDocument();
    expect(screen.getByText(/failure_rate_exceeded/)).toBeInTheDocument();
    expect(screen.getByText(/runtime_release_evidence_mismatch/)).toBeInTheDocument();
    expect(screen.getByText("Hermes release reporter")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "执行策略判定" }));
    await waitFor(() =>
      expect(mocks.evaluateCandidate).toHaveBeenCalledWith(
        candidate.public_id,
        expect.objectContaining({
          namespace_id: 7,
          policy_version_public_id: "rpolv_test",
        }),
      ),
    );
  });

  it("keeps release evidence readable when credential metadata is forbidden", async () => {
    mocks.listReceiptCredentials.mockRejectedValueOnce({ response: { status: 403 } });

    render(<ReleaseControlPage />);

    expect(await screen.findByText("Agent Release Control")).toBeInTheDocument();
    expect(await screen.findByText("package_evidence_verified")).toBeInTheDocument();
    expect(screen.getByText("OBSERVING")).toBeInTheDocument();
    expect(screen.queryByText("Hermes release reporter")).not.toBeInTheDocument();
  });
});
