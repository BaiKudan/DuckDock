import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import EvalHubPage from "../pages/EvalHubPage";
import { useI18nStore } from "../store/i18n";

const mocks = vi.hoisted(() => ({
  listDatasets: vi.fn(),
  reviewBinding: vi.fn(),
  evaluateGate: vi.fn(),
  materializeTrace: vi.fn(),
  listCurationBatches: vi.fn(),
  listTraceCandidates: vi.fn(),
  createCurationBatch: vi.fn(),
  reviewCurationBatch: vi.fn(),
  materializeCurationBatch: vi.fn(),
  listSamplingPolicies: vi.fn(),
  listSamplingRuns: vi.fn(),
  createSamplingPolicy: vi.fn(),
  createSamplingPolicyVersion: vi.fn(),
  runSamplingPolicyVersion: vi.fn(),
  listAnnotationProviderQueues: vi.fn(),
  listAnnotationQueueBindings: vi.fn(),
  listAnnotationDispatches: vi.fn(),
  bindAnnotationQueue: vi.fn(),
  dispatchAnnotationBatch: vi.fn(),
  retryAnnotationDispatch: vi.fn(),
  reconcileAnnotationDispatch: vi.fn(),
  listPromotionPolicies: vi.fn(),
  listPromotionRuns: vi.fn(),
  createPromotionPolicy: vi.fn(),
  createPromotionPolicyVersion: vi.fn(),
  runPromotionPolicyVersion: vi.fn(),
  listCaseRoutingPolicies: vi.fn(),
  listCaseRoutingRuns: vi.fn(),
  createCaseRoutingPolicy: vi.fn(),
  createCaseRoutingPolicyVersion: vi.fn(),
  runCaseRoutingPolicyVersion: vi.fn(),
  listSemanticClusteringPolicies: vi.fn(),
  listSemanticClusteringRuns: vi.fn(),
  createSemanticClusteringPolicy: vi.fn(),
  createSemanticClusteringPolicyVersion: vi.fn(),
  runSemanticClusteringPolicyVersion: vi.fn(),
  listSemanticRegressionPolicies: vi.fn(),
  listSemanticRegressionComparisons: vi.fn(),
  createSemanticRegressionPolicy: vi.fn(),
  createSemanticRegressionPolicyVersion: vi.fn(),
  createSemanticRegressionComparison: vi.fn(),
  listSemanticMonitors: vi.fn(),
  listSemanticMonitorRuns: vi.fn(),
  listSemanticMonitorAlerts: vi.fn(),
  createSemanticMonitor: vi.fn(),
  pauseSemanticMonitor: vi.fn(),
  resumeSemanticMonitor: vi.fn(),
  runSemanticMonitorNow: vi.fn(),
  acknowledgeSemanticMonitorAlert: vi.fn(),
  listFailureTaxonomyPolicies: vi.fn(),
  listExperienceExtractionRuns: vi.fn(),
  createFailureTaxonomyPolicy: vi.fn(),
  createFailureTaxonomyPolicyVersion: vi.fn(),
  runFailureTaxonomyPolicyVersion: vi.fn(),
  reviewExperienceCandidate: vi.fn(),
  listExperienceAssets: vi.fn(),
  createExperienceAsset: vi.fn(),
  createExperienceAssetVersion: vi.fn(),
  requestExperienceActivation: vi.fn(),
  reviewExperienceActivation: vi.fn(),
}));

const testData = vi.hoisted(() => ({
  binding: {
    public_id: "rcb_test_exact",
    namespace_id: 7,
    release_candidate_ref: "candidate:hermes:2026-07-31",
    deployment_public_id: "dep_test_exact",
    deployment_revision: "eh06-1",
    deployment_configuration_digest: "a".repeat(64),
    evaluation_comparison_public_id: "cmp_test_exact",
    comparison_outcome: "PASS",
    comparison_reproducibility_digest: "b".repeat(64),
    candidate_evaluation_public_id: "eval_candidate_exact",
    candidate_manifest_public_id: "erm_candidate_exact",
    schema_name: "duckdock-release-candidate-evaluation-binding",
    schema_version: "1.0",
    binding_digest: "c".repeat(64),
    created_by_user_id: 1,
    created_at: "2026-07-31T04:00:00Z",
    review: null,
  },
}));

const binding = testData.binding;

vi.mock("../api/client", () => ({
  namespacesApi: {
    list: vi.fn().mockResolvedValue({
      data: [{ id: 7, name: "production" }],
    }),
  },
  evalHubApi: {
    listDatasets: mocks.listDatasets.mockResolvedValue({
      data: [
        {
          public_id: "eds_test",
          namespace_id: 7,
          name: "hermes-regression",
          provider: "LANGFUSE",
          provider_dataset_ref: "langfuse-dataset-test",
          status: "ACTIVE",
          versions: [{ public_id: "edv_test", version: 1 }],
          created_at: "2026-07-31T04:00:00Z",
          updated_at: "2026-07-31T04:00:00Z",
        },
      ],
    }),
    listDatasetMaterializations: vi.fn().mockResolvedValue({ data: [] }),
    listCurationBatches: mocks.listCurationBatches.mockResolvedValue({ data: [] }),
    listSamplingPolicies: mocks.listSamplingPolicies.mockResolvedValue({ data: [] }),
    listSamplingRuns: mocks.listSamplingRuns.mockResolvedValue({ data: [] }),
    listAnnotationProviderQueues: mocks.listAnnotationProviderQueues.mockResolvedValue({ data: [] }),
    listAnnotationQueueBindings: mocks.listAnnotationQueueBindings.mockResolvedValue({ data: [] }),
    listAnnotationDispatches: mocks.listAnnotationDispatches.mockResolvedValue({ data: [] }),
    listPromotionPolicies: mocks.listPromotionPolicies.mockResolvedValue({ data: [] }),
    listPromotionRuns: mocks.listPromotionRuns.mockResolvedValue({ data: [] }),
    listCaseRoutingPolicies: mocks.listCaseRoutingPolicies.mockResolvedValue({ data: [] }),
    listCaseRoutingRuns: mocks.listCaseRoutingRuns.mockResolvedValue({ data: [] }),
    listSemanticClusteringPolicies: mocks.listSemanticClusteringPolicies.mockResolvedValue({ data: [] }),
    listSemanticClusteringRuns: mocks.listSemanticClusteringRuns.mockResolvedValue({ data: [] }),
    listSemanticRegressionPolicies: mocks.listSemanticRegressionPolicies.mockResolvedValue({ data: [] }),
    listSemanticRegressionComparisons: mocks.listSemanticRegressionComparisons.mockResolvedValue({ data: [] }),
    listSemanticMonitors: mocks.listSemanticMonitors.mockResolvedValue({ data: [] }),
    listSemanticMonitorRuns: mocks.listSemanticMonitorRuns.mockResolvedValue({ data: [] }),
    listSemanticMonitorAlerts: mocks.listSemanticMonitorAlerts.mockResolvedValue({ data: [] }),
    listFailureTaxonomyPolicies: mocks.listFailureTaxonomyPolicies.mockResolvedValue({ data: [] }),
    listExperienceExtractionRuns: mocks.listExperienceExtractionRuns.mockResolvedValue({ data: [] }),
    listExperienceAssets: mocks.listExperienceAssets.mockResolvedValue({ data: [] }),
    listEvaluators: vi.fn().mockResolvedValue({
      data: [{ public_id: "evr_test", name: "quality-rule", status: "ACTIVE" }],
    }),
    listEvaluations: vi.fn().mockResolvedValue({
      data: [
        {
          public_id: "eval_candidate_exact",
          status: "COMPLETED",
          result_completeness: "COMPLETE",
        },
      ],
    }),
    listComparisons: vi.fn().mockResolvedValue({
      data: [
        {
          public_id: "cmp_test_exact",
          namespace_id: 7,
          outcome: "PASS",
          reason_code: "comparison_passed",
          baseline: {
            target_ref: "candidate:hermes:baseline",
            score: 0.8,
            pass_rate: 0.75,
            manifest_public_id: "erm_baseline_exact",
          },
          candidate: {
            target_ref: "candidate:hermes:2026-07-31",
            score: 0.9,
            pass_rate: 1,
            manifest_public_id: "erm_candidate_exact",
          },
          policy: { policy_name: "no-regression", version: 1 },
          score_delta: 0.1,
          pass_rate_delta: 0.25,
          reproducibility_digest: "b".repeat(64),
          created_at: "2026-07-31T04:00:00Z",
        },
      ],
    }),
    listBindings: vi.fn().mockResolvedValue({ data: [testData.binding] }),
    reviewBinding: mocks.reviewBinding.mockResolvedValue({
      data: {
        public_id: "rcr_test_exact",
        binding_public_id: testData.binding.public_id,
        decision: "APPROVED",
      },
    }),
    evaluateGate: mocks.evaluateGate.mockResolvedValue({
      data: {
        schema_name: "duckdock-candidate-release-gate",
        schema_version: "1.0",
        namespace_id: 7,
        release_candidate_ref: testData.binding.release_candidate_ref,
        deployment_public_id: testData.binding.deployment_public_id,
        deployment_revision: testData.binding.deployment_revision,
        outcome: "PASS",
        reason_codes: ["runtime_evaluation_passed"],
        evidence_count: 2,
        evidence: [
          { evidence_kind: "evaluation_comparison", verdict: "pass" },
          { evidence_kind: "manual_review", verdict: "pass" },
        ],
        decision_digest: "d".repeat(64),
      },
    }),
    materializeTrace: mocks.materializeTrace.mockResolvedValue({
      data: {
        public_id: "edm_test",
        dataset_public_id: "eds_test",
        dataset_version: 2,
      },
    }),
    listTraceCandidates: mocks.listTraceCandidates.mockResolvedValue({
      data: [
        {
          source_trace_ref: "1".repeat(32),
          source_observation_ref: "a".repeat(16),
          name: "duckdock.curation.candidate",
          observation_type: "SPAN",
          start_time: "2026-07-31T08:00:00Z",
          end_time: "2026-07-31T08:00:01Z",
          environment: "test",
          already_materialized: false,
          already_governed: false,
        },
        {
          source_trace_ref: "2".repeat(32),
          source_observation_ref: "b".repeat(16),
          name: "duckdock.curation.candidate",
          observation_type: "SPAN",
          start_time: "2026-07-31T08:01:00Z",
          end_time: "2026-07-31T08:01:01Z",
          environment: "test",
          already_materialized: false,
          already_governed: false,
        },
      ],
    }),
    createCurationBatch: mocks.createCurationBatch.mockResolvedValue({
      data: {
        public_id: "ecb_test",
        status: "PENDING_REVIEW",
        item_count: 2,
      },
    }),
    reviewCurationBatch: mocks.reviewCurationBatch.mockResolvedValue({
      data: {
        public_id: "ecb_test",
        status: "APPROVED",
      },
    }),
    materializeCurationBatch: mocks.materializeCurationBatch.mockResolvedValue({
      data: {
        public_id: "ecb_test",
        status: "MATERIALIZED",
      },
    }),
    createSamplingPolicy: mocks.createSamplingPolicy.mockResolvedValue({
      data: {
        public_id: "esp_test",
        namespace_id: 7,
        name: "production-stable-sample",
        status: "ACTIVE",
        versions: [],
      },
    }),
    createSamplingPolicyVersion: mocks.createSamplingPolicyVersion.mockResolvedValue({
      data: {
        public_id: "esv_test",
        version: 1,
        config_digest: "f".repeat(64),
        strategy: "STABLE_HASH",
        sample_size: 10,
        minimum_sample_size: 1,
        candidate_limit: 100,
        root_only: true,
        exclude_governed: true,
      },
    }),
    runSamplingPolicyVersion: mocks.runSamplingPolicyVersion.mockResolvedValue({
      data: {
        public_id: "esr_test",
        policy_version: 1,
        selected_count: 2,
        eligible_count: 3,
        curation_batch_public_id: "ecb_sampling_test",
      },
    }),
    bindAnnotationQueue: mocks.bindAnnotationQueue.mockResolvedValue({
      data: {
        public_id: "eaqb_test",
        provider_queue_ref: "queue-test",
        provider_queue_name: "duckdock-human-quality",
      },
    }),
    dispatchAnnotationBatch: mocks.dispatchAnnotationBatch.mockResolvedValue({
      data: {
        public_id: "ead_test",
        status: "PENDING",
        item_count: 2,
      },
    }),
    retryAnnotationDispatch: mocks.retryAnnotationDispatch.mockResolvedValue({
      data: { public_id: "ead_test", status: "PENDING" },
    }),
    reconcileAnnotationDispatch: mocks.reconcileAnnotationDispatch.mockResolvedValue({
      data: {
        public_id: "ead_test",
        status: "SYNCED",
        item_count: 2,
        completed_count: 1,
      },
    }),
    createPromotionPolicy: mocks.createPromotionPolicy.mockResolvedValue({
      data: {
        public_id: "epp_test",
        namespace_id: 7,
        name: "quality-diversity-promotion",
        status: "ACTIVE",
        versions: [],
      },
    }),
    createPromotionPolicyVersion: mocks.createPromotionPolicyVersion.mockResolvedValue({
      data: {
        public_id: "epv_test",
        version: 2,
        config_digest: "8".repeat(64),
      },
    }),
    runPromotionPolicyVersion: mocks.runPromotionPolicyVersion.mockResolvedValue({
      data: {
        public_id: "epr_test",
        outcome: "RECOMMENDED",
      },
    }),
    createCaseRoutingPolicy: mocks.createCaseRoutingPolicy.mockResolvedValue({
      data: {
        public_id: "ecrp_test",
        namespace_id: 7,
        name: "golden-bad-case-routing",
        status: "ACTIVE",
        versions: [],
      },
    }),
    createCaseRoutingPolicyVersion: mocks.createCaseRoutingPolicyVersion.mockResolvedValue({
      data: {
        public_id: "ecrv_test",
        version: 2,
        config_digest: "9".repeat(64),
      },
    }),
    runCaseRoutingPolicyVersion: mocks.runCaseRoutingPolicyVersion.mockResolvedValue({
      data: {
        public_id: "ecrr_test",
        outcome: "ROUTED",
        golden_selected_count: 2,
        bad_case_selected_count: 2,
      },
    }),
    createSemanticClusteringPolicy: mocks.createSemanticClusteringPolicy.mockResolvedValue({
      data: { public_id: "escp_test", namespace_id: 7, name: "semantic-failure-clustering", status: "ACTIVE", versions: [] },
    }),
    createSemanticClusteringPolicyVersion: mocks.createSemanticClusteringPolicyVersion.mockResolvedValue({
      data: { public_id: "escv_test", version: 1, config_digest: "b".repeat(64) },
    }),
    runSemanticClusteringPolicyVersion: mocks.runSemanticClusteringPolicyVersion.mockResolvedValue({
      data: { public_id: "escr_test", outcome: "CLUSTERED", cluster_count: 1, eligible_cluster_count: 1 },
    }),
    createSemanticRegressionPolicy: mocks.createSemanticRegressionPolicy.mockResolvedValue({
      data: { public_id: "esrp_test", namespace_id: 7, name: "semantic-cluster-regression", status: "ACTIVE", versions: [] },
    }),
    createSemanticRegressionPolicyVersion: mocks.createSemanticRegressionPolicyVersion.mockResolvedValue({
      data: { public_id: "esrv_test", version: 1, config_digest: "c".repeat(64) },
    }),
    createSemanticRegressionComparison: mocks.createSemanticRegressionComparison.mockResolvedValue({
      data: { public_id: "esrc_test", outcome: "PASS" },
    }),
    createSemanticMonitor: mocks.createSemanticMonitor.mockResolvedValue({
      data: { public_id: "esmp_test", status: "ACTIVE" },
    }),
    pauseSemanticMonitor: mocks.pauseSemanticMonitor.mockResolvedValue({
      data: { public_id: "esmp_test", status: "PAUSED" },
    }),
    resumeSemanticMonitor: mocks.resumeSemanticMonitor.mockResolvedValue({
      data: { public_id: "esmp_test", status: "ACTIVE" },
    }),
    runSemanticMonitorNow: mocks.runSemanticMonitorNow.mockResolvedValue({
      data: { public_id: "esmr_test", status: "QUEUED" },
    }),
    acknowledgeSemanticMonitorAlert: mocks.acknowledgeSemanticMonitorAlert.mockResolvedValue({
      data: { public_id: "esma_test", status: "ACKNOWLEDGED" },
    }),
    createFailureTaxonomyPolicy: mocks.createFailureTaxonomyPolicy.mockResolvedValue({
      data: {
        public_id: "eftp_test",
        namespace_id: 7,
        name: "failure-taxonomy",
        status: "ACTIVE",
        versions: [],
      },
    }),
    createFailureTaxonomyPolicyVersion: mocks.createFailureTaxonomyPolicyVersion.mockResolvedValue({
      data: {
        public_id: "eftv_test",
        version: 1,
        config_digest: "a".repeat(64),
      },
    }),
    runFailureTaxonomyPolicyVersion: mocks.runFailureTaxonomyPolicyVersion.mockResolvedValue({
      data: {
        public_id: "eer_test",
        outcome: "EXTRACTED",
        candidate_count: 1,
      },
    }),
    reviewExperienceCandidate: mocks.reviewExperienceCandidate.mockResolvedValue({
      data: {
        public_id: "eec_test",
        status: "APPROVED",
      },
    }),
    createExperienceAsset: mocks.createExperienceAsset.mockResolvedValue({
      data: {
        public_id: "eea_test",
        namespace_id: 7,
        source_candidate_public_id: "eec_test",
        name: "recovery-experience",
        versions: [],
      },
    }),
    createExperienceAssetVersion: mocks.createExperienceAssetVersion.mockResolvedValue({
      data: { public_id: "eea_test", versions: [] },
    }),
    requestExperienceActivation: mocks.requestExperienceActivation.mockResolvedValue({
      data: { public_id: "eea_test", versions: [] },
    }),
    reviewExperienceActivation: mocks.reviewExperienceActivation.mockResolvedValue({
      data: { public_id: "eea_test", versions: [] },
    }),
  },
}));

describe("EvalHubPage", () => {
  afterEach(() => cleanup());

  beforeEach(() => {
    useI18nStore.setState({ locale: "zh" });
    mocks.reviewBinding.mockClear();
    mocks.evaluateGate.mockClear();
    mocks.materializeTrace.mockClear();
    mocks.listCurationBatches.mockReset();
    mocks.listTraceCandidates.mockClear();
    mocks.createCurationBatch.mockClear();
    mocks.reviewCurationBatch.mockClear();
    mocks.materializeCurationBatch.mockReset();
    mocks.listSamplingPolicies.mockReset();
    mocks.listSamplingRuns.mockReset();
    mocks.createSamplingPolicy.mockClear();
    mocks.createSamplingPolicyVersion.mockClear();
    mocks.runSamplingPolicyVersion.mockClear();
    mocks.listAnnotationProviderQueues.mockReset();
    mocks.listAnnotationQueueBindings.mockReset();
    mocks.listAnnotationDispatches.mockReset();
    mocks.bindAnnotationQueue.mockClear();
    mocks.dispatchAnnotationBatch.mockClear();
    mocks.retryAnnotationDispatch.mockClear();
    mocks.reconcileAnnotationDispatch.mockClear();
    mocks.listPromotionPolicies.mockReset();
    mocks.listPromotionRuns.mockReset();
    mocks.createPromotionPolicy.mockClear();
    mocks.createPromotionPolicyVersion.mockClear();
    mocks.runPromotionPolicyVersion.mockClear();
    mocks.listDatasets.mockReset();
    mocks.listCaseRoutingPolicies.mockReset();
    mocks.listCaseRoutingRuns.mockReset();
    mocks.createCaseRoutingPolicy.mockClear();
    mocks.createCaseRoutingPolicyVersion.mockClear();
    mocks.runCaseRoutingPolicyVersion.mockClear();
    mocks.listSemanticClusteringPolicies.mockReset();
    mocks.listSemanticClusteringRuns.mockReset();
    mocks.createSemanticClusteringPolicy.mockClear();
    mocks.createSemanticClusteringPolicyVersion.mockClear();
    mocks.runSemanticClusteringPolicyVersion.mockClear();
    mocks.listSemanticRegressionPolicies.mockReset();
    mocks.listSemanticRegressionComparisons.mockReset();
    mocks.createSemanticRegressionPolicy.mockClear();
    mocks.createSemanticRegressionPolicyVersion.mockClear();
    mocks.createSemanticRegressionComparison.mockClear();
    mocks.listSemanticMonitors.mockReset();
    mocks.listSemanticMonitorRuns.mockReset();
    mocks.listSemanticMonitorAlerts.mockReset();
    mocks.createSemanticMonitor.mockClear();
    mocks.pauseSemanticMonitor.mockClear();
    mocks.resumeSemanticMonitor.mockClear();
    mocks.runSemanticMonitorNow.mockClear();
    mocks.acknowledgeSemanticMonitorAlert.mockClear();
    mocks.listFailureTaxonomyPolicies.mockReset();
    mocks.listExperienceExtractionRuns.mockReset();
    mocks.createFailureTaxonomyPolicy.mockClear();
    mocks.createFailureTaxonomyPolicyVersion.mockClear();
    mocks.runFailureTaxonomyPolicyVersion.mockClear();
    mocks.reviewExperienceCandidate.mockClear();
    mocks.listExperienceAssets.mockReset();
    mocks.createExperienceAsset.mockClear();
    mocks.createExperienceAssetVersion.mockClear();
    mocks.requestExperienceActivation.mockClear();
    mocks.reviewExperienceActivation.mockClear();
    mocks.listDatasets.mockResolvedValue({
      data: [
        {
          public_id: "eds_test",
          namespace_id: 7,
          name: "hermes-regression",
          provider: "LANGFUSE",
          provider_dataset_ref: "langfuse-dataset-test",
          status: "ACTIVE",
          versions: [{ public_id: "edv_test", version: 1 }],
          created_at: "2026-07-31T04:00:00Z",
          updated_at: "2026-07-31T04:00:00Z",
        },
      ],
    });
    mocks.listCurationBatches.mockResolvedValue({ data: [] });
    mocks.listSamplingPolicies.mockResolvedValue({ data: [] });
    mocks.listSamplingRuns.mockResolvedValue({ data: [] });
    mocks.listAnnotationProviderQueues.mockResolvedValue({ data: [] });
    mocks.listAnnotationQueueBindings.mockResolvedValue({ data: [] });
    mocks.listAnnotationDispatches.mockResolvedValue({ data: [] });
    mocks.listPromotionPolicies.mockResolvedValue({ data: [] });
    mocks.listPromotionRuns.mockResolvedValue({ data: [] });
    mocks.listCaseRoutingPolicies.mockResolvedValue({ data: [] });
    mocks.listCaseRoutingRuns.mockResolvedValue({ data: [] });
    mocks.listSemanticClusteringPolicies.mockResolvedValue({ data: [] });
    mocks.listSemanticClusteringRuns.mockResolvedValue({ data: [] });
    mocks.listSemanticRegressionPolicies.mockResolvedValue({ data: [] });
    mocks.listSemanticRegressionComparisons.mockResolvedValue({ data: [] });
    mocks.listSemanticMonitors.mockResolvedValue({ data: [] });
    mocks.listSemanticMonitorRuns.mockResolvedValue({ data: [] });
    mocks.listSemanticMonitorAlerts.mockResolvedValue({ data: [] });
    mocks.listFailureTaxonomyPolicies.mockResolvedValue({ data: [] });
    mocks.listExperienceExtractionRuns.mockResolvedValue({ data: [] });
    mocks.listExperienceAssets.mockResolvedValue({ data: [] });
    mocks.materializeCurationBatch.mockResolvedValue({
      data: {
        public_id: "ecb_test",
        status: "MATERIALIZED",
      },
    });
  });

  it("renders governance inventory and records immutable approval", async () => {
    const user = userEvent.setup();
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <EvalHubPage />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("hermes-regression")).toBeInTheDocument();
    expect(screen.getAllByText("1 / 1")).toHaveLength(2);

    await user.click(screen.getByRole("button", { name: "基线对比" }));
    expect(
      await screen.findAllByText("candidate:hermes:2026-07-31"),
    ).toHaveLength(2);
    expect(screen.getByText("comparison_passed", { exact: false })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "候选门禁" }));
    await user.click(screen.getByRole("button", { name: "批准证据" }));
    await user.type(screen.getByLabelText("评审备注"), "人工复核已完成");
    await user.click(screen.getByRole("button", { name: "确认批准" }));

    await waitFor(() => {
      expect(mocks.reviewBinding).toHaveBeenCalledWith(
        expect.objectContaining({
          namespace_id: 7,
          binding_public_id: binding.public_id,
          decision: "APPROVED",
          comment: "人工复核已完成",
        }),
        `eval-review-${binding.public_id}-approved`,
      );
    });
    expect(await screen.findByText("评审已批准，并作为不可变证据写入。")).toBeInTheDocument();
  });

  it("re-evaluates the exact candidate gate and shows its digest evidence", async () => {
    const user = userEvent.setup();
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <EvalHubPage />
      </QueryClientProvider>,
    );

    await user.click(await screen.findByRole("button", { name: "候选门禁" }));
    await user.click(screen.getByRole("button", { name: "复算门禁" }));

    expect(await screen.findByTestId("gate-decision")).toHaveTextContent("PASS");
    expect(screen.getByTestId("gate-decision")).toHaveTextContent("manual_review:pass");
    expect(mocks.evaluateGate).toHaveBeenCalledWith({
      namespace_id: 7,
      release_candidate_ref: binding.release_candidate_ref,
      deployment_public_id: binding.deployment_public_id,
      deployment_revision: binding.deployment_revision,
    });
  });

  it("materializes a Langfuse trace into a governed Dataset version", async () => {
    const user = userEvent.setup();
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <EvalHubPage />
      </QueryClientProvider>,
    );

    await user.click(await screen.findByRole("button", { name: "Trace2Dataset" }));
    await user.type(screen.getByLabelText("Trace ID"), "a".repeat(32));
    await user.type(screen.getByLabelText("Observation ID"), "b".repeat(16));
    await user.click(screen.getByRole("button", { name: "写入 Dataset 并固定版本" }));

    await waitFor(() => {
      expect(mocks.materializeTrace).toHaveBeenCalledWith(
        "eds_test",
        {
          trace_id: "a".repeat(32),
          observation_id: "b".repeat(16),
        },
        `trace2dataset-eds_test-${"a".repeat(32)}-${"b".repeat(16)}`,
      );
    });
    expect(
      await screen.findByText(
        "Trace 已写入 Langfuse Dataset，并生成不可变 DuckDock Dataset Version。",
      ),
    ).toBeInTheDocument();
  });

  it("searches metadata-only candidates and freezes a multi-source review batch", async () => {
    const user = userEvent.setup();
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <EvalHubPage />
      </QueryClientProvider>,
    );

    await user.click(await screen.findByRole("button", { name: "Trace2Dataset" }));
    await user.type(
      screen.getByLabelText("候选 Observation 名称"),
      "duckdock.curation.candidate",
    );
    await user.click(screen.getByRole("button", { name: "检索候选" }));

    const candidates = await screen.findAllByRole("checkbox", {
      name: "选择候选 duckdock.curation.candidate",
    });
    await user.click(candidates[0]);
    await user.click(candidates[1]);
    await user.click(screen.getByRole("button", { name: "提交审核（2）" }));

    await waitFor(() => {
      expect(mocks.listTraceCandidates).toHaveBeenCalledWith(
        "eds_test",
        expect.objectContaining({
          name: "duckdock.curation.candidate",
          root_only: true,
          limit: 100,
        }),
      );
      expect(mocks.createCurationBatch).toHaveBeenCalledWith(
        "eds_test",
        {
          items: [
            {
              trace_id: "1".repeat(32),
              observation_id: "a".repeat(16),
            },
            {
              trace_id: "2".repeat(32),
              observation_id: "b".repeat(16),
            },
          ],
        },
        expect.stringMatching(/^curation-submit-eds_test-[0-9a-f]{8}$/),
      );
    });
    expect(
      await screen.findByText("策展批次已提交审核；选中引用已冻结为不可变清单。"),
    ).toBeInTheDocument();
  });

  it("approves a curation batch and materializes it as one governed action", async () => {
    mocks.materializeCurationBatch
      .mockRejectedValueOnce(new Error("review commit still settling"))
      .mockResolvedValue({
        data: {
          public_id: "ecb_test",
          status: "MATERIALIZED",
        },
      });
    mocks.listCurationBatches.mockResolvedValue({
      data: [
        {
          public_id: "ecb_test",
          namespace_id: 7,
          dataset_public_id: "eds_test",
          status: "PENDING_REVIEW",
          selection_digest: "e".repeat(64),
          item_count: 2,
          schema_name: "langfuse-trace-dataset-curation",
          schema_version: "1.0",
          submitted_by_user_id: 1,
          created_at: "2026-07-31T08:00:00Z",
          items: [
            {
              position: 1,
              source_trace_ref: "1".repeat(32),
              source_observation_ref: "a".repeat(16),
              provider_dataset_item_ref: null,
            },
            {
              position: 2,
              source_trace_ref: "2".repeat(32),
              source_observation_ref: "b".repeat(16),
              provider_dataset_item_ref: null,
            },
          ],
          review: null,
          materialization: null,
        },
      ],
    });
    const user = userEvent.setup();
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <EvalHubPage />
      </QueryClientProvider>,
    );

    await user.click(await screen.findByRole("button", { name: "Trace2Dataset" }));
    await user.type(
      screen.getByLabelText("批次审核备注 ecb_test"),
      "人工复核通过",
    );
    await user.click(screen.getByRole("button", { name: "批准并批量入集" }));

    await waitFor(() => {
      expect(mocks.reviewCurationBatch).toHaveBeenCalledWith(
        "ecb_test",
        { decision: "APPROVED", comment: "人工复核通过" },
        expect.stringMatching(
          /^curation-review-ecb_test-approved-[0-9a-f]{8}$/,
        ),
      );
      expect(mocks.materializeCurationBatch).toHaveBeenCalledWith(
        "ecb_test",
        "curation-materialize-ecb_test",
      );
      expect(mocks.materializeCurationBatch).toHaveBeenCalledTimes(2);
    });
    expect(
      await screen.findByText(
        "批次已批准并完成批量入集；审核、来源清单和 Dataset Version 均不可覆盖。",
      ),
    ).toBeInTheDocument();
  });

  it("creates a versioned metadata-only sampling policy", async () => {
    const user = userEvent.setup();
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <EvalHubPage />
      </QueryClientProvider>,
    );

    await user.click(await screen.findByRole("button", { name: "Trace2Dataset" }));
    await user.click(screen.getByRole("button", { name: "创建策略与 v1" }));

    await waitFor(() => {
      expect(mocks.createSamplingPolicy).toHaveBeenCalledWith({
        namespace_id: 7,
        name: "production-stable-sample",
        description: "Reproducible metadata-only production sampling",
      });
      expect(mocks.createSamplingPolicyVersion).toHaveBeenCalledWith(
        "esp_test",
        expect.objectContaining({
          strategy: "STABLE_HASH",
          sample_size: 10,
          minimum_sample_size: 1,
          candidate_limit: 100,
          root_only: true,
        }),
      );
    });
    expect(
      await screen.findByText("采样策略及首个不可变版本已创建。"),
    ).toBeInTheDocument();
  });

  it("runs an explicit sampling policy version into the review queue", async () => {
    mocks.listSamplingPolicies.mockResolvedValue({
      data: [
        {
          public_id: "esp_test",
          namespace_id: 7,
          name: "production-stable-sample",
          description: null,
          status: "ACTIVE",
          versions: [
            {
              public_id: "esv_test",
              version: 1,
              config_digest: "f".repeat(64),
              strategy: "STABLE_HASH",
              sample_size: 2,
              minimum_sample_size: 1,
              candidate_limit: 100,
              observation_name: null,
              observation_type: null,
              environment: null,
              root_only: true,
              exclude_governed: true,
              schema_name: "duckdock-trace-sampling-policy",
              schema_version: "1.0",
              created_by_user_id: 1,
              created_at: "2026-07-31T10:00:00Z",
            },
          ],
          created_at: "2026-07-31T10:00:00Z",
          updated_at: "2026-07-31T10:00:00Z",
        },
      ],
    });
    const user = userEvent.setup();
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <EvalHubPage />
      </QueryClientProvider>,
    );

    await user.click(await screen.findByRole("button", { name: "Trace2Dataset" }));
    await user.click(screen.getByRole("button", { name: "按当前窗口运行" }));

    await waitFor(() => {
      expect(mocks.runSamplingPolicyVersion).toHaveBeenCalledWith(
        "esv_test",
        expect.objectContaining({ dataset_public_id: "eds_test" }),
        expect.stringMatching(/^sampling-run-esv_test-[0-9a-f]{8}$/),
      );
    });
    expect(
      await screen.findByText("采样完成：2 条来源已进入待审批批次。"),
    ).toBeInTheDocument();
  });

  it("binds a Langfuse annotation queue and durably dispatches a curation batch", async () => {
    mocks.listAnnotationProviderQueues.mockResolvedValue({
      data: [
        {
          provider_queue_ref: "queue-test",
          name: "duckdock-human-quality",
          description: "human quality review",
          score_config_ids: ["score-quality"],
          created_at: "2026-08-03T03:00:00Z",
          updated_at: "2026-08-03T03:00:00Z",
          already_bound: false,
        },
      ],
    });
    mocks.listAnnotationQueueBindings.mockResolvedValue({
      data: [
        {
          public_id: "eaqb_test",
          namespace_id: 7,
          provider: "LANGFUSE",
          provider_queue_ref: "queue-test",
          provider_queue_name: "duckdock-human-quality",
          score_config_ids: ["score-quality"],
          provider_updated_at: "2026-08-03T03:00:00Z",
          status: "ACTIVE",
          schema_name: "langfuse-annotation-queue-binding",
          schema_version: "1.0",
          created_by_user_id: 1,
          created_at: "2026-08-03T03:00:00Z",
          updated_at: "2026-08-03T03:00:00Z",
        },
      ],
    });
    mocks.listCurationBatches.mockResolvedValue({
      data: [
        {
          public_id: "ecb_annotation_test",
          namespace_id: 7,
          dataset_public_id: "eds_test",
          status: "PENDING_REVIEW",
          selection_digest: "e".repeat(64),
          item_count: 2,
          schema_name: "langfuse-trace-dataset-curation",
          schema_version: "1.0",
          submitted_by_user_id: 1,
          created_at: "2026-08-03T03:00:00Z",
          items: [],
          review: null,
          materialization: null,
        },
      ],
    });
    const user = userEvent.setup();
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <EvalHubPage />
      </QueryClientProvider>,
    );

    await user.click(await screen.findByRole("button", { name: "Trace2Dataset" }));
    await user.click(screen.getByRole("button", { name: "绑定并冻结配置" }));
    await waitFor(() => {
      expect(mocks.bindAnnotationQueue).toHaveBeenCalledWith({
        namespace_id: 7,
        provider_queue_ref: "queue-test",
      });
    });
    await user.click(screen.getByRole("button", { name: "持久化并异步派发" }));
    await waitFor(() => {
      expect(mocks.dispatchAnnotationBatch).toHaveBeenCalledWith(
        "eaqb_test",
        "ecb_annotation_test",
        expect.stringMatching(/^annotation-dispatch-eaqb_test-[0-9a-f]{8}$/),
      );
    });
    expect(
      await screen.findByText("标注派发意图已持久化，后台将幂等同步到 Langfuse。"),
    ).toBeInTheDocument();
  });

  it("creates a versioned promotion rule and evaluates only a completed dispatch", async () => {
    const binding = {
      public_id: "eaqb_promotion",
      namespace_id: 7,
      provider: "LANGFUSE",
      provider_queue_ref: "queue-promotion",
      provider_queue_name: "duckdock-promotion",
      score_config_ids: ["score-quality"],
      provider_updated_at: "2026-08-03T06:00:00Z",
      status: "ACTIVE",
      schema_name: "langfuse-annotation-queue-binding",
      schema_version: "v4",
      created_by_user_id: 1,
      created_at: "2026-08-03T06:00:00Z",
      updated_at: "2026-08-03T06:00:00Z",
    };
    const dispatch = {
      public_id: "ead_promotion",
      namespace_id: 7,
      binding_public_id: binding.public_id,
      provider_queue_ref: binding.provider_queue_ref,
      provider_queue_name: binding.provider_queue_name,
      curation_batch_public_id: "ecb_promotion",
      sampling_run_public_id: null,
      request_digest: "7".repeat(64),
      status: "SYNCED",
      item_count: 2,
      synced_count: 2,
      completed_count: 2,
      failed_count: 0,
      attempt_count: 1,
      error_code: null,
      schema_name: "duckdock-annotation-queue-dispatch",
      schema_version: "1.0",
      created_by_user_id: 1,
      started_at: "2026-08-03T06:00:00Z",
      synced_at: "2026-08-03T06:00:01Z",
      last_reconciled_at: "2026-08-03T06:01:00Z",
      created_at: "2026-08-03T06:00:00Z",
      updated_at: "2026-08-03T06:01:00Z",
      items: [],
    };
    const version = {
      public_id: "epv_promotion",
      version: 1,
      binding_public_id: binding.public_id,
      provider_queue_ref: binding.provider_queue_ref,
      config_digest: "8".repeat(64),
      score_config_id: "score-quality",
      score_data_type: "NUMERIC",
      minimum_numeric_score: 0.8,
      accepted_values: [],
      diversity_dimension: "NONE",
      min_distinct_buckets: 1,
      schema_name: "duckdock-annotation-promotion-policy",
      schema_version: "1.0",
      created_by_user_id: 1,
      created_at: "2026-08-03T06:00:00Z",
    };
    mocks.listAnnotationQueueBindings.mockResolvedValue({ data: [binding] });
    mocks.listAnnotationDispatches.mockResolvedValue({ data: [dispatch] });
    mocks.listPromotionPolicies.mockResolvedValue({
      data: [
        {
          public_id: "epp_promotion",
          namespace_id: 7,
          name: "quality-diversity-promotion",
          description: null,
          status: "ACTIVE",
          versions: [version],
          created_at: "2026-08-03T06:00:00Z",
          updated_at: "2026-08-03T06:00:00Z",
        },
      ],
    });
    mocks.listPromotionRuns.mockResolvedValue({ data: [] });

    const user = userEvent.setup();
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <EvalHubPage />
      </QueryClientProvider>,
    );

    await user.click(await screen.findByRole("button", { name: "Trace2Dataset" }));
    await user.click(screen.getByRole("button", { name: "新增当前规则版本" }));
    await waitFor(() => {
      expect(mocks.createPromotionPolicyVersion).toHaveBeenCalledWith(
        "epp_promotion",
        expect.objectContaining({
          binding_public_id: "eaqb_promotion",
          score_config_id: "score-quality",
          score_data_type: "NUMERIC",
          minimum_numeric_score: 0.8,
          diversity_dimension: "NONE",
          min_distinct_buckets: 1,
        }),
      );
    });

    await user.click(screen.getByRole("button", { name: "评估已完成派发" }));
    await waitFor(() => {
      expect(mocks.runPromotionPolicyVersion).toHaveBeenCalledWith(
        "epv_promotion",
        "ead_promotion",
        expect.stringMatching(/^promotion-run-epv_promotion-[0-9a-f]{8}$/),
      );
    });
    expect(
      await screen.findByText("Promotion 评估完成：RECOMMENDED；仍需人工审批后才能入集。"),
    ).toBeInTheDocument();
  });

  it("routes multiple Promotion runs into independent Golden and Bad Case review batches", async () => {
    const sourceVersion = {
      public_id: "epv_clustered",
      version: 1,
      binding_public_id: "eaqb_clustered",
      provider_queue_ref: "queue-clustered",
      config_digest: "8".repeat(64),
      score_config_id: "score-quality",
      score_data_type: "NUMERIC",
      minimum_numeric_score: 0.8,
      accepted_values: [],
      diversity_dimension: "OBSERVATION_NAME",
      min_distinct_buckets: 2,
      schema_name: "duckdock-annotation-promotion-policy",
      schema_version: "1.0",
      created_by_user_id: 1,
      created_at: "2026-08-03T08:00:00Z",
    };
    const routingVersion = {
      public_id: "ecrv_clustered",
      version: 1,
      source_promotion_policy_public_id: "epp_clustered",
      source_promotion_policy_version_public_id: sourceVersion.public_id,
      source_promotion_policy_version: 1,
      config_digest: "9".repeat(64),
      strategy: "CLUSTER_ROUND_ROBIN",
      golden_target_size: 20,
      golden_min_items: 1,
      bad_case_target_size: 20,
      bad_case_min_items: 1,
      schema_name: "duckdock-evaluation-case-routing-policy",
      schema_version: "1.0",
      created_by_user_id: 1,
      created_at: "2026-08-03T08:00:00Z",
    };
    mocks.listDatasets.mockResolvedValue({
      data: [
        {
          public_id: "eds_golden",
          namespace_id: 7,
          name: "golden-cases",
          provider: "LANGFUSE",
          provider_dataset_ref: "langfuse-golden",
          status: "ACTIVE",
          versions: [],
          created_at: "2026-08-03T08:00:00Z",
          updated_at: "2026-08-03T08:00:00Z",
        },
        {
          public_id: "eds_bad_cases",
          namespace_id: 7,
          name: "bad-cases",
          provider: "LANGFUSE",
          provider_dataset_ref: "langfuse-bad-cases",
          status: "ACTIVE",
          versions: [],
          created_at: "2026-08-03T08:00:00Z",
          updated_at: "2026-08-03T08:00:00Z",
        },
      ],
    });
    mocks.listPromotionPolicies.mockResolvedValue({
      data: [
        {
          public_id: "epp_clustered",
          namespace_id: 7,
          name: "clustered-promotion",
          description: null,
          status: "ACTIVE",
          versions: [sourceVersion],
          created_at: "2026-08-03T08:00:00Z",
          updated_at: "2026-08-03T08:00:00Z",
        },
      ],
    });
    mocks.listPromotionRuns.mockResolvedValue({
      data: [
        {
          public_id: "epr_batch_one",
          policy_version_public_id: sourceVersion.public_id,
          outcome: "RECOMMENDED",
          passed_count: 2,
          item_count: 3,
          distinct_bucket_count: 2,
          reason_codes: ["quality_and_diversity_passed"],
          curation_batch_public_id: "ecb_batch_one",
          evidence_digest: "a".repeat(64),
          created_at: "2026-08-03T08:01:00Z",
        },
        {
          public_id: "epr_batch_two",
          policy_version_public_id: sourceVersion.public_id,
          outcome: "BLOCKED",
          passed_count: 1,
          item_count: 3,
          distinct_bucket_count: 2,
          reason_codes: ["quality_threshold_failed"],
          curation_batch_public_id: "ecb_batch_two",
          evidence_digest: "b".repeat(64),
          created_at: "2026-08-03T08:02:00Z",
        },
      ],
    });
    mocks.listCaseRoutingPolicies.mockResolvedValue({
      data: [
        {
          public_id: "ecrp_clustered",
          namespace_id: 7,
          name: "golden-bad-case-routing",
          description: null,
          status: "ACTIVE",
          versions: [routingVersion],
          created_at: "2026-08-03T08:00:00Z",
          updated_at: "2026-08-03T08:00:00Z",
        },
      ],
    });
    mocks.listCaseRoutingRuns.mockResolvedValue({ data: [] });

    const user = userEvent.setup();
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <EvalHubPage />
      </QueryClientProvider>,
    );

    await user.click(await screen.findByRole("button", { name: "Trace2Dataset" }));
    await user.click(screen.getByRole("button", { name: "新增当前路由版本" }));
    await waitFor(() => {
      expect(mocks.createCaseRoutingPolicyVersion).toHaveBeenCalledWith(
        "ecrp_clustered",
        {
          source_promotion_policy_version_public_id: "epv_clustered",
          strategy: "CLUSTER_ROUND_ROBIN",
          golden_target_size: 20,
          golden_min_items: 1,
          bad_case_target_size: 20,
          bad_case_min_items: 1,
        },
      );
    });

    await user.selectOptions(screen.getByLabelText("路由 Promotion 运行"), [
      "epr_batch_one",
      "epr_batch_two",
    ]);
    await user.click(screen.getByRole("button", { name: "执行跨批次路由" }));

    await waitFor(() => {
      expect(mocks.runCaseRoutingPolicyVersion).toHaveBeenCalledWith(
        "ecrv_clustered",
        {
          promotion_run_public_ids: ["epr_batch_one", "epr_batch_two"],
          golden_dataset_public_id: "eds_golden",
          bad_case_dataset_public_id: "eds_bad_cases",
        },
        expect.stringMatching(/^case-routing-run-ecrv_clustered-[0-9a-f]{8}$/),
      );
    });
    expect(
      await screen.findByText(
        "跨批次路由完成：ROUTED；Golden 2 条，Bad Case 2 条。两个批次仍需分别人工审批。",
      ),
    ).toBeInTheDocument();
  });

  it("extracts and reviews metadata-only Experience candidates", async () => {
    const routingVersion = {
      public_id: "ecrv_experience",
      version: 1,
      source_promotion_policy_public_id: "epp_experience",
      source_promotion_policy_version_public_id: "epv_experience",
      source_promotion_policy_version: 1,
      config_digest: "1".repeat(64),
      strategy: "CLUSTER_ROUND_ROBIN",
      golden_target_size: 4,
      golden_min_items: 1,
      bad_case_target_size: 4,
      bad_case_min_items: 1,
      schema_name: "duckdock-case-routing-policy",
      schema_version: "1.0",
      created_by_user_id: 1,
      created_at: "2026-08-03T09:00:00Z",
    };
    const routingRun = {
      public_id: "ecrr_experience",
      namespace_id: 7,
      policy_public_id: "ecrp_experience",
      policy_version_public_id: routingVersion.public_id,
      policy_version: 1,
      source_promotion_policy_version_public_id: "epv_experience",
      golden_dataset_public_id: "eds_golden",
      bad_case_dataset_public_id: "eds_bad",
      golden_curation_batch_public_id: "ecb_golden",
      bad_case_curation_batch_public_id: "ecb_bad",
      request_digest: "2".repeat(64),
      evidence_digest: "3".repeat(64),
      routing_digest: "4".repeat(64),
      outcome: "ROUTED",
      reason_codes: ["golden_and_bad_case_subsets_routed"],
      source_run_count: 2,
      candidate_count: 6,
      golden_candidate_count: 4,
      bad_case_candidate_count: 2,
      excluded_count: 0,
      golden_selected_count: 4,
      bad_case_selected_count: 2,
      golden_cluster_count: 2,
      bad_case_cluster_count: 1,
      schema_name: "duckdock-case-routing-run",
      schema_version: "1.0",
      created_by_user_id: 1,
      created_at: "2026-08-03T09:01:00Z",
      items: [],
    };
    const taxonomyVersion = {
      public_id: "eftv_experience",
      version: 1,
      source_case_routing_policy_public_id: "ecrp_experience",
      source_case_routing_policy_version_public_id: routingVersion.public_id,
      source_case_routing_policy_version: 1,
      config_digest: "5".repeat(64),
      min_cluster_occurrences: 2,
      min_source_runs: 2,
      include_isolated: false,
      max_candidates: 20,
      schema_name: "duckdock-failure-taxonomy-policy",
      schema_version: "1.0",
      created_by_user_id: 1,
      created_at: "2026-08-03T09:02:00Z",
    };
    const candidate = {
      public_id: "eec_experience",
      position: 1,
      category: "CROSS_RUN_RECURRING",
      status: "PENDING_REVIEW",
      cluster_digest: "6".repeat(64),
      rank_digest: "7".repeat(64),
      evidence_digest: "8".repeat(64),
      source_item_count: 2,
      source_run_count: 2,
      reason_code: "metadata_failure_cluster_candidate",
      created_at: "2026-08-03T09:03:00Z",
      evidence_items: [],
      review: null,
    };
    mocks.listCaseRoutingPolicies.mockResolvedValue({
      data: [
        {
          public_id: "ecrp_experience",
          namespace_id: 7,
          name: "golden-bad-case-routing",
          description: null,
          status: "ACTIVE",
          versions: [routingVersion],
          created_at: "2026-08-03T09:00:00Z",
          updated_at: "2026-08-03T09:00:00Z",
        },
      ],
    });
    mocks.listCaseRoutingRuns.mockResolvedValue({ data: [routingRun] });
    mocks.listFailureTaxonomyPolicies.mockResolvedValue({
      data: [
        {
          public_id: "eftp_experience",
          namespace_id: 7,
          name: "failure-taxonomy",
          description: null,
          status: "ACTIVE",
          versions: [taxonomyVersion],
          created_at: "2026-08-03T09:02:00Z",
          updated_at: "2026-08-03T09:02:00Z",
        },
      ],
    });
    mocks.listExperienceExtractionRuns.mockResolvedValue({
      data: [
        {
          public_id: "eer_experience",
          namespace_id: 7,
          policy_public_id: "eftp_experience",
          policy_version_public_id: taxonomyVersion.public_id,
          policy_version: 1,
          source_case_routing_run_public_id: routingRun.public_id,
          request_digest: "9".repeat(64),
          evidence_digest: "a".repeat(64),
          extraction_digest: "b".repeat(64),
          outcome: "EXTRACTED",
          reason_codes: ["experience_candidates_extracted"],
          source_bad_case_count: 2,
          cluster_count: 1,
          eligible_cluster_count: 1,
          candidate_count: 1,
          schema_name: "duckdock-experience-extraction-run",
          schema_version: "1.0",
          created_by_user_id: 1,
          created_at: "2026-08-03T09:03:00Z",
          candidates: [candidate],
        },
      ],
    });

    const user = userEvent.setup();
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <EvalHubPage />
      </QueryClientProvider>,
    );

    await user.click(await screen.findByRole("button", { name: "Trace2Dataset" }));
    await user.click(screen.getByRole("button", { name: "新增分类版本" }));
    await waitFor(() => {
      expect(mocks.createFailureTaxonomyPolicyVersion).toHaveBeenCalledWith(
        "eftp_experience",
        {
          source_case_routing_policy_version_public_id: "ecrv_experience",
          source_semantic_clustering_policy_version_public_id: null,
          min_cluster_occurrences: 2,
          min_source_runs: 2,
          include_isolated: false,
          max_candidates: 20,
        },
      );
    });

    await user.click(screen.getByRole("button", { name: "提取 Experience 候选" }));
    await waitFor(() => {
      expect(mocks.runFailureTaxonomyPolicyVersion).toHaveBeenCalledWith(
        "eftv_experience",
        "ecrr_experience",
        null,
        expect.stringMatching(/^experience-extraction-eftv_experience-[0-9a-f]{8}$/),
      );
    });

    await user.click(screen.getByRole("button", { name: "批准候选" }));
    await waitFor(() => {
      expect(mocks.reviewExperienceCandidate).toHaveBeenCalledWith(
        "eec_experience",
        { decision: "APPROVED", comment: null },
      );
    });
    expect(
      await screen.findByText(
        "Experience 候选已批准；该决定只确认候选，不会自动修改生产 Agent。",
      ),
    ).toBeInTheDocument();
  });

  it("authors versioned Experience assets and exposes four-eyes activation", async () => {
    const unclaimedCandidate = {
      public_id: "eec_unclaimed",
      position: 1,
      category: "SINGLE_RUN_RECURRING",
      status: "APPROVED",
      cluster_digest: "1".repeat(64),
      rank_digest: "2".repeat(64),
      evidence_digest: "3".repeat(64),
      source_item_count: 2,
      source_run_count: 1,
      reason_code: "metadata_failure_cluster_candidate",
      created_at: "2026-08-03T10:00:00Z",
      evidence_items: [],
      review: null,
    };
    mocks.listExperienceExtractionRuns.mockResolvedValue({
      data: [
        {
          public_id: "eer_assets",
          namespace_id: 7,
          policy_public_id: "eftp_assets",
          policy_version_public_id: "eftv_assets",
          policy_version: 1,
          source_case_routing_run_public_id: "ecrr_assets",
          request_digest: "4".repeat(64),
          evidence_digest: "5".repeat(64),
          extraction_digest: "6".repeat(64),
          outcome: "EXTRACTED",
          reason_codes: ["experience_candidates_extracted"],
          source_bad_case_count: 2,
          cluster_count: 1,
          eligible_cluster_count: 1,
          candidate_count: 1,
          schema_name: "duckdock-experience-extraction-run",
          schema_version: "1.0",
          created_by_user_id: 1,
          created_at: "2026-08-03T10:00:00Z",
          candidates: [unclaimedCandidate],
        },
      ],
    });
    mocks.listExperienceAssets.mockResolvedValue({
      data: [
        {
          public_id: "eea_governed",
          namespace_id: 7,
          source_candidate_public_id: "eec_claimed",
          source_candidate_category: "CROSS_RUN_RECURRING",
          source_candidate_evidence_digest: "7".repeat(64),
          name: "recover-recurring-failure",
          description: "governed Experience",
          created_by_user_id: 11,
          created_at: "2026-08-03T10:01:00Z",
          versions: [
            {
              public_id: "eeav_draft",
              version: 1,
              status: "DRAFT",
              body: "先验证输入约束，然后执行可恢复路径并记录结果。",
              applicability: "适用于重复失败簇。",
              change_summary: "initial",
              content_digest: "8".repeat(64),
              source_evidence_digest: "7".repeat(64),
              schema_name: "duckdock-experience-asset-version",
              schema_version: "1.0",
              created_by_user_id: 11,
              created_at: "2026-08-03T10:02:00Z",
              activation_request: null,
            },
            {
              public_id: "eeav_pending",
              version: 2,
              status: "PENDING_ACTIVATION",
              body: "恢复完成后增加结构化复核，并保留结果摘要。",
              applicability: "适用于证据充分的重复失败簇。",
              change_summary: "add verification",
              content_digest: "9".repeat(64),
              source_evidence_digest: "7".repeat(64),
              schema_name: "duckdock-experience-asset-version",
              schema_version: "1.0",
              created_by_user_id: 11,
              created_at: "2026-08-03T10:03:00Z",
              activation_request: {
                public_id: "eear_pending",
                request_note: "ready",
                request_digest: "a".repeat(64),
                requested_by_user_id: 11,
                created_at: "2026-08-03T10:04:00Z",
                review: null,
              },
            },
          ],
        },
      ],
    });

    const user = userEvent.setup();
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <EvalHubPage />
      </QueryClientProvider>,
    );

    await user.click(await screen.findByRole("button", { name: "Trace2Dataset" }));
    expect(await screen.findByText("Experience 资产与独立激活")).toBeInTheDocument();

    await user.type(screen.getByLabelText("Experience 经验正文"), "这是一个全新的不可变经验草稿正文。 ");
    await user.type(screen.getByLabelText("Experience 适用边界"), "仅用于重复失败簇。 ");
    await user.click(screen.getByRole("button", { name: "创建不可变草稿" }));
    await waitFor(() => {
      expect(mocks.createExperienceAssetVersion).toHaveBeenCalledWith(
        "eea_governed",
        {
          body: "这是一个全新的不可变经验草稿正文。",
          applicability: "仅用于重复失败簇。",
          change_summary: null,
        },
      );
    });

    await user.type(screen.getByLabelText("Experience 激活申请说明"), "ready for review");
    await user.click(screen.getByRole("button", { name: "申请激活" }));
    await waitFor(() => {
      expect(mocks.requestExperienceActivation).toHaveBeenCalledWith(
        "eeav_draft",
        { request_note: "ready for review" },
      );
    });

    await user.type(
      screen.getByLabelText("Experience 激活审批意见 eear_pending"),
      "different reviewer approved",
    );
    await user.click(screen.getByRole("button", { name: "批准激活" }));
    await waitFor(() => {
      expect(mocks.reviewExperienceActivation).toHaveBeenCalledWith(
        "eear_pending",
        { decision: "APPROVED", comment: "different reviewer approved" },
      );
    });

    await user.type(screen.getByLabelText("Experience 资产名称"), "new-experience");
    await user.click(screen.getByRole("button", { name: "创建资产" }));
    await waitFor(() => {
      expect(mocks.createExperienceAsset).toHaveBeenCalledWith({
        namespace_id: 7,
        source_candidate_public_id: "eec_unclaimed",
        name: "new-experience",
        description: null,
      });
    });
  });

  it("runs the semantic cluster quality and drift gate", async () => {
    const semanticRun = (publicId: string, policyVersionId: string) => ({
      public_id: publicId,
      namespace_id: 7,
      policy_public_id: `escp_${publicId}`,
      policy_version_public_id: policyVersionId,
      policy_version: 1,
      source_case_routing_run_public_id: "ecrr_shared",
      request_digest: "1".repeat(64),
      evidence_digest: "2".repeat(64),
      clustering_digest: "3".repeat(64),
      outcome: "CLUSTERED",
      reason_codes: ["semantic_clusters_created"],
      source_item_count: 2,
      cluster_count: 1,
      eligible_cluster_count: 1,
      schema_name: "duckdock-semantic-clustering-run",
      schema_version: "1.0",
      created_by_user_id: 1,
      created_at: "2026-08-03T12:00:00Z",
      items: [],
    });
    mocks.listSemanticClusteringRuns.mockResolvedValue({
      data: [
        semanticRun("escr_baseline", "escv_baseline"),
        semanticRun("escr_candidate", "escv_candidate"),
      ],
    });
    mocks.listSemanticRegressionPolicies.mockResolvedValue({
      data: [
        {
          public_id: "esrp_quality",
          namespace_id: 7,
          name: "semantic-cluster-regression",
          description: null,
          status: "ACTIVE",
          versions: [
            {
              public_id: "esrv_quality",
              version: 1,
              config_digest: "4".repeat(64),
              minimum_pairwise_assignment_agreement: 0.9,
              maximum_cluster_count_change_ratio: 0.5,
              maximum_mean_centroid_similarity_drop: 0.05,
              maximum_eligible_cluster_ratio_drop: 0.25,
              require_exact_source_content: true,
              schema_name: "duckdock-semantic-clustering-regression-policy",
              schema_version: "1.0",
              created_by_user_id: 1,
              created_at: "2026-08-03T12:01:00Z",
            },
          ],
          created_at: "2026-08-03T12:01:00Z",
          updated_at: "2026-08-03T12:01:00Z",
        },
      ],
    });

    const user = userEvent.setup();
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <EvalHubPage />
      </QueryClientProvider>,
    );

    await user.click(await screen.findByRole("button", { name: "Trace2Dataset" }));
    expect(await screen.findByText("语义簇质量与漂移门禁")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "执行离线门禁" }));
    await waitFor(() => {
      expect(mocks.createSemanticRegressionComparison).toHaveBeenCalledWith(
        {
          namespace_id: 7,
          baseline_run_public_id: "escr_baseline",
          candidate_run_public_id: "escr_candidate",
          policy_version_public_id: "esrv_quality",
        },
        expect.any(String),
      );
    });
  });

  it("creates and operates a scheduled semantic drift monitor", async () => {
    mocks.listSemanticClusteringPolicies.mockResolvedValue({
      data: [
        {
          public_id: "escp_monitor",
          namespace_id: 7,
          name: "semantic-monitor-source",
          description: null,
          status: "ACTIVE",
          versions: [
            {
              public_id: "escv_monitor",
              version: 1,
              source_case_routing_policy_public_id: "ecrp_monitor",
              source_case_routing_policy_version_public_id: "ecrv_monitor",
              source_case_routing_policy_version: 1,
              config_digest: "1".repeat(64),
              embedding_profile: "test-local",
              model_ref: "test-bge",
              dimensions: 2,
              similarity_threshold: 0.95,
              min_cluster_size: 2,
              max_items: 2,
              max_content_chars: 800,
              schema_name: "duckdock-semantic-clustering-policy",
              schema_version: "1.0",
              created_by_user_id: 1,
              created_at: "2026-08-04T00:00:00Z",
            },
          ],
          created_at: "2026-08-04T00:00:00Z",
          updated_at: "2026-08-04T00:00:00Z",
        },
      ],
    });
    mocks.listSemanticClusteringRuns.mockResolvedValue({
      data: [
        {
          public_id: "escr_monitor_baseline",
          namespace_id: 7,
          policy_public_id: "escp_monitor",
          policy_version_public_id: "escv_monitor",
          policy_version: 1,
          source_case_routing_run_public_id: "ecrr_monitor",
          request_digest: "2".repeat(64),
          evidence_digest: "3".repeat(64),
          clustering_digest: "4".repeat(64),
          outcome: "CLUSTERED",
          reason_codes: ["semantic_failure_clusters_found"],
          source_item_count: 2,
          cluster_count: 1,
          eligible_cluster_count: 1,
          schema_name: "duckdock-semantic-clustering-run",
          schema_version: "1.0",
          created_by_user_id: 1,
          created_at: "2026-08-04T00:00:00Z",
          items: [],
        },
      ],
    });
    mocks.listSemanticRegressionPolicies.mockResolvedValue({
      data: [
        {
          public_id: "esrp_monitor",
          namespace_id: 7,
          name: "monitor-gate",
          description: null,
          status: "ACTIVE",
          versions: [
            {
              public_id: "esrv_monitor",
              version: 1,
              config_digest: "5".repeat(64),
              minimum_pairwise_assignment_agreement: 0.9,
              maximum_cluster_count_change_ratio: 0.5,
              maximum_mean_centroid_similarity_drop: 0.05,
              maximum_eligible_cluster_ratio_drop: 0.25,
              require_exact_source_content: true,
              schema_name: "duckdock-semantic-clustering-regression-policy",
              schema_version: "1.0",
              created_by_user_id: 1,
              created_at: "2026-08-04T00:00:00Z",
            },
          ],
          created_at: "2026-08-04T00:00:00Z",
          updated_at: "2026-08-04T00:00:00Z",
        },
      ],
    });
    mocks.listSemanticMonitors.mockResolvedValue({
      data: [
        {
          public_id: "esmp_existing",
          namespace_id: 7,
          name: "existing-watch",
          description: null,
          baseline_run_public_id: "escr_monitor_baseline",
          candidate_policy_version_public_id: "escv_monitor",
          regression_policy_version_public_id: "esrv_monitor",
          interval_seconds: 3600,
          config_digest: "6".repeat(64),
          status: "ACTIVE",
          next_run_at: "2026-08-04T01:00:00Z",
          created_by_user_id: 1,
          created_at: "2026-08-04T00:00:00Z",
          updated_at: "2026-08-04T00:00:00Z",
        },
      ],
    });
    mocks.listSemanticMonitorAlerts.mockResolvedValue({
      data: [
        {
          public_id: "esma_open",
          namespace_id: 7,
          monitor_public_id: "esmp_existing",
          monitor_run_public_id: "esmr_drift",
          comparison_public_id: "esrc_drift",
          severity: "CRITICAL",
          status: "OPEN",
          reason_codes: ["candidate_not_clustered"],
          error_code: null,
          acknowledged_by_user_id: null,
          acknowledged_at: null,
          acknowledgement_note: null,
          created_at: "2026-08-04T00:30:00Z",
          updated_at: "2026-08-04T00:30:00Z",
        },
      ],
    });

    const user = userEvent.setup();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <EvalHubPage />
      </QueryClientProvider>,
    );

    await user.click(await screen.findByRole("button", { name: "Trace2Dataset" }));
    const heading = await screen.findByText("定时语义漂移监控");
    const panel = heading.closest("section");
    expect(panel).not.toBeNull();
    await user.click(within(panel as HTMLElement).getByRole("button", { name: "创建" }));
    await waitFor(() => {
      expect(mocks.createSemanticMonitor).toHaveBeenCalledWith(
        expect.objectContaining({
          namespace_id: 7,
          baseline_run_public_id: "escr_monitor_baseline",
          candidate_policy_version_public_id: "escv_monitor",
          regression_policy_version_public_id: "esrv_monitor",
          interval_seconds: 3600,
        }),
      );
    });

    await user.click(within(panel as HTMLElement).getByRole("button", { name: "立即运行" }));
    await waitFor(() => {
      expect(mocks.runSemanticMonitorNow).toHaveBeenCalledWith(
        "esmp_existing",
        expect.stringMatching(/^semantic-monitor-now-/),
      );
    });

    await user.type(
      within(panel as HTMLElement).getByLabelText("告警确认备注 esma_open"),
      "checked locally",
    );
    await user.click(within(panel as HTMLElement).getByRole("button", { name: "确认" }));
    await waitFor(() => {
      expect(mocks.acknowledgeSemanticMonitorAlert).toHaveBeenCalledWith(
        "esma_open",
        "checked locally",
      );
    });
  });
});
