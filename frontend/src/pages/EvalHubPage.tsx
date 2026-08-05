import {
  CheckCircle2,
  Clock3,
  Database,
  FlaskConical,
  GitCompareArrows,
  GitFork,
  ListChecks,
  RefreshCw,
  Search,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  evalHubApi,
  namespacesApi,
  type EvaluationAnnotationDispatch,
  type EvaluationAnnotationQueueBinding,
  type EvaluationCaseRoutingPolicy,
  type EvaluationCaseRoutingPolicyVersion,
  type EvaluationCaseRoutingRun,
  type EvaluationPromotionDiversityDimension,
  type EvaluationPromotionPolicy,
  type EvaluationPromotionPolicyVersion,
  type EvaluationPromotionRun,
  type EvaluationPromotionScoreDataType,
  type EvaluationComparison,
  type EvaluationComparisonOutcome,
  type EvaluationDataset,
  type EvaluationDatasetCurationBatch,
  type EvaluationDatasetMaterialization,
  type EvaluationExperienceAsset,
  type EvaluationExperienceAssetVersion,
  type EvaluationExperienceCandidate,
  type EvaluationExperienceExtractionRun,
  type EvaluationFailureTaxonomyPolicy,
  type EvaluationFailureTaxonomyPolicyVersion,
  type EvaluationSamplingPolicy,
  type EvaluationSamplingPolicyVersion,
  type EvaluationSamplingRun,
  type EvaluationSemanticClusteringPolicy,
  type EvaluationSemanticClusteringPolicyVersion,
  type EvaluationSemanticClusteringRun,
  type EvaluationSemanticMonitor,
  type EvaluationSemanticMonitorAlert,
  type EvaluationSemanticMonitorRun,
  type EvaluationSemanticRegressionComparison,
  type EvaluationSemanticRegressionPolicy,
  type EvaluationSemanticRegressionPolicyVersion,
  type EvaluationProviderAnnotationQueue,
  type TraceDatasetCandidate,
  type ReleaseCandidateEvaluationBinding,
  type ReleaseCandidateGateDecision,
  type ReleaseCandidateReviewDecision,
} from "../api/client";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  MetricCard,
  MonoPill,
  PageHeader,
  type Tone,
} from "../components/ui";
import { useI18n } from "../i18n";

type Tab = "overview" | "data-flywheel" | "comparisons" | "release";

interface ReviewDraft {
  binding: ReleaseCandidateEvaluationBinding;
  decision: ReleaseCandidateReviewDecision;
  comment: string;
}

const comparisonTone: Record<EvaluationComparisonOutcome, Tone> = {
  PASS: "emerald",
  REGRESSION: "rose",
  INCONCLUSIVE: "amber",
};

function formatPercent(value: number | null): string {
  return value === null ? "—" : `${(value * 100).toFixed(1)}%`;
}

function formatDelta(value: number | null): string {
  if (value === null) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${(value * 100).toFixed(1)} pp`;
}

function formatTime(value: string, locale: string): string {
  return new Intl.DateTimeFormat(locale === "zh" ? "zh-CN" : "en-US", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(new Date(value));
}

function shortDigest(value: string): string {
  return `${value.slice(0, 10)}…${value.slice(-8)}`;
}

function stableClientKey(prefix: string, value: string): string {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return `${prefix}-${(hash >>> 0).toString(16).padStart(8, "0")}`;
}

export default function EvalHubPage() {
  const { locale } = useI18n();
  const zh = locale === "zh";
  const [namespaceId, setNamespaceId] = useState<number | null>(null);
  const [tab, setTab] = useState<Tab>("overview");
  const [reviewDraft, setReviewDraft] = useState<ReviewDraft | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [notice, setNotice] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const [gateLoading, setGateLoading] = useState<string | null>(null);
  const [gateDecisions, setGateDecisions] = useState<Record<string, ReleaseCandidateGateDecision>>({});
  const [materializationDatasetId, setMaterializationDatasetId] = useState("");
  const [traceId, setTraceId] = useState("");
  const [observationId, setObservationId] = useState("");
  const [materializing, setMaterializing] = useState(false);
  const [candidateName, setCandidateName] = useState("");
  const [candidateEnvironment, setCandidateEnvironment] = useState("");
  const [candidateType, setCandidateType] = useState("");
  const [candidateWindowHours, setCandidateWindowHours] = useState("24");
  const [candidateRootOnly, setCandidateRootOnly] = useState(true);
  const [candidates, setCandidates] = useState<TraceDatasetCandidate[]>([]);
  const [selectedCandidateRefs, setSelectedCandidateRefs] = useState<Set<string>>(new Set());
  const [candidateLoading, setCandidateLoading] = useState(false);
  const [curationSubmitting, setCurationSubmitting] = useState(false);
  const [curationActionId, setCurationActionId] = useState<string | null>(null);
  const [curationComments, setCurationComments] = useState<Record<string, string>>({});
  const [samplingPolicyName, setSamplingPolicyName] = useState("production-stable-sample");
  const [samplingSampleSize, setSamplingSampleSize] = useState("10");
  const [samplingMinimumSize, setSamplingMinimumSize] = useState("1");
  const [samplingSaving, setSamplingSaving] = useState(false);
  const [samplingActionId, setSamplingActionId] = useState<string | null>(null);
  const [annotationProviderQueueRef, setAnnotationProviderQueueRef] = useState("");
  const [annotationBindingId, setAnnotationBindingId] = useState("");
  const [annotationCurationBatchId, setAnnotationCurationBatchId] = useState("");
  const [annotationSaving, setAnnotationSaving] = useState(false);
  const [annotationActionId, setAnnotationActionId] = useState<string | null>(null);
  const [promotionPolicyName, setPromotionPolicyName] = useState("quality-diversity-promotion");
  const [promotionScoreConfigId, setPromotionScoreConfigId] = useState("");
  const [promotionScoreDataType, setPromotionScoreDataType] =
    useState<EvaluationPromotionScoreDataType>("NUMERIC");
  const [promotionMinimumScore, setPromotionMinimumScore] = useState("0.8");
  const [promotionAcceptedValues, setPromotionAcceptedValues] = useState("");
  const [promotionDiversityDimension, setPromotionDiversityDimension] =
    useState<EvaluationPromotionDiversityDimension>("NONE");
  const [promotionMinimumBuckets, setPromotionMinimumBuckets] = useState("1");
  const [promotionDispatchId, setPromotionDispatchId] = useState("");
  const [promotionSaving, setPromotionSaving] = useState(false);
  const [promotionActionId, setPromotionActionId] = useState<string | null>(null);
  const [caseRoutingPolicyName, setCaseRoutingPolicyName] =
    useState("golden-bad-case-routing");
  const [caseRoutingSourcePromotionVersionId, setCaseRoutingSourcePromotionVersionId] =
    useState("");
  const [caseRoutingGoldenTargetSize, setCaseRoutingGoldenTargetSize] = useState("20");
  const [caseRoutingGoldenMinimumSize, setCaseRoutingGoldenMinimumSize] = useState("1");
  const [caseRoutingBadCaseTargetSize, setCaseRoutingBadCaseTargetSize] = useState("20");
  const [caseRoutingBadCaseMinimumSize, setCaseRoutingBadCaseMinimumSize] = useState("1");
  const [caseRoutingPromotionRunIds, setCaseRoutingPromotionRunIds] = useState<string[]>([]);
  const [caseRoutingGoldenDatasetId, setCaseRoutingGoldenDatasetId] = useState("");
  const [caseRoutingBadCaseDatasetId, setCaseRoutingBadCaseDatasetId] = useState("");
  const [caseRoutingSaving, setCaseRoutingSaving] = useState(false);
  const [caseRoutingActionId, setCaseRoutingActionId] = useState<string | null>(null);
  const [failureTaxonomyPolicyName, setFailureTaxonomyPolicyName] =
    useState("failure-taxonomy");
  const [failureTaxonomySourceRoutingVersionId, setFailureTaxonomySourceRoutingVersionId] =
    useState("");
  const [failureTaxonomySemanticVersionId, setFailureTaxonomySemanticVersionId] =
    useState("");
  const [failureTaxonomyMinOccurrences, setFailureTaxonomyMinOccurrences] = useState("2");
  const [failureTaxonomyMinSourceRuns, setFailureTaxonomyMinSourceRuns] = useState("2");
  const [failureTaxonomyIncludeIsolated, setFailureTaxonomyIncludeIsolated] = useState(false);
  const [failureTaxonomyMaxCandidates, setFailureTaxonomyMaxCandidates] = useState("20");
  const [experienceSourceRoutingRunId, setExperienceSourceRoutingRunId] = useState("");
  const [experienceSemanticRunId, setExperienceSemanticRunId] = useState("");
  const [failureTaxonomySaving, setFailureTaxonomySaving] = useState(false);
  const [failureTaxonomyActionId, setFailureTaxonomyActionId] = useState<string | null>(null);
  const [experienceReviewComments, setExperienceReviewComments] =
    useState<Record<string, string>>({});
  const [experienceAssetCandidateId, setExperienceAssetCandidateId] = useState("");
  const [experienceAssetName, setExperienceAssetName] = useState("");
  const [experienceAssetDescription, setExperienceAssetDescription] = useState("");
  const [experienceAssetId, setExperienceAssetId] = useState("");
  const [experienceVersionBody, setExperienceVersionBody] = useState("");
  const [experienceVersionApplicability, setExperienceVersionApplicability] = useState("");
  const [experienceVersionChangeSummary, setExperienceVersionChangeSummary] = useState("");
  const [experienceActivationNote, setExperienceActivationNote] = useState("");
  const [experienceActivationComments, setExperienceActivationComments] =
    useState<Record<string, string>>({});
  const [experienceAssetActionId, setExperienceAssetActionId] = useState<string | null>(null);

  const namespaces = useQuery({
    queryKey: ["eval-hub", "namespaces"],
    queryFn: async () => (await namespacesApi.list()).data,
  });

  useEffect(() => {
    if (namespaceId === null && namespaces.data?.length) {
      setNamespaceId(namespaces.data[0].id);
    }
  }, [namespaceId, namespaces.data]);

  const hub = useQuery({
    queryKey: ["eval-hub", namespaceId],
    enabled: namespaceId !== null,
    queryFn: async () => {
      const id = namespaceId as number;
      const [datasets, materializations, curationBatches, samplingPolicies, samplingRuns, annotationProviderQueues, annotationQueueBindings, annotationDispatches, promotionPolicies, promotionRuns, caseRoutingPolicies, caseRoutingRuns, semanticClusteringPolicies, semanticClusteringRuns, semanticRegressionPolicies, semanticRegressionComparisons, semanticMonitors, semanticMonitorRuns, semanticMonitorAlerts, failureTaxonomyPolicies, experienceExtractionRuns, experienceAssets, evaluators, evaluations, comparisons, bindings] = await Promise.all([
        evalHubApi.listDatasets(id),
        evalHubApi.listDatasetMaterializations(id),
        evalHubApi.listCurationBatches(id),
        evalHubApi.listSamplingPolicies(id),
        evalHubApi.listSamplingRuns(id),
        evalHubApi.listAnnotationProviderQueues(id).catch(() => ({
          data: [] as EvaluationProviderAnnotationQueue[],
        })),
        evalHubApi.listAnnotationQueueBindings(id),
        evalHubApi.listAnnotationDispatches(id),
        evalHubApi.listPromotionPolicies(id),
        evalHubApi.listPromotionRuns(id),
        evalHubApi.listCaseRoutingPolicies(id),
        evalHubApi.listCaseRoutingRuns(id),
        evalHubApi.listSemanticClusteringPolicies(id),
        evalHubApi.listSemanticClusteringRuns(id),
        evalHubApi.listSemanticRegressionPolicies(id),
        evalHubApi.listSemanticRegressionComparisons(id),
        evalHubApi.listSemanticMonitors(id),
        evalHubApi.listSemanticMonitorRuns(id),
        evalHubApi.listSemanticMonitorAlerts(id),
        evalHubApi.listFailureTaxonomyPolicies(id),
        evalHubApi.listExperienceExtractionRuns(id),
        evalHubApi.listExperienceAssets(id),
        evalHubApi.listEvaluators(id),
        evalHubApi.listEvaluations(id),
        evalHubApi.listComparisons(id),
        evalHubApi.listBindings(id),
      ]);
      return {
        datasets: datasets.data,
        materializations: materializations.data,
        curationBatches: curationBatches.data,
        samplingPolicies: samplingPolicies.data,
        samplingRuns: samplingRuns.data,
        annotationProviderQueues: annotationProviderQueues.data,
        annotationQueueBindings: annotationQueueBindings.data,
        annotationDispatches: annotationDispatches.data,
        promotionPolicies: promotionPolicies.data,
        promotionRuns: promotionRuns.data,
        caseRoutingPolicies: caseRoutingPolicies.data,
        caseRoutingRuns: caseRoutingRuns.data,
        semanticClusteringPolicies: semanticClusteringPolicies.data,
        semanticClusteringRuns: semanticClusteringRuns.data,
        semanticRegressionPolicies: semanticRegressionPolicies.data,
        semanticRegressionComparisons: semanticRegressionComparisons.data,
        semanticMonitors: semanticMonitors.data,
        semanticMonitorRuns: semanticMonitorRuns.data,
        semanticMonitorAlerts: semanticMonitorAlerts.data,
        failureTaxonomyPolicies: failureTaxonomyPolicies.data,
        experienceExtractionRuns: experienceExtractionRuns.data,
        experienceAssets: experienceAssets.data,
        evaluators: evaluators.data,
        evaluations: evaluations.data,
        comparisons: comparisons.data,
        bindings: bindings.data,
      };
    },
  });

  useEffect(() => {
    const datasets = hub.data?.datasets ?? [];
    if (
      datasets.length &&
      !datasets.some((dataset) => dataset.public_id === materializationDatasetId)
    ) {
      setMaterializationDatasetId(datasets[0].public_id);
    }
  }, [hub.data?.datasets, materializationDatasetId]);

  useEffect(() => {
    const claimed = new Set(
      (hub.data?.experienceAssets ?? []).map(
        (asset) => asset.source_candidate_public_id,
      ),
    );
    const approved = (hub.data?.experienceExtractionRuns ?? [])
      .flatMap((run) => run.candidates)
      .filter(
        (candidate) =>
          candidate.status === "APPROVED" && !claimed.has(candidate.public_id),
      );
    if (
      approved.length &&
      !approved.some(
        (candidate) => candidate.public_id === experienceAssetCandidateId,
      )
    ) {
      setExperienceAssetCandidateId(approved[0].public_id);
    }
  }, [
    experienceAssetCandidateId,
    hub.data?.experienceAssets,
    hub.data?.experienceExtractionRuns,
  ]);

  useEffect(() => {
    const assets = hub.data?.experienceAssets ?? [];
    if (
      assets.length &&
      !assets.some((asset) => asset.public_id === experienceAssetId)
    ) {
      setExperienceAssetId(assets[0].public_id);
    }
  }, [experienceAssetId, hub.data?.experienceAssets]);

  useEffect(() => {
    const queues = hub.data?.annotationProviderQueues ?? [];
    if (
      queues.length &&
      !queues.some((queue) => queue.provider_queue_ref === annotationProviderQueueRef)
    ) {
      setAnnotationProviderQueueRef(queues[0].provider_queue_ref);
    }
  }, [annotationProviderQueueRef, hub.data?.annotationProviderQueues]);

  useEffect(() => {
    const bindings = hub.data?.annotationQueueBindings ?? [];
    if (
      bindings.length &&
      !bindings.some((binding) => binding.public_id === annotationBindingId)
    ) {
      setAnnotationBindingId(bindings[0].public_id);
    }
  }, [annotationBindingId, hub.data?.annotationQueueBindings]);

  useEffect(() => {
    const binding = (hub.data?.annotationQueueBindings ?? []).find(
      (value) => value.public_id === annotationBindingId,
    );
    if (
      binding?.score_config_ids.length &&
      !binding.score_config_ids.includes(promotionScoreConfigId)
    ) {
      setPromotionScoreConfigId(binding.score_config_ids[0]);
    }
  }, [annotationBindingId, hub.data?.annotationQueueBindings, promotionScoreConfigId]);

  useEffect(() => {
    const dispatches = (hub.data?.annotationDispatches ?? []).filter(
      (dispatch) =>
        dispatch.binding_public_id === annotationBindingId &&
        dispatch.status === "SYNCED" &&
        dispatch.completed_count === dispatch.item_count,
    );
    if (
      dispatches.length &&
      !dispatches.some((dispatch) => dispatch.public_id === promotionDispatchId)
    ) {
      setPromotionDispatchId(dispatches[0].public_id);
    }
  }, [annotationBindingId, hub.data?.annotationDispatches, promotionDispatchId]);

  useEffect(() => {
    const versions = (hub.data?.promotionPolicies ?? [])
      .flatMap((policy) => policy.versions)
      .filter((version) => version.diversity_dimension !== "NONE");
    if (
      versions.length &&
      !versions.some(
        (version) => version.public_id === caseRoutingSourcePromotionVersionId,
      )
    ) {
      setCaseRoutingSourcePromotionVersionId(versions[0].public_id);
    } else if (!versions.length && caseRoutingSourcePromotionVersionId) {
      setCaseRoutingSourcePromotionVersionId("");
    }
  }, [
    caseRoutingSourcePromotionVersionId,
    hub.data?.promotionPolicies,
  ]);

  useEffect(() => {
    setCaseRoutingPromotionRunIds((current) =>
      current.filter((publicId) =>
        (hub.data?.promotionRuns ?? []).some(
          (run) =>
            run.public_id === publicId &&
            run.policy_version_public_id === caseRoutingSourcePromotionVersionId,
        ),
      ),
    );
  }, [caseRoutingSourcePromotionVersionId, hub.data?.promotionRuns]);

  useEffect(() => {
    const versions = (hub.data?.caseRoutingPolicies ?? []).flatMap(
      (policy) => policy.versions,
    );
    if (
      versions.length &&
      !versions.some(
        (version) =>
          version.public_id === failureTaxonomySourceRoutingVersionId,
      )
    ) {
      setFailureTaxonomySourceRoutingVersionId(versions[0].public_id);
    } else if (!versions.length && failureTaxonomySourceRoutingVersionId) {
      setFailureTaxonomySourceRoutingVersionId("");
    }
  }, [
    failureTaxonomySourceRoutingVersionId,
    hub.data?.caseRoutingPolicies,
  ]);

  useEffect(() => {
    const runs = (hub.data?.caseRoutingRuns ?? []).filter(
      (run) =>
        run.outcome === "ROUTED" &&
        run.policy_version_public_id === failureTaxonomySourceRoutingVersionId,
    );
    if (
      runs.length &&
      !runs.some((run) => run.public_id === experienceSourceRoutingRunId)
    ) {
      setExperienceSourceRoutingRunId(runs[0].public_id);
    } else if (!runs.length && experienceSourceRoutingRunId) {
      setExperienceSourceRoutingRunId("");
    }
  }, [
    experienceSourceRoutingRunId,
    failureTaxonomySourceRoutingVersionId,
    hub.data?.caseRoutingRuns,
  ]);

  useEffect(() => {
    const versions = (hub.data?.semanticClusteringPolicies ?? [])
      .flatMap((policy) => policy.versions)
      .filter(
        (version) =>
          version.source_case_routing_policy_version_public_id ===
          failureTaxonomySourceRoutingVersionId,
      );
    if (
      failureTaxonomySemanticVersionId &&
      !versions.some(
        (version) => version.public_id === failureTaxonomySemanticVersionId,
      )
    ) {
      setFailureTaxonomySemanticVersionId("");
    }
  }, [
    failureTaxonomySemanticVersionId,
    failureTaxonomySourceRoutingVersionId,
    hub.data?.semanticClusteringPolicies,
  ]);

  useEffect(() => {
    if (!failureTaxonomySemanticVersionId) {
      setExperienceSemanticRunId("");
      return;
    }
    const runs = (hub.data?.semanticClusteringRuns ?? []).filter(
      (run) =>
        run.policy_version_public_id === failureTaxonomySemanticVersionId &&
        run.source_case_routing_run_public_id === experienceSourceRoutingRunId,
    );
    if (
      runs.length &&
      !runs.some((run) => run.public_id === experienceSemanticRunId)
    ) {
      setExperienceSemanticRunId(runs[0].public_id);
    } else if (!runs.length && experienceSemanticRunId) {
      setExperienceSemanticRunId("");
    }
  }, [
    experienceSemanticRunId,
    experienceSourceRoutingRunId,
    failureTaxonomySemanticVersionId,
    hub.data?.semanticClusteringRuns,
  ]);

  useEffect(() => {
    const datasets = (hub.data?.datasets ?? []).filter(
      (dataset) =>
        dataset.provider === "LANGFUSE" &&
        dataset.status === "ACTIVE" &&
        Boolean(dataset.provider_dataset_ref),
    );
    if (
      datasets.length &&
      !datasets.some((dataset) => dataset.public_id === caseRoutingGoldenDatasetId)
    ) {
      setCaseRoutingGoldenDatasetId(datasets[0].public_id);
    }
    const alternative = datasets.find(
      (dataset) => dataset.public_id !== caseRoutingGoldenDatasetId,
    );
    if (
      alternative &&
      (!datasets.some((dataset) => dataset.public_id === caseRoutingBadCaseDatasetId) ||
        caseRoutingBadCaseDatasetId === caseRoutingGoldenDatasetId)
    ) {
      setCaseRoutingBadCaseDatasetId(alternative.public_id);
    } else if (!alternative && caseRoutingBadCaseDatasetId) {
      setCaseRoutingBadCaseDatasetId("");
    }
  }, [
    caseRoutingBadCaseDatasetId,
    caseRoutingGoldenDatasetId,
    hub.data?.datasets,
  ]);

  useEffect(() => {
    const batches = hub.data?.curationBatches ?? [];
    if (
      batches.length &&
      !batches.some((batch) => batch.public_id === annotationCurationBatchId)
    ) {
      setAnnotationCurationBatchId(batches[0].public_id);
    }
  }, [annotationCurationBatchId, hub.data?.curationBatches]);

  const summary = useMemo(() => {
    const data = hub.data;
    return {
      datasetCount: data?.datasets.length ?? 0,
      evaluatorCount: data?.evaluators.length ?? 0,
      completedEvaluationCount:
        data?.evaluations.filter((item) => item.status === "COMPLETED").length ?? 0,
      evaluationCount: data?.evaluations.length ?? 0,
      passedComparisonCount:
        data?.comparisons.filter((item) => item.outcome === "PASS").length ?? 0,
      reviewedBindingCount: data?.bindings.filter((item) => item.review !== null).length ?? 0,
    };
  }, [hub.data]);

  function openReview(
    binding: ReleaseCandidateEvaluationBinding,
    decision: ReleaseCandidateReviewDecision,
  ) {
    setNotice(null);
    setReviewDraft({ binding, decision, comment: "" });
  }

  async function submitReview() {
    if (!reviewDraft || namespaceId === null) return;
    if (reviewDraft.decision === "REJECTED" && reviewDraft.comment.trim().length < 5) {
      setNotice({
        tone: "error",
        text: zh ? "拒绝原因至少需要 5 个字符。" : "A rejection reason needs at least 5 characters.",
      });
      return;
    }
    setReviewing(true);
    setNotice(null);
    try {
      await evalHubApi.reviewBinding(
        {
          namespace_id: namespaceId,
          binding_public_id: reviewDraft.binding.public_id,
          decision: reviewDraft.decision,
          comment: reviewDraft.comment.trim() || null,
        },
        `eval-review-${reviewDraft.binding.public_id}-${reviewDraft.decision.toLowerCase()}`,
      );
      setNotice({
        tone: "success",
        text:
          reviewDraft.decision === "APPROVED"
            ? zh
              ? "评审已批准，并作为不可变证据写入。"
              : "Review approved and recorded as immutable evidence."
            : zh
              ? "评审已拒绝，候选发布门禁将被阻断。"
              : "Review rejected; the candidate gate will be blocked.",
      });
      setReviewDraft(null);
      await hub.refetch();
    } catch {
      setNotice({
        tone: "error",
        text: zh
          ? "评审写入失败。该绑定可能已存在最终决定，请刷新确认。"
          : "Review failed. This binding may already have a final decision.",
      });
    } finally {
      setReviewing(false);
    }
  }

  async function evaluateGate(binding: ReleaseCandidateEvaluationBinding) {
    setGateLoading(binding.public_id);
    setNotice(null);
    try {
      const response = await evalHubApi.evaluateGate({
        namespace_id: binding.namespace_id,
        release_candidate_ref: binding.release_candidate_ref,
        deployment_public_id: binding.deployment_public_id,
        deployment_revision: binding.deployment_revision,
      });
      setGateDecisions((current) => ({
        ...current,
        [binding.public_id]: response.data,
      }));
    } catch {
      setNotice({
        tone: "error",
        text: zh ? "门禁复算失败，请检查写权限与证据状态。" : "Gate evaluation failed.",
      });
    } finally {
      setGateLoading(null);
    }
  }

  async function materializeTrace() {
    if (!materializationDatasetId || namespaceId === null) return;
    const normalizedTraceId = traceId.trim().toLowerCase();
    const normalizedObservationId = observationId.trim().toLowerCase();
    if (!/^[0-9a-f]{32}$/.test(normalizedTraceId)) {
      setNotice({
        tone: "error",
        text: zh ? "Trace ID 必须是 32 位十六进制。" : "Trace ID must be 32 hexadecimal characters.",
      });
      return;
    }
    if (normalizedObservationId && !/^[0-9a-f]{16}$/.test(normalizedObservationId)) {
      setNotice({
        tone: "error",
        text: zh
          ? "Observation ID 必须是 16 位十六进制；留空则选择唯一根 Observation。"
          : "Observation ID must be 16 hexadecimal characters, or blank for the unique root.",
      });
      return;
    }
    setMaterializing(true);
    setNotice(null);
    try {
      await evalHubApi.materializeTrace(
        materializationDatasetId,
        {
          trace_id: normalizedTraceId,
          observation_id: normalizedObservationId || null,
        },
        `trace2dataset-${materializationDatasetId}-${normalizedTraceId}-${normalizedObservationId || "root"}`,
      );
      setNotice({
        tone: "success",
        text: zh
          ? "Trace 已写入 Langfuse Dataset，并生成不可变 DuckDock Dataset Version。"
          : "Trace was added to the Langfuse Dataset and pinned as an immutable DuckDock Dataset version.",
      });
      setTraceId("");
      setObservationId("");
      await hub.refetch();
    } catch {
      setNotice({
        tone: "error",
        text: zh
          ? "轨迹入集失败。请确认 Trace/Observation 存在、包含 input/output，且尚未写入此 Dataset。"
          : "Trace materialization failed. Verify the source, input/output, and Dataset history.",
      });
    } finally {
      setMaterializing(false);
    }
  }

  function candidateRef(candidate: TraceDatasetCandidate): string {
    return `${candidate.source_trace_ref}:${candidate.source_observation_ref}`;
  }

  function toggleCandidate(candidate: TraceDatasetCandidate) {
    const ref = candidateRef(candidate);
    setSelectedCandidateRefs((current) => {
      const next = new Set(current);
      if (next.has(ref)) {
        next.delete(ref);
      } else if (next.size < 20 && !candidate.already_governed) {
        next.add(ref);
      }
      return next;
    });
  }

  function markBatchCandidatesMaterialized(batch: EvaluationDatasetCurationBatch) {
    const sourceRefs = new Set(
      batch.items.map(
        (item) => `${item.source_trace_ref}:${item.source_observation_ref}`,
      ),
    );
    setCandidates((current) =>
      current.map((candidate) =>
        sourceRefs.has(candidateRef(candidate))
          ? { ...candidate, already_materialized: true, already_governed: true }
          : candidate,
      ),
    );
    setSelectedCandidateRefs((current) => {
      const next = new Set(current);
      sourceRefs.forEach((ref) => next.delete(ref));
      return next;
    });
  }

  async function searchTraceCandidates() {
    if (!materializationDatasetId) return;
    const hours = Number(candidateWindowHours);
    const to = new Date();
    const from = new Date(to.getTime() - hours * 60 * 60 * 1000);
    setCandidateLoading(true);
    setNotice(null);
    try {
      const response = await evalHubApi.listTraceCandidates(materializationDatasetId, {
        from_start_time: from.toISOString(),
        to_start_time: to.toISOString(),
        name: candidateName.trim() || undefined,
        observation_type: candidateType || undefined,
        environment: candidateEnvironment.trim() || undefined,
        root_only: candidateRootOnly,
        limit: 100,
      });
      setCandidates(response.data);
      setSelectedCandidateRefs(new Set());
      if (!response.data.length) {
        setNotice({
          tone: "error",
          text: zh
            ? "当前筛选条件没有返回 Langfuse Observation 元数据。"
            : "No Langfuse Observation metadata matched these filters.",
        });
      }
    } catch {
      setNotice({
        tone: "error",
        text: zh
          ? "候选检索失败，请检查 Langfuse 可用性和时间窗口。"
          : "Candidate search failed. Check Langfuse and the time window.",
      });
    } finally {
      setCandidateLoading(false);
    }
  }

  async function submitCurationBatch() {
    if (!materializationDatasetId) return;
    const selected = candidates
      .filter((candidate) => selectedCandidateRefs.has(candidateRef(candidate)))
      .sort((left, right) => candidateRef(left).localeCompare(candidateRef(right)));
    if (!selected.length) {
      setNotice({
        tone: "error",
        text: zh ? "请至少选择一个候选 Observation。" : "Select at least one Observation.",
      });
      return;
    }
    const selectionIdentity = selected.map(candidateRef).join("|");
    setCurationSubmitting(true);
    setNotice(null);
    try {
      await evalHubApi.createCurationBatch(
        materializationDatasetId,
        {
          items: selected.map((candidate) => ({
            trace_id: candidate.source_trace_ref,
            observation_id: candidate.source_observation_ref,
          })),
        },
        stableClientKey(
          `curation-submit-${materializationDatasetId}`,
          selectionIdentity,
        ),
      );
      setSelectedCandidateRefs(new Set());
      setNotice({
        tone: "success",
        text: zh
          ? "策展批次已提交审核；选中引用已冻结为不可变清单。"
          : "Curation batch submitted with an immutable source list.",
      });
      await hub.refetch();
    } catch {
      setNotice({
        tone: "error",
        text: zh
          ? "策展批次提交失败；候选可能已被此 Dataset 的其他批次治理。"
          : "Batch submission failed; a source may already be governed.",
      });
    } finally {
      setCurationSubmitting(false);
    }
  }

  function samplingVersionPayload() {
    const sampleSize = Number(samplingSampleSize);
    const minimumSize = Number(samplingMinimumSize);
    if (
      !Number.isInteger(sampleSize) ||
      !Number.isInteger(minimumSize) ||
      sampleSize < 1 ||
      sampleSize > 20 ||
      minimumSize < 1 ||
      minimumSize > sampleSize
    ) {
      setNotice({
        tone: "error",
        text: zh
          ? "目标样本数需为 1–20，最低样本数不能超过目标值。"
          : "Target size must be 1–20 and the minimum cannot exceed it.",
      });
      return null;
    }
    return {
      strategy: "STABLE_HASH" as const,
      sample_size: sampleSize,
      minimum_sample_size: minimumSize,
      candidate_limit: 100,
      observation_name: candidateName.trim() || undefined,
      observation_type: candidateType || undefined,
      environment: candidateEnvironment.trim() || undefined,
      root_only: candidateRootOnly,
    };
  }

  async function createSamplingPolicy() {
    if (namespaceId === null) return;
    const name = samplingPolicyName.trim();
    const payload = samplingVersionPayload();
    if (!name || !payload) {
      if (!name) {
        setNotice({
          tone: "error",
          text: zh ? "请输入采样策略名称。" : "Enter a sampling policy name.",
        });
      }
      return;
    }
    setSamplingSaving(true);
    setNotice(null);
    let policyCreated = false;
    try {
      const policy = await evalHubApi.createSamplingPolicy({
        namespace_id: namespaceId,
        name,
        description: "Reproducible metadata-only production sampling",
      });
      policyCreated = true;
      await evalHubApi.createSamplingPolicyVersion(policy.data.public_id, payload);
      setNotice({
        tone: "success",
        text: zh
          ? "采样策略及首个不可变版本已创建。"
          : "Sampling policy and its first immutable version were created.",
      });
      await hub.refetch();
    } catch {
      setNotice({
        tone: "error",
        text: policyCreated
          ? zh
            ? "策略已创建，但版本创建失败；可在下方保存当前筛选为新版本。"
            : "Policy was created but version creation failed; add a version below."
          : zh
            ? "采样策略创建失败；名称可能已存在。"
            : "Sampling policy creation failed; the name may already exist.",
      });
      await hub.refetch();
    } finally {
      setSamplingSaving(false);
    }
  }

  async function createSamplingVersion(policy: EvaluationSamplingPolicy) {
    const payload = samplingVersionPayload();
    if (!payload) return;
    setSamplingActionId(policy.public_id);
    setNotice(null);
    try {
      await evalHubApi.createSamplingPolicyVersion(policy.public_id, payload);
      setNotice({
        tone: "success",
        text: zh
          ? "当前筛选已冻结为新的不可变采样策略版本。"
          : "Current filters were frozen as a new immutable policy version.",
      });
      await hub.refetch();
    } catch {
      setNotice({
        tone: "error",
        text: zh
          ? "版本创建失败；相同配置摘要可能已经存在。"
          : "Version creation failed; this configuration may already exist.",
      });
    } finally {
      setSamplingActionId(null);
    }
  }

  async function runSamplingVersion(version: EvaluationSamplingPolicyVersion) {
    if (!materializationDatasetId) return;
    const hours = Number(candidateWindowHours);
    const to = new Date();
    const from = new Date(to.getTime() - hours * 60 * 60 * 1000);
    const requestIdentity = [
      version.public_id,
      materializationDatasetId,
      from.toISOString(),
      to.toISOString(),
    ].join("|");
    setSamplingActionId(version.public_id);
    setNotice(null);
    try {
      const response = await evalHubApi.runSamplingPolicyVersion(
        version.public_id,
        {
          dataset_public_id: materializationDatasetId,
          from_start_time: from.toISOString(),
          to_start_time: to.toISOString(),
        },
        stableClientKey(`sampling-run-${version.public_id}`, requestIdentity),
      );
      setCandidates([]);
      setSelectedCandidateRefs(new Set());
      setNotice({
        tone: "success",
        text: zh
          ? `采样完成：${response.data.selected_count} 条来源已进入待审批批次。`
          : `Sampling selected ${response.data.selected_count} sources for review.`,
      });
      await hub.refetch();
    } catch {
      setNotice({
        tone: "error",
        text: zh
          ? "采样运行失败；请检查最低样本数、时间窗口和已治理来源。"
          : "Sampling failed; check the minimum, window, and governed sources.",
      });
      await hub.refetch();
    } finally {
      setSamplingActionId(null);
    }
  }

  async function bindAnnotationQueue() {
    if (namespaceId === null || !annotationProviderQueueRef) return;
    setAnnotationSaving(true);
    setNotice(null);
    try {
      const response = await evalHubApi.bindAnnotationQueue({
        namespace_id: namespaceId,
        provider_queue_ref: annotationProviderQueueRef,
      });
      setAnnotationBindingId(response.data.public_id);
      setNotice({
        tone: "success",
        text: zh
          ? "Langfuse 标注队列已绑定；评分配置快照已冻结，升级后变更会 fail-closed。"
          : "Langfuse queue bound with a fail-closed score-config snapshot.",
      });
      await hub.refetch();
    } catch {
      setNotice({
        tone: "error",
        text: zh
          ? "标注队列绑定失败；请确认队列仍存在且至少配置一个 Score Config。"
          : "Queue binding failed; verify the queue and its score config.",
      });
    } finally {
      setAnnotationSaving(false);
    }
  }

  async function dispatchAnnotationBatch() {
    if (!annotationBindingId || !annotationCurationBatchId) return;
    setAnnotationActionId(annotationCurationBatchId);
    setNotice(null);
    try {
      await evalHubApi.dispatchAnnotationBatch(
        annotationBindingId,
        annotationCurationBatchId,
        stableClientKey(
          `annotation-dispatch-${annotationBindingId}`,
          annotationCurationBatchId,
        ),
      );
      setNotice({
        tone: "success",
        text: zh
          ? "标注派发意图已持久化，后台将幂等同步到 Langfuse。"
          : "Dispatch intent persisted; the worker will sync it idempotently.",
      });
      await hub.refetch();
    } catch {
      setNotice({
        tone: "error",
        text: zh
          ? "标注派发失败；同一批次只能绑定一个队列，请刷新查看现有派发。"
          : "Dispatch failed; refresh to inspect any existing dispatch.",
      });
    } finally {
      setAnnotationActionId(null);
    }
  }

  async function retryAnnotationDispatch(dispatch: EvaluationAnnotationDispatch) {
    setAnnotationActionId(dispatch.public_id);
    setNotice(null);
    try {
      await evalHubApi.retryAnnotationDispatch(dispatch.public_id);
      setNotice({
        tone: "success",
        text: zh ? "失败派发已重新排队。" : "Failed dispatch queued for retry.",
      });
      await hub.refetch();
    } catch {
      setNotice({
        tone: "error",
        text: zh ? "重新排队失败，请刷新状态。" : "Retry failed; refresh the state.",
      });
    } finally {
      setAnnotationActionId(null);
    }
  }

  async function reconcileAnnotationDispatch(dispatch: EvaluationAnnotationDispatch) {
    setAnnotationActionId(dispatch.public_id);
    setNotice(null);
    try {
      const response = await evalHubApi.reconcileAnnotationDispatch(dispatch.public_id);
      setNotice({
        tone: "success",
        text: zh
          ? `已对账：${response.data.completed_count}/${response.data.item_count} 个 Langfuse 条目完成；不会自动批准 DuckDock 策展批次。`
          : `Reconciled ${response.data.completed_count}/${response.data.item_count}; curation approval remains separate.`,
      });
      await hub.refetch();
    } catch {
      setNotice({
        tone: "error",
        text: zh ? "Langfuse 状态对账失败。" : "Langfuse reconciliation failed.",
      });
    } finally {
      setAnnotationActionId(null);
    }
  }

  async function createPromotionPolicy() {
    if (namespaceId === null || !promotionPolicyName.trim()) return;
    setPromotionSaving(true);
    setNotice(null);
    try {
      await evalHubApi.createPromotionPolicy({
        namespace_id: namespaceId,
        name: promotionPolicyName.trim(),
        description: "Human-governed Langfuse annotation promotion recommendation",
      });
      setNotice({
        tone: "success",
        text: zh
          ? "Promotion 策略身份已创建；请为它新增不可变规则版本。"
          : "Promotion policy created; add an immutable rule version next.",
      });
      await hub.refetch();
    } catch {
      setNotice({
        tone: "error",
        text: zh
          ? "策略创建失败；名称必须唯一且只能含安全字符。"
          : "Policy creation failed; the safe name must be unique.",
      });
    } finally {
      setPromotionSaving(false);
    }
  }

  async function createPromotionVersion(policy: EvaluationPromotionPolicy) {
    if (!annotationBindingId || !promotionScoreConfigId) return;
    const minimumScore = Number(promotionMinimumScore);
    const minimumBuckets = Number(promotionMinimumBuckets);
    const acceptedTokens = promotionAcceptedValues
      .split(",")
      .map((value) => value.trim())
      .filter(Boolean);
    let acceptedValues: Array<string | boolean> = [];
    if (promotionScoreDataType === "BOOLEAN") {
      if (acceptedTokens.some((value) => !["true", "false"].includes(value.toLowerCase()))) {
        setNotice({
          tone: "error",
          text: zh ? "布尔允许值只能是 true/false。" : "Boolean values must be true/false.",
        });
        return;
      }
      acceptedValues = acceptedTokens.map((value) => value.toLowerCase() === "true");
    } else if (promotionScoreDataType === "CATEGORICAL") {
      acceptedValues = acceptedTokens;
    }
    if (
      (promotionScoreDataType === "NUMERIC" && !Number.isFinite(minimumScore)) ||
      (promotionScoreDataType !== "NUMERIC" && !acceptedValues.length) ||
      !Number.isInteger(minimumBuckets) ||
      minimumBuckets < 1 ||
      minimumBuckets > 20
    ) {
      setNotice({
        tone: "error",
        text: zh ? "请填写有效的质量阈值/允许值与多样性桶数。" : "Enter a valid quality rule and diversity count.",
      });
      return;
    }
    setPromotionActionId(policy.public_id);
    setNotice(null);
    try {
      await evalHubApi.createPromotionPolicyVersion(policy.public_id, {
        binding_public_id: annotationBindingId,
        score_config_id: promotionScoreConfigId,
        score_data_type: promotionScoreDataType,
        minimum_numeric_score:
          promotionScoreDataType === "NUMERIC" ? minimumScore : null,
        accepted_values: acceptedValues,
        diversity_dimension: promotionDiversityDimension,
        min_distinct_buckets:
          promotionDiversityDimension === "NONE" ? 1 : minimumBuckets,
      });
      setNotice({
        tone: "success",
        text: zh
          ? "不可变 Promotion 规则版本已创建；原始评分仍只保留在 Langfuse。"
          : "Immutable promotion rule created; raw scores remain in Langfuse.",
      });
      await hub.refetch();
    } catch {
      setNotice({
        tone: "error",
        text: zh
          ? "规则版本创建失败；请检查队列绑定、Score Config 和重复配置。"
          : "Rule creation failed; check the binding, score config, and duplicate digest.",
      });
    } finally {
      setPromotionActionId(null);
    }
  }

  async function runPromotionVersion(version: EvaluationPromotionPolicyVersion) {
    const dispatch = (hub.data?.annotationDispatches ?? []).find(
      (value) => value.public_id === promotionDispatchId,
    );
    if (!dispatch || dispatch.binding_public_id !== version.binding_public_id) {
      setNotice({
        tone: "error",
        text: zh
          ? "请选择与该规则绑定相同且已全部完成的派发。"
          : "Select a fully completed dispatch from the same binding.",
      });
      return;
    }
    setPromotionActionId(version.public_id);
    setNotice(null);
    try {
      const evidenceRevision = dispatch.last_reconciled_at ?? dispatch.updated_at;
      const response = await evalHubApi.runPromotionPolicyVersion(
        version.public_id,
        dispatch.public_id,
        stableClientKey(
          `promotion-run-${version.public_id}`,
          `${dispatch.public_id}|${evidenceRevision}`,
        ),
      );
      setNotice({
        tone: "success",
        text: zh
          ? `Promotion 评估完成：${response.data.outcome}；仍需人工审批后才能入集。`
          : `Promotion evaluated: ${response.data.outcome}; human approval is still required.`,
      });
      await hub.refetch();
    } catch {
      setNotice({
        tone: "error",
        text: zh
          ? "Promotion 评估失败；要求队列全部完成且评分配置未漂移。"
          : "Promotion failed; the queue must be complete and its score config unchanged.",
      });
      await hub.refetch();
    } finally {
      setPromotionActionId(null);
    }
  }

  async function createCaseRoutingPolicy() {
    if (namespaceId === null || !caseRoutingPolicyName.trim()) return;
    setCaseRoutingSaving(true);
    setNotice(null);
    try {
      await evalHubApi.createCaseRoutingPolicy({
        namespace_id: namespaceId,
        name: caseRoutingPolicyName.trim(),
        description: "Cluster-balanced Golden and Bad Case candidate routing",
      });
      setNotice({
        tone: "success",
        text: zh
          ? "Golden / Bad Case 路由策略身份已创建；请新增不可变版本。"
          : "Golden / Bad Case routing policy created; add an immutable version next.",
      });
      await hub.refetch();
    } catch {
      setNotice({
        tone: "error",
        text: zh
          ? "路由策略创建失败；名称必须唯一且只能含安全字符。"
          : "Routing policy creation failed; its safe name must be unique.",
      });
    } finally {
      setCaseRoutingSaving(false);
    }
  }

  async function createCaseRoutingVersion(policy: EvaluationCaseRoutingPolicy) {
    const sourceVersion = (hub.data?.promotionPolicies ?? [])
      .flatMap((value) => value.versions)
      .find(
        (version) =>
          version.public_id === caseRoutingSourcePromotionVersionId &&
          version.diversity_dimension !== "NONE",
      );
    const goldenTarget = Number(caseRoutingGoldenTargetSize);
    const goldenMinimum = Number(caseRoutingGoldenMinimumSize);
    const badCaseTarget = Number(caseRoutingBadCaseTargetSize);
    const badCaseMinimum = Number(caseRoutingBadCaseMinimumSize);
    if (
      !sourceVersion ||
      ![goldenTarget, goldenMinimum, badCaseTarget, badCaseMinimum].every(
        (value) => Number.isInteger(value) && value >= 1 && value <= 20,
      ) ||
      goldenMinimum > goldenTarget ||
      badCaseMinimum > badCaseTarget
    ) {
      setNotice({
        tone: "error",
        text: zh
          ? "请选择带多样性桶的 Promotion 版本，并填写 1–20 的有效目标/最低数量。"
          : "Select a bucketed Promotion version and valid 1–20 target/minimum sizes.",
      });
      return;
    }
    setCaseRoutingActionId(policy.public_id);
    setNotice(null);
    try {
      await evalHubApi.createCaseRoutingPolicyVersion(policy.public_id, {
        source_promotion_policy_version_public_id: sourceVersion.public_id,
        strategy: "CLUSTER_ROUND_ROBIN",
        golden_target_size: goldenTarget,
        golden_min_items: goldenMinimum,
        bad_case_target_size: badCaseTarget,
        bad_case_min_items: badCaseMinimum,
      });
      setNotice({
        tone: "success",
        text: zh
          ? "不可变跨批次路由版本已创建；它严格绑定一个 Promotion 规则版本。"
          : "Immutable cross-batch routing version created and pinned to one Promotion version.",
      });
      await hub.refetch();
    } catch {
      setNotice({
        tone: "error",
        text: zh
          ? "路由版本创建失败；请检查 Promotion 多样性配置和重复版本。"
          : "Routing version failed; check Promotion diversity and duplicate configuration.",
      });
    } finally {
      setCaseRoutingActionId(null);
    }
  }

  async function runCaseRoutingVersion(version: EvaluationCaseRoutingPolicyVersion) {
    const sourceRunIds = caseRoutingPromotionRunIds
      .filter((publicId) =>
        (hub.data?.promotionRuns ?? []).some(
          (run) =>
            run.public_id === publicId &&
            run.policy_version_public_id ===
              version.source_promotion_policy_version_public_id,
        ),
      )
      .sort();
    if (
      !sourceRunIds.length ||
      !caseRoutingGoldenDatasetId ||
      !caseRoutingBadCaseDatasetId ||
      caseRoutingGoldenDatasetId === caseRoutingBadCaseDatasetId
    ) {
      setNotice({
        tone: "error",
        text: zh
          ? "请选择同一 Promotion 版本的运行，并指定两个不同的同步 Dataset。"
          : "Select runs from the pinned Promotion version and two distinct synchronized Datasets.",
      });
      return;
    }
    setCaseRoutingActionId(version.public_id);
    setNotice(null);
    try {
      const response = await evalHubApi.runCaseRoutingPolicyVersion(
        version.public_id,
        {
          promotion_run_public_ids: sourceRunIds,
          golden_dataset_public_id: caseRoutingGoldenDatasetId,
          bad_case_dataset_public_id: caseRoutingBadCaseDatasetId,
        },
        stableClientKey(
          `case-routing-run-${version.public_id}`,
          `${sourceRunIds.join("|")}|${caseRoutingGoldenDatasetId}|${caseRoutingBadCaseDatasetId}`,
        ),
      );
      setNotice({
        tone: "success",
        text: zh
          ? `跨批次路由完成：${response.data.outcome}；Golden ${response.data.golden_selected_count} 条，Bad Case ${response.data.bad_case_selected_count} 条。两个批次仍需分别人工审批。`
          : `Cross-batch routing ${response.data.outcome}: ${response.data.golden_selected_count} Golden and ${response.data.bad_case_selected_count} Bad Case candidates; both require separate review.`,
      });
      await hub.refetch();
    } catch {
      setNotice({
        tone: "error",
        text: zh
          ? "跨批次路由失败；来源必须同版本、互不重叠，且 Dataset 已同步。"
          : "Cross-batch routing failed; sources must share one version, not overlap, and target synchronized Datasets.",
      });
      await hub.refetch();
    } finally {
      setCaseRoutingActionId(null);
    }
  }

  async function createFailureTaxonomyPolicy() {
    if (namespaceId === null || !failureTaxonomyPolicyName.trim()) return;
    setFailureTaxonomySaving(true);
    setNotice(null);
    try {
      await evalHubApi.createFailureTaxonomyPolicy({
        namespace_id: namespaceId,
        name: failureTaxonomyPolicyName.trim(),
        description: "Versioned metadata-only failure classification",
      });
      setNotice({
        tone: "success",
        text: zh
          ? "失败分类策略身份已创建；请新增不可变规则版本。"
          : "Failure taxonomy policy created; add an immutable rule version next.",
      });
      await hub.refetch();
    } catch {
      setNotice({
        tone: "error",
        text: zh
          ? "失败分类策略创建失败；请检查名称是否重复。"
          : "Failure taxonomy policy creation failed; check for a duplicate name.",
      });
    } finally {
      setFailureTaxonomySaving(false);
    }
  }

  async function createFailureTaxonomyVersion(
    policy: EvaluationFailureTaxonomyPolicy,
  ) {
    const minOccurrences = Number(failureTaxonomyMinOccurrences);
    const minSourceRuns = Number(failureTaxonomyMinSourceRuns);
    const maxCandidates = Number(failureTaxonomyMaxCandidates);
    if (
      !failureTaxonomySourceRoutingVersionId ||
      !Number.isInteger(minOccurrences) ||
      minOccurrences < 2 ||
      minOccurrences > 20 ||
      !Number.isInteger(minSourceRuns) ||
      minSourceRuns < 2 ||
      minSourceRuns > 20 ||
      !Number.isInteger(maxCandidates) ||
      maxCandidates < 1 ||
      maxCandidates > 20
    ) {
      setNotice({
        tone: "error",
        text: zh
          ? "请选择 Case Routing 版本；重复阈值需为 2～20，候选上限需为 1～20。"
          : "Select a Case Routing version; recurrence thresholds must be 2–20 and the candidate cap 1–20.",
      });
      return;
    }
    setFailureTaxonomyActionId(policy.public_id);
    setNotice(null);
    try {
      await evalHubApi.createFailureTaxonomyPolicyVersion(policy.public_id, {
        source_case_routing_policy_version_public_id:
          failureTaxonomySourceRoutingVersionId,
        source_semantic_clustering_policy_version_public_id:
          failureTaxonomySemanticVersionId || null,
        min_cluster_occurrences: minOccurrences,
        min_source_runs: minSourceRuns,
        include_isolated: failureTaxonomyIncludeIsolated,
        max_candidates: maxCandidates,
      });
      setNotice({
        tone: "success",
        text: zh
          ? "不可变失败分类版本已创建，并精确绑定一个 Case Routing 版本。"
          : "Immutable failure taxonomy version created and pinned to one Case Routing version.",
      });
      await hub.refetch();
    } catch {
      setNotice({
        tone: "error",
        text: zh
          ? "失败分类版本创建失败；请检查来源版本和重复配置。"
          : "Failure taxonomy version failed; check the source version and duplicate configuration.",
      });
    } finally {
      setFailureTaxonomyActionId(null);
    }
  }

  async function runFailureTaxonomyVersion(
    version: EvaluationFailureTaxonomyPolicyVersion,
  ) {
    const sourceRun = (hub.data?.caseRoutingRuns ?? []).find(
      (run) =>
        run.public_id === experienceSourceRoutingRunId &&
        run.outcome === "ROUTED" &&
        run.policy_version_public_id ===
          version.source_case_routing_policy_version_public_id,
    );
    if (!sourceRun) {
      setNotice({
        tone: "error",
        text: zh
          ? "请选择该规则版本精确绑定的 ROUTED Case Routing 运行。"
          : "Select a ROUTED Case Routing run from the exactly pinned version.",
      });
      return;
    }
    const semanticRun = version.source_semantic_clustering_policy_version_public_id
      ? (hub.data?.semanticClusteringRuns ?? []).find(
          (run) =>
            run.public_id === experienceSemanticRunId &&
            run.policy_version_public_id ===
              version.source_semantic_clustering_policy_version_public_id &&
            run.source_case_routing_run_public_id === sourceRun.public_id,
        )
      : null;
    if (
      version.source_semantic_clustering_policy_version_public_id &&
      !semanticRun
    ) {
      setNotice({
        tone: "error",
        text: zh
          ? "该分类版本要求精确匹配的语义聚类运行。"
          : "This taxonomy version requires an exactly matching semantic clustering run.",
      });
      return;
    }
    setFailureTaxonomyActionId(version.public_id);
    setNotice(null);
    try {
      const response = await evalHubApi.runFailureTaxonomyPolicyVersion(
        version.public_id,
        sourceRun.public_id,
        semanticRun?.public_id ?? null,
        stableClientKey(
          `experience-extraction-${version.public_id}`,
          `${sourceRun.public_id}|${semanticRun?.public_id ?? "metadata"}`,
        ),
      );
      setNotice({
        tone: "success",
        text: zh
          ? `Experience 提取完成：${response.data.outcome}；生成 ${response.data.candidate_count} 个待人工评审候选。`
          : `Experience extraction ${response.data.outcome}: ${response.data.candidate_count} candidates await human review.`,
      });
      await hub.refetch();
    } catch {
      setNotice({
        tone: "error",
        text: zh
          ? "Experience 提取失败；请检查来源运行、精确版本绑定和幂等冲突。"
          : "Experience extraction failed; check the source run, exact version pin, and idempotency conflicts.",
      });
      await hub.refetch();
    } finally {
      setFailureTaxonomyActionId(null);
    }
  }

  async function reviewExperienceCandidate(
    candidate: EvaluationExperienceCandidate,
    decision: "APPROVED" | "REJECTED",
  ) {
    const comment = (experienceReviewComments[candidate.public_id] ?? "").trim();
    if (decision === "REJECTED" && comment.length < 5) {
      setNotice({
        tone: "error",
        text: zh
          ? "拒绝 Experience 候选至少需要 5 个字符的原因。"
          : "Rejecting an Experience candidate requires at least 5 characters.",
      });
      return;
    }
    setFailureTaxonomyActionId(candidate.public_id);
    setNotice(null);
    try {
      await evalHubApi.reviewExperienceCandidate(candidate.public_id, {
        decision,
        comment: comment || null,
      });
      setNotice({
        tone: "success",
        text: zh
          ? `Experience 候选已${decision === "APPROVED" ? "批准" : "拒绝"}；该决定只确认候选，不会自动修改生产 Agent。`
          : `Experience candidate ${decision.toLowerCase()}; this confirms the candidate but does not modify a production Agent.`,
      });
      await hub.refetch();
    } catch {
      setNotice({
        tone: "error",
        text: zh
          ? "Experience 候选评审失败；该候选可能已经有最终决定。"
          : "Experience candidate review failed; it may already have a final decision.",
      });
    } finally {
      setFailureTaxonomyActionId(null);
    }
  }

  async function createExperienceAsset() {
    if (
      namespaceId === null ||
      !experienceAssetCandidateId ||
      !experienceAssetName.trim()
    ) {
      setNotice({
        tone: "error",
        text: zh
          ? "请选择已批准候选并填写 Experience 资产名称。"
          : "Select an approved candidate and enter an Experience asset name.",
      });
      return;
    }
    setExperienceAssetActionId(experienceAssetCandidateId);
    setNotice(null);
    try {
      const response = await evalHubApi.createExperienceAsset({
        namespace_id: namespaceId,
        source_candidate_public_id: experienceAssetCandidateId,
        name: experienceAssetName.trim(),
        description: experienceAssetDescription.trim() || null,
      });
      setExperienceAssetId(response.data.public_id);
      setExperienceAssetName("");
      setExperienceAssetDescription("");
      setNotice({
        tone: "success",
        text: zh
          ? "Experience 资产身份已创建；下一步编写不可变版本草稿。"
          : "Experience asset created; author an immutable version draft next.",
      });
      await hub.refetch();
    } catch {
      setNotice({
        tone: "error",
        text: zh
          ? "Experience 资产创建失败；候选必须已批准且只能被认领一次。"
          : "Experience asset creation failed; the candidate must be approved and unclaimed.",
      });
    } finally {
      setExperienceAssetActionId(null);
    }
  }

  async function createExperienceVersion() {
    if (
      !experienceAssetId ||
      experienceVersionBody.trim().length < 10 ||
      experienceVersionApplicability.trim().length < 5
    ) {
      setNotice({
        tone: "error",
        text: zh
          ? "请选择资产；经验正文至少 10 个字符，适用边界至少 5 个字符。"
          : "Select an asset; body requires 10 characters and applicability requires 5.",
      });
      return;
    }
    setExperienceAssetActionId(experienceAssetId);
    setNotice(null);
    try {
      await evalHubApi.createExperienceAssetVersion(experienceAssetId, {
        body: experienceVersionBody.trim(),
        applicability: experienceVersionApplicability.trim(),
        change_summary: experienceVersionChangeSummary.trim() || null,
      });
      setExperienceVersionBody("");
      setExperienceVersionApplicability("");
      setExperienceVersionChangeSummary("");
      setNotice({
        tone: "success",
        text: zh
          ? "不可变 Experience 草稿版本已创建；正文未写入 Audit 或 Outbox。"
          : "Immutable Experience draft created; content was not copied to Audit or Outbox.",
      });
      await hub.refetch();
    } catch {
      setNotice({
        tone: "error",
        text: zh
          ? "Experience 版本创建失败；请检查重复内容或字段长度。"
          : "Experience version failed; check duplicate content and field lengths.",
      });
    } finally {
      setExperienceAssetActionId(null);
    }
  }

  async function requestExperienceActivation(
    version: EvaluationExperienceAssetVersion,
  ) {
    setExperienceAssetActionId(version.public_id);
    setNotice(null);
    try {
      await evalHubApi.requestExperienceActivation(version.public_id, {
        request_note: experienceActivationNote.trim() || null,
      });
      setExperienceActivationNote("");
      setNotice({
        tone: "success",
        text: zh
          ? "激活申请已提交；必须由不同于作者和请求人的成员审批。"
          : "Activation requested; a member other than the author and requester must review it.",
      });
      await hub.refetch();
    } catch {
      setNotice({
        tone: "error",
        text: zh
          ? "激活申请失败；仅 DRAFT 版本可提交一次。"
          : "Activation request failed; only a DRAFT version may be submitted once.",
      });
    } finally {
      setExperienceAssetActionId(null);
    }
  }

  async function reviewExperienceActivation(
    version: EvaluationExperienceAssetVersion,
    decision: "APPROVED" | "REJECTED",
  ) {
    const activationRequest = version.activation_request;
    if (!activationRequest) return;
    const comment = (
      experienceActivationComments[activationRequest.public_id] ?? ""
    ).trim();
    if (decision === "REJECTED" && comment.length < 5) {
      setNotice({
        tone: "error",
        text: zh
          ? "拒绝激活至少需要 5 个字符的原因。"
          : "Rejecting activation requires at least 5 characters.",
      });
      return;
    }
    setExperienceAssetActionId(activationRequest.public_id);
    setNotice(null);
    try {
      await evalHubApi.reviewExperienceActivation(activationRequest.public_id, {
        decision,
        comment: comment || null,
      });
      setNotice({
        tone: "success",
        text: zh
          ? `Experience 版本已${decision === "APPROVED" ? "激活" : "拒绝"}；这里只改变 DuckDock 控制面状态，不会自动下发 Hermes。`
          : `Experience version ${decision === "APPROVED" ? "activated" : "rejected"}; this changes DuckDock control-plane state only and does not push to Hermes.`,
      });
      await hub.refetch();
    } catch {
      setNotice({
        tone: "error",
        text: zh
          ? "激活审批失败；审批人必须与作者及请求人不同，且决定不可覆盖。"
          : "Activation review failed; the reviewer must be independent and the decision is final.",
      });
    } finally {
      setExperienceAssetActionId(null);
    }
  }

  async function materializeCurationWithRetry(batchPublicId: string) {
    const idempotencyKey = `curation-materialize-${batchPublicId}`;
    try {
      return await evalHubApi.materializeCurationBatch(
        batchPublicId,
        idempotencyKey,
      );
    } catch {
      await new Promise((resolve) => window.setTimeout(resolve, 350));
      return evalHubApi.materializeCurationBatch(
        batchPublicId,
        idempotencyKey,
      );
    }
  }

  async function materializeApprovedCuration(batch: EvaluationDatasetCurationBatch) {
    setCurationActionId(batch.public_id);
    setNotice(null);
    try {
      await materializeCurationWithRetry(batch.public_id);
      setNotice({
        tone: "success",
        text: zh
          ? "批准批次已批量写入 Langfuse，并只生成一个不可变 Dataset Version。"
          : "Approved sources were materialized as one immutable Dataset version.",
      });
      markBatchCandidatesMaterialized(batch);
      await hub.refetch();
    } catch {
      setNotice({
        tone: "error",
        text: zh
          ? "批量入集失败；批准记录仍然有效，可在 Langfuse 恢复后安全重试。"
          : "Materialization failed; approval remains valid and can be retried.",
      });
      await hub.refetch();
    } finally {
      setCurationActionId(null);
    }
  }

  async function reviewCurationBatch(
    batch: EvaluationDatasetCurationBatch,
    decision: "APPROVED" | "REJECTED",
  ) {
    const comment = (curationComments[batch.public_id] ?? "").trim();
    if (decision === "REJECTED" && comment.length < 5) {
      setNotice({
        tone: "error",
        text: zh ? "拒绝原因至少需要 5 个字符。" : "A rejection reason needs at least 5 characters.",
      });
      return;
    }
    setCurationActionId(batch.public_id);
    setNotice(null);
    try {
      const reviewed = await evalHubApi.reviewCurationBatch(
        batch.public_id,
        { decision, comment: comment || null },
        stableClientKey(
          `curation-review-${batch.public_id}-${decision.toLowerCase()}`,
          comment || "__EMPTY__",
        ),
      );
      if (decision === "APPROVED") {
        await materializeCurationWithRetry(batch.public_id);
        setNotice({
          tone: "success",
          text: zh
            ? "批次已批准并完成批量入集；审核、来源清单和 Dataset Version 均不可覆盖。"
            : "Batch approved and materialized with immutable evidence.",
        });
        markBatchCandidatesMaterialized(batch);
      } else {
        setNotice({
          tone: "success",
          text: zh
            ? "批次已拒绝并记录最终审核证据。"
            : "Batch rejected and recorded as final evidence.",
        });
      }
      setCurationComments((current) => ({ ...current, [batch.public_id]: "" }));
      if (reviewed.data.status === "MATERIALIZED") {
        setSelectedCandidateRefs(new Set());
      }
      await hub.refetch();
    } catch {
      setNotice({
        tone: "error",
        text: zh
          ? "审核或批量入集未完整完成；请刷新批次状态，已写入的不可变步骤不会被重复。"
          : "Review or materialization did not finish; refresh and retry safely.",
      });
      await hub.refetch();
    } finally {
      setCurationActionId(null);
    }
  }

  const tabs: Array<{ key: Tab; label: string }> = [
    { key: "overview", label: zh ? "治理总览" : "Overview" },
    { key: "data-flywheel", label: "Trace2Dataset" },
    { key: "comparisons", label: zh ? "基线对比" : "Comparisons" },
    { key: "release", label: zh ? "候选门禁" : "Release gates" },
  ];

  return (
    <div className="app-page max-w-7xl space-y-6">
      <PageHeader
        eyebrow="EVALUATION GOVERNANCE"
        title="Eval Hub"
        description={
          zh
            ? "统一查看 Langfuse 数据集、评测执行、可复现基线对比，以及精确绑定到候选版本的发布证据。"
            : "Govern Langfuse datasets, evaluation runs, reproducible comparisons, and candidate-pinned release evidence."
        }
        actions={
          <div className="flex items-center gap-2">
            <select
              className="h-10 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-700"
              value={namespaceId ?? ""}
              onChange={(event) => {
                setNamespaceId(Number(event.target.value));
                setGateDecisions({});
                setReviewDraft(null);
              }}
              aria-label={zh ? "命名空间" : "Namespace"}
            >
              {(namespaces.data ?? []).map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
            <Button
              variant="secondary"
              onClick={() => {
                setNotice(null);
                void hub.refetch();
              }}
              disabled={hub.isFetching}
              icon={<RefreshCw className={`h-4 w-4 ${hub.isFetching ? "animate-spin" : ""}`} />}
            >
              {zh ? "刷新" : "Refresh"}
            </Button>
          </div>
        }
      />

      {hub.error ? (
        <div className="soft-rose rounded-lg px-4 py-3 text-sm">
          {zh ? "Eval Hub 加载失败，请检查 Namespace 权限和 v2 API。" : "Eval Hub failed to load."}
        </div>
      ) : null}
      {notice ? (
        <div
          role="status"
          className={`rounded-lg px-4 py-3 text-sm ${
            notice.tone === "success" ? "soft-emerald" : "soft-rose"
          }`}
        >
          {notice.text}
        </div>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          label={zh ? "数据集 / 评测器" : "Datasets / evaluators"}
          value={`${summary.datasetCount} / ${summary.evaluatorCount}`}
          detail={zh ? "版本化、可追溯的评测输入" : "Versioned evaluation inputs"}
          icon={Database}
          loading={hub.isLoading}
        />
        <MetricCard
          label={zh ? "完成的评测" : "Completed runs"}
          value={`${summary.completedEvaluationCount} / ${summary.evaluationCount}`}
          detail={zh ? "含结果完整性与 Manifest" : "With completeness and manifests"}
          icon={FlaskConical}
          tone="indigo"
          loading={hub.isLoading}
        />
        <MetricCard
          label={zh ? "通过的对比" : "Passing comparisons"}
          value={summary.passedComparisonCount}
          detail={zh ? "固定基线、候选与策略版本" : "Pinned baseline, candidate, and policy"}
          icon={GitCompareArrows}
          loading={hub.isLoading}
        />
        <MetricCard
          label={zh ? "已人工评审" : "Human-reviewed"}
          value={`${summary.reviewedBindingCount} / ${hub.data?.bindings.length ?? 0}`}
          detail={zh ? "最终决定不可覆盖" : "Final decisions are immutable"}
          icon={ShieldCheck}
          tone={hub.data?.bindings.some((item) => item.review?.decision === "REJECTED") ? "rose" : "neutral"}
          loading={hub.isLoading}
        />
      </div>

      <div className="flex gap-1 rounded-lg border border-slate-200 bg-white p-1 shadow-sm">
        {tabs.map((item) => (
          <button
            key={item.key}
            type="button"
            onClick={() => setTab(item.key)}
            className={`rounded-md px-4 py-2 text-sm font-medium transition ${
              tab === item.key
                ? "bg-indigo-50 text-indigo-700"
                : "text-slate-500 hover:bg-slate-50 hover:text-slate-800"
            }`}
          >
            {item.label}
          </button>
        ))}
      </div>

      {hub.isLoading ? (
        <Card padded>
          <EmptyState text={zh ? "正在加载评测治理数据…" : "Loading evaluation governance data…"} />
        </Card>
      ) : null}

      {!hub.isLoading && tab === "overview" ? (
        <OverviewPanel
          zh={zh}
          datasets={hub.data?.datasets ?? []}
          evaluations={hub.data?.evaluations ?? []}
          comparisons={hub.data?.comparisons ?? []}
          bindings={hub.data?.bindings ?? []}
          onOpenComparisons={() => setTab("comparisons")}
          onOpenRelease={() => setTab("release")}
        />
      ) : null}

      {!hub.isLoading && tab === "comparisons" ? (
        <ComparisonPanel
          zh={zh}
          locale={locale}
          comparisons={hub.data?.comparisons ?? []}
        />
      ) : null}

      {!hub.isLoading && tab === "data-flywheel" ? (
        <div className="space-y-6">
          <SemanticClusteringPanel
            zh={zh}
            locale={locale}
            namespaceId={namespaceId as number}
            caseRoutingPolicies={hub.data?.caseRoutingPolicies ?? []}
            caseRoutingRuns={hub.data?.caseRoutingRuns ?? []}
            policies={hub.data?.semanticClusteringPolicies ?? []}
            runs={hub.data?.semanticClusteringRuns ?? []}
            onChanged={() => hub.refetch()}
            onNotice={setNotice}
          />
          <SemanticRegressionPanel
            zh={zh}
            locale={locale}
            namespaceId={namespaceId as number}
            runs={hub.data?.semanticClusteringRuns ?? []}
            policies={hub.data?.semanticRegressionPolicies ?? []}
            comparisons={hub.data?.semanticRegressionComparisons ?? []}
            onChanged={() => hub.refetch()}
            onNotice={setNotice}
          />
          <SemanticMonitorPanel
            zh={zh}
            locale={locale}
            namespaceId={namespaceId as number}
            semanticPolicies={hub.data?.semanticClusteringPolicies ?? []}
            semanticRuns={hub.data?.semanticClusteringRuns ?? []}
            regressionPolicies={hub.data?.semanticRegressionPolicies ?? []}
            monitors={hub.data?.semanticMonitors ?? []}
            monitorRuns={hub.data?.semanticMonitorRuns ?? []}
            alerts={hub.data?.semanticMonitorAlerts ?? []}
            onChanged={() => hub.refetch()}
            onNotice={setNotice}
          />
          <TraceDatasetPanel
          zh={zh}
          locale={locale}
          datasets={hub.data?.datasets ?? []}
          materializations={hub.data?.materializations ?? []}
          curationBatches={hub.data?.curationBatches ?? []}
          samplingPolicies={hub.data?.samplingPolicies ?? []}
          samplingRuns={hub.data?.samplingRuns ?? []}
          annotationProviderQueues={hub.data?.annotationProviderQueues ?? []}
          annotationQueueBindings={hub.data?.annotationQueueBindings ?? []}
          annotationDispatches={hub.data?.annotationDispatches ?? []}
          promotionPolicies={hub.data?.promotionPolicies ?? []}
          promotionRuns={hub.data?.promotionRuns ?? []}
          caseRoutingPolicies={hub.data?.caseRoutingPolicies ?? []}
          caseRoutingRuns={hub.data?.caseRoutingRuns ?? []}
          semanticClusteringPolicies={hub.data?.semanticClusteringPolicies ?? []}
          semanticClusteringRuns={hub.data?.semanticClusteringRuns ?? []}
          failureTaxonomyPolicies={hub.data?.failureTaxonomyPolicies ?? []}
          experienceExtractionRuns={hub.data?.experienceExtractionRuns ?? []}
          selectedDatasetId={materializationDatasetId}
          traceId={traceId}
          observationId={observationId}
          materializing={materializing}
          candidates={candidates}
          selectedCandidateRefs={selectedCandidateRefs}
          candidateName={candidateName}
          candidateEnvironment={candidateEnvironment}
          candidateType={candidateType}
          candidateWindowHours={candidateWindowHours}
          candidateRootOnly={candidateRootOnly}
          candidateLoading={candidateLoading}
          curationSubmitting={curationSubmitting}
          curationActionId={curationActionId}
          curationComments={curationComments}
          samplingPolicyName={samplingPolicyName}
          samplingSampleSize={samplingSampleSize}
          samplingMinimumSize={samplingMinimumSize}
          samplingSaving={samplingSaving}
          samplingActionId={samplingActionId}
          annotationProviderQueueRef={annotationProviderQueueRef}
          annotationBindingId={annotationBindingId}
          annotationCurationBatchId={annotationCurationBatchId}
          annotationSaving={annotationSaving}
          annotationActionId={annotationActionId}
          promotionPolicyName={promotionPolicyName}
          promotionScoreConfigId={promotionScoreConfigId}
          promotionScoreDataType={promotionScoreDataType}
          promotionMinimumScore={promotionMinimumScore}
          promotionAcceptedValues={promotionAcceptedValues}
          promotionDiversityDimension={promotionDiversityDimension}
          promotionMinimumBuckets={promotionMinimumBuckets}
          promotionDispatchId={promotionDispatchId}
          promotionSaving={promotionSaving}
          promotionActionId={promotionActionId}
          caseRoutingPolicyName={caseRoutingPolicyName}
          caseRoutingSourcePromotionVersionId={caseRoutingSourcePromotionVersionId}
          caseRoutingGoldenTargetSize={caseRoutingGoldenTargetSize}
          caseRoutingGoldenMinimumSize={caseRoutingGoldenMinimumSize}
          caseRoutingBadCaseTargetSize={caseRoutingBadCaseTargetSize}
          caseRoutingBadCaseMinimumSize={caseRoutingBadCaseMinimumSize}
          caseRoutingPromotionRunIds={caseRoutingPromotionRunIds}
          caseRoutingGoldenDatasetId={caseRoutingGoldenDatasetId}
          caseRoutingBadCaseDatasetId={caseRoutingBadCaseDatasetId}
          caseRoutingSaving={caseRoutingSaving}
          caseRoutingActionId={caseRoutingActionId}
          failureTaxonomyPolicyName={failureTaxonomyPolicyName}
          failureTaxonomySourceRoutingVersionId={failureTaxonomySourceRoutingVersionId}
          failureTaxonomySemanticVersionId={failureTaxonomySemanticVersionId}
          failureTaxonomyMinOccurrences={failureTaxonomyMinOccurrences}
          failureTaxonomyMinSourceRuns={failureTaxonomyMinSourceRuns}
          failureTaxonomyIncludeIsolated={failureTaxonomyIncludeIsolated}
          failureTaxonomyMaxCandidates={failureTaxonomyMaxCandidates}
          experienceSourceRoutingRunId={experienceSourceRoutingRunId}
          experienceSemanticRunId={experienceSemanticRunId}
          failureTaxonomySaving={failureTaxonomySaving}
          failureTaxonomyActionId={failureTaxonomyActionId}
          experienceReviewComments={experienceReviewComments}
          onSelectDataset={(value) => {
            setMaterializationDatasetId(value);
            setCandidates([]);
            setSelectedCandidateRefs(new Set());
          }}
          onChangeTrace={setTraceId}
          onChangeObservation={setObservationId}
          onSubmit={() => void materializeTrace()}
          onChangeCandidateName={setCandidateName}
          onChangeCandidateEnvironment={setCandidateEnvironment}
          onChangeCandidateType={setCandidateType}
          onChangeCandidateWindowHours={setCandidateWindowHours}
          onChangeCandidateRootOnly={setCandidateRootOnly}
          onSearchCandidates={() => void searchTraceCandidates()}
          onToggleCandidate={toggleCandidate}
          onSubmitCuration={() => void submitCurationBatch()}
          onChangeSamplingPolicyName={setSamplingPolicyName}
          onChangeSamplingSampleSize={setSamplingSampleSize}
          onChangeSamplingMinimumSize={setSamplingMinimumSize}
          onCreateSamplingPolicy={() => void createSamplingPolicy()}
          onCreateSamplingVersion={(policy) => void createSamplingVersion(policy)}
          onRunSamplingVersion={(version) => void runSamplingVersion(version)}
          onChangeAnnotationProviderQueue={setAnnotationProviderQueueRef}
          onChangeAnnotationBinding={setAnnotationBindingId}
          onChangeAnnotationCurationBatch={setAnnotationCurationBatchId}
          onBindAnnotationQueue={() => void bindAnnotationQueue()}
          onDispatchAnnotationBatch={() => void dispatchAnnotationBatch()}
          onRetryAnnotationDispatch={(dispatch) => void retryAnnotationDispatch(dispatch)}
          onReconcileAnnotationDispatch={(dispatch) => void reconcileAnnotationDispatch(dispatch)}
          onChangePromotionPolicyName={setPromotionPolicyName}
          onChangePromotionScoreConfig={setPromotionScoreConfigId}
          onChangePromotionScoreDataType={setPromotionScoreDataType}
          onChangePromotionMinimumScore={setPromotionMinimumScore}
          onChangePromotionAcceptedValues={setPromotionAcceptedValues}
          onChangePromotionDiversityDimension={setPromotionDiversityDimension}
          onChangePromotionMinimumBuckets={setPromotionMinimumBuckets}
          onChangePromotionDispatch={setPromotionDispatchId}
          onCreatePromotionPolicy={() => void createPromotionPolicy()}
          onCreatePromotionVersion={(policy) => void createPromotionVersion(policy)}
          onRunPromotionVersion={(version) => void runPromotionVersion(version)}
          onChangeCaseRoutingPolicyName={setCaseRoutingPolicyName}
          onChangeCaseRoutingSourcePromotionVersion={setCaseRoutingSourcePromotionVersionId}
          onChangeCaseRoutingGoldenTargetSize={setCaseRoutingGoldenTargetSize}
          onChangeCaseRoutingGoldenMinimumSize={setCaseRoutingGoldenMinimumSize}
          onChangeCaseRoutingBadCaseTargetSize={setCaseRoutingBadCaseTargetSize}
          onChangeCaseRoutingBadCaseMinimumSize={setCaseRoutingBadCaseMinimumSize}
          onChangeCaseRoutingPromotionRuns={setCaseRoutingPromotionRunIds}
          onChangeCaseRoutingGoldenDataset={setCaseRoutingGoldenDatasetId}
          onChangeCaseRoutingBadCaseDataset={setCaseRoutingBadCaseDatasetId}
          onCreateCaseRoutingPolicy={() => void createCaseRoutingPolicy()}
          onCreateCaseRoutingVersion={(policy) => void createCaseRoutingVersion(policy)}
          onRunCaseRoutingVersion={(version) => void runCaseRoutingVersion(version)}
          onChangeFailureTaxonomyPolicyName={setFailureTaxonomyPolicyName}
          onChangeFailureTaxonomySourceRoutingVersion={setFailureTaxonomySourceRoutingVersionId}
          onChangeFailureTaxonomySemanticVersion={setFailureTaxonomySemanticVersionId}
          onChangeFailureTaxonomyMinOccurrences={setFailureTaxonomyMinOccurrences}
          onChangeFailureTaxonomyMinSourceRuns={setFailureTaxonomyMinSourceRuns}
          onChangeFailureTaxonomyIncludeIsolated={setFailureTaxonomyIncludeIsolated}
          onChangeFailureTaxonomyMaxCandidates={setFailureTaxonomyMaxCandidates}
          onChangeExperienceSourceRoutingRun={setExperienceSourceRoutingRunId}
          onChangeExperienceSemanticRun={setExperienceSemanticRunId}
          onCreateFailureTaxonomyPolicy={() => void createFailureTaxonomyPolicy()}
          onCreateFailureTaxonomyVersion={(policy) => void createFailureTaxonomyVersion(policy)}
          onRunFailureTaxonomyVersion={(version) => void runFailureTaxonomyVersion(version)}
          onChangeExperienceReviewComment={(publicId, comment) =>
            setExperienceReviewComments((current) => ({
              ...current,
              [publicId]: comment,
            }))
          }
          onReviewExperienceCandidate={(candidate, decision) =>
            void reviewExperienceCandidate(candidate, decision)
          }
          onChangeCurationComment={(publicId, comment) =>
            setCurationComments((current) => ({ ...current, [publicId]: comment }))
          }
          onReviewCuration={(batch, decision) => void reviewCurationBatch(batch, decision)}
            onMaterializeCuration={(batch) => void materializeApprovedCuration(batch)}
          />
          <ExperienceAssetsPanel
            zh={zh}
            locale={locale}
            extractionRuns={hub.data?.experienceExtractionRuns ?? []}
            assets={hub.data?.experienceAssets ?? []}
            candidateId={experienceAssetCandidateId}
            assetName={experienceAssetName}
            assetDescription={experienceAssetDescription}
            selectedAssetId={experienceAssetId}
            versionBody={experienceVersionBody}
            versionApplicability={experienceVersionApplicability}
            versionChangeSummary={experienceVersionChangeSummary}
            activationNote={experienceActivationNote}
            activationComments={experienceActivationComments}
            actionId={experienceAssetActionId}
            onChangeCandidate={setExperienceAssetCandidateId}
            onChangeAssetName={setExperienceAssetName}
            onChangeAssetDescription={setExperienceAssetDescription}
            onChangeSelectedAsset={setExperienceAssetId}
            onChangeVersionBody={setExperienceVersionBody}
            onChangeVersionApplicability={setExperienceVersionApplicability}
            onChangeVersionChangeSummary={setExperienceVersionChangeSummary}
            onChangeActivationNote={setExperienceActivationNote}
            onChangeActivationComment={(publicId, comment) =>
              setExperienceActivationComments((current) => ({
                ...current,
                [publicId]: comment,
              }))
            }
            onCreateAsset={() => void createExperienceAsset()}
            onCreateVersion={() => void createExperienceVersion()}
            onRequestActivation={(version) =>
              void requestExperienceActivation(version)
            }
            onReviewActivation={(version, decision) =>
              void reviewExperienceActivation(version, decision)
            }
          />
        </div>
      ) : null}

      {!hub.isLoading && tab === "release" ? (
        <ReleasePanel
          zh={zh}
          locale={locale}
          bindings={hub.data?.bindings ?? []}
          reviewDraft={reviewDraft}
          reviewing={reviewing}
          gateLoading={gateLoading}
          gateDecisions={gateDecisions}
          onOpenReview={openReview}
          onChangeComment={(comment) =>
            setReviewDraft((current) => (current ? { ...current, comment } : current))
          }
          onCancelReview={() => setReviewDraft(null)}
          onSubmitReview={() => void submitReview()}
          onEvaluateGate={(binding) => void evaluateGate(binding)}
        />
      ) : null}
    </div>
  );
}

function SemanticClusteringPanel({
  zh,
  locale,
  namespaceId,
  caseRoutingPolicies,
  caseRoutingRuns,
  policies,
  runs,
  onChanged,
  onNotice,
}: {
  zh: boolean;
  locale: string;
  namespaceId: number;
  caseRoutingPolicies: EvaluationCaseRoutingPolicy[];
  caseRoutingRuns: EvaluationCaseRoutingRun[];
  policies: EvaluationSemanticClusteringPolicy[];
  runs: EvaluationSemanticClusteringRun[];
  onChanged: () => Promise<unknown>;
  onNotice: (notice: { tone: "success" | "error"; text: string } | null) => void;
}) {
  const [name, setName] = useState("semantic-failure-clustering");
  const [routingVersionId, setRoutingVersionId] = useState("");
  const [routingRunId, setRoutingRunId] = useState("");
  const [modelRef, setModelRef] = useState("bge-small-en-v1.5");
  const [threshold, setThreshold] = useState("0.82");
  const [actionId, setActionId] = useState<string | null>(null);
  const routingVersions = caseRoutingPolicies.flatMap((policy) =>
    policy.versions.map((version) => ({ policy, version })),
  );
  const eligibleRuns = caseRoutingRuns.filter(
    (run) =>
      run.outcome === "ROUTED" &&
      run.policy_version_public_id === routingVersionId &&
      run.bad_case_selected_count > 0,
  );

  useEffect(() => {
    if (
      routingVersions.length &&
      !routingVersions.some(({ version }) => version.public_id === routingVersionId)
    ) {
      setRoutingVersionId(routingVersions[0].version.public_id);
    }
  }, [routingVersionId, routingVersions]);

  useEffect(() => {
    if (
      eligibleRuns.length &&
      !eligibleRuns.some((run) => run.public_id === routingRunId)
    ) {
      setRoutingRunId(eligibleRuns[0].public_id);
    } else if (!eligibleRuns.length && routingRunId) {
      setRoutingRunId("");
    }
  }, [eligibleRuns, routingRunId]);

  async function createPolicy() {
    if (!name.trim()) return;
    setActionId("policy");
    onNotice(null);
    try {
      await evalHubApi.createSemanticClusteringPolicy({
        namespace_id: namespaceId,
        name: name.trim(),
        description: "Provider-neutral ephemeral embeddings over Langfuse observations",
      });
      onNotice({
        tone: "success",
        text: zh
          ? "语义聚类策略已创建；下一步冻结模型与相似度版本。"
          : "Semantic clustering policy created; freeze a model and threshold version next.",
      });
      await onChanged();
    } catch {
      onNotice({
        tone: "error",
        text: zh ? "语义聚类策略创建失败。" : "Semantic clustering policy creation failed.",
      });
    } finally {
      setActionId(null);
    }
  }

  async function createVersion(policy: EvaluationSemanticClusteringPolicy) {
    const numericThreshold = Number(threshold);
    if (!routingVersionId || !modelRef.trim() || numericThreshold < 0 || numericThreshold > 1) {
      onNotice({
        tone: "error",
        text: zh ? "请选择路由版本并填写 0～1 的相似度阈值。" : "Select a routing version and a similarity threshold from 0 to 1.",
      });
      return;
    }
    setActionId(policy.public_id);
    onNotice(null);
    try {
      await evalHubApi.createSemanticClusteringPolicyVersion(policy.public_id, {
        source_case_routing_policy_version_public_id: routingVersionId,
        embedding_profile: "openai-compatible-local",
        model_ref: modelRef.trim(),
        dimensions: 384,
        similarity_threshold: numericThreshold,
        min_cluster_size: 2,
        max_items: 20,
        max_content_chars: 8000,
      });
      onNotice({
        tone: "success",
        text: zh ? "不可变语义聚类版本已创建。" : "Immutable semantic clustering version created.",
      });
      await onChanged();
    } catch {
      onNotice({
        tone: "error",
        text: zh ? "语义聚类版本创建失败；请检查重复配置。" : "Semantic clustering version failed; check for duplicate configuration.",
      });
    } finally {
      setActionId(null);
    }
  }

  async function runVersion(version: EvaluationSemanticClusteringPolicyVersion) {
    const sourceRun = eligibleRuns.find(
      (run) =>
        run.public_id === routingRunId &&
        run.policy_version_public_id ===
          version.source_case_routing_policy_version_public_id,
    );
    if (!sourceRun) return;
    setActionId(version.public_id);
    onNotice(null);
    try {
      const response = await evalHubApi.runSemanticClusteringPolicyVersion(
        version.public_id,
        sourceRun.public_id,
        stableClientKey(`semantic-clustering-${version.public_id}`, sourceRun.public_id),
      );
      onNotice({
        tone: "success",
        text: zh
          ? `真实语义聚类完成：${response.data.cluster_count} 个簇，${response.data.eligible_cluster_count} 个达到阈值。`
          : `Semantic clustering completed: ${response.data.cluster_count} clusters, ${response.data.eligible_cluster_count} eligible.`,
      });
      await onChanged();
    } catch {
      onNotice({
        tone: "error",
        text: zh ? "语义聚类失败；请检查 Langfuse 来源与本地 embedding 服务。" : "Semantic clustering failed; check the Langfuse source and local embedding service.",
      });
      await onChanged();
    } finally {
      setActionId(null);
    }
  }

  return (
    <Card
      title={zh ? "真实语义失败聚类" : "Semantic failure clustering"}
      description={
        zh
          ? "运行时临时读取 Langfuse IO，调用可替换的 OpenAI-compatible embedding；DuckDock 只保存摘要、相似度和簇成员。"
          : "Ephemerally read Langfuse IO and call a replaceable OpenAI-compatible embedding endpoint; DuckDock stores only digests, similarities, and membership."
      }
      padded
    >
      <div className="space-y-4">
        <div className="grid gap-3 lg:grid-cols-4">
          <label className="block text-sm font-medium text-slate-700">
            {zh ? "策略名称" : "Policy name"}
            <input className="mt-1 h-10 w-full rounded-lg border border-slate-200 px-3 text-sm" value={name} onChange={(event) => setName(event.target.value)} />
          </label>
          <label className="block text-sm font-medium text-slate-700">
            {zh ? "来源路由版本" : "Source routing version"}
            <select className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm" value={routingVersionId} onChange={(event) => setRoutingVersionId(event.target.value)}>
              {!routingVersions.length ? <option value="">—</option> : null}
              {routingVersions.map(({ policy, version }) => <option key={version.public_id} value={version.public_id}>{policy.name} v{version.version}</option>)}
            </select>
          </label>
          <label className="block text-sm font-medium text-slate-700">
            {zh ? "Embedding 模型" : "Embedding model"}
            <input className="mt-1 h-10 w-full rounded-lg border border-slate-200 px-3 text-sm" value={modelRef} onChange={(event) => setModelRef(event.target.value)} />
          </label>
          <label className="block text-sm font-medium text-slate-700">
            {zh ? "相似度阈值" : "Similarity threshold"}
            <input type="number" min="0" max="1" step="0.01" className="mt-1 h-10 w-full rounded-lg border border-slate-200 px-3 text-sm" value={threshold} onChange={(event) => setThreshold(event.target.value)} />
          </label>
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <label className="min-w-72 flex-1 text-sm font-medium text-slate-700">
            {zh ? "来源 ROUTED 运行" : "Source ROUTED run"}
            <select className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm" value={routingRunId} onChange={(event) => setRoutingRunId(event.target.value)}>
              {!eligibleRuns.length ? <option value="">—</option> : null}
              {eligibleRuns.map((run) => <option key={run.public_id} value={run.public_id}>{run.public_id} · {run.bad_case_selected_count} Bad Cases</option>)}
            </select>
          </label>
          <Button onClick={() => void createPolicy()} disabled={actionId !== null || !name.trim()} icon={<GitFork className="h-4 w-4" />}>{zh ? "创建策略" : "Create policy"}</Button>
        </div>
        <div className="grid gap-3 xl:grid-cols-2">
          {policies.map((policy) => (
            <div key={policy.public_id} className="rounded-lg border border-slate-200 p-4">
              <div className="flex items-center justify-between gap-2">
                <div><div className="font-medium text-slate-800">{policy.name}</div><div className="font-mono text-xs text-slate-400">{policy.public_id}</div></div>
                <Button variant="secondary" onClick={() => void createVersion(policy)} disabled={!routingVersionId || actionId !== null}>{zh ? "新增版本" : "Add version"}</Button>
              </div>
              <div className="mt-3 space-y-2">
                {policy.versions.map((version) => (
                  <div key={version.public_id} className="rounded-md bg-slate-50 px-3 py-3">
                    <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-600">
                      <div className="flex flex-wrap items-center gap-2"><Badge tone="indigo">v{version.version}</Badge><span>{version.model_ref}</span><span>cos≥{version.similarity_threshold}</span><span>{version.dimensions}d</span></div>
                      <Button onClick={() => void runVersion(version)} disabled={!routingRunId || version.source_case_routing_policy_version_public_id !== routingVersionId || actionId !== null}>{actionId === version.public_id ? zh ? "聚类中…" : "Clustering…" : zh ? "运行真实聚类" : "Run clustering"}</Button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
        {runs.length ? (
          <div className="rounded-lg border border-slate-200 divide-y divide-slate-100">
            {runs.slice(0, 8).map((run) => (
              <div key={run.public_id} className="px-4 py-3 text-xs text-slate-600">
                <div className="flex flex-wrap items-center justify-between gap-2"><div className="flex flex-wrap items-center gap-2"><Badge tone={run.outcome === "CLUSTERED" ? "emerald" : "amber"}>{run.outcome}</Badge><span>{run.source_item_count} items</span><span>{run.cluster_count} clusters</span><span>{run.eligible_cluster_count} eligible</span></div><span>{formatTime(run.created_at, locale)}</span></div>
                <div className="mt-1 font-mono text-[11px] text-slate-400">{run.public_id} · {shortDigest(run.clustering_digest)}</div>
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </Card>
  );
}


function SemanticRegressionPanel({
  zh,
  locale,
  namespaceId,
  runs,
  policies,
  comparisons,
  onChanged,
  onNotice,
}: {
  zh: boolean;
  locale: string;
  namespaceId: number;
  runs: EvaluationSemanticClusteringRun[];
  policies: EvaluationSemanticRegressionPolicy[];
  comparisons: EvaluationSemanticRegressionComparison[];
  onChanged: () => Promise<unknown>;
  onNotice: (notice: { tone: "success" | "error"; text: string } | null) => void;
}) {
  const [name, setName] = useState("semantic-cluster-regression");
  const [minimumAgreement, setMinimumAgreement] = useState("0.9");
  const [maximumClusterChange, setMaximumClusterChange] = useState("0.5");
  const [maximumSimilarityDrop, setMaximumSimilarityDrop] = useState("0.05");
  const [maximumEligibleDrop, setMaximumEligibleDrop] = useState("0.25");
  const [baselineRunId, setBaselineRunId] = useState("");
  const [candidateRunId, setCandidateRunId] = useState("");
  const [actionId, setActionId] = useState<string | null>(null);

  const baselineRun = runs.find((run) => run.public_id === baselineRunId);
  const candidateRuns = useMemo(
    () =>
      baselineRun
        ? runs.filter(
            (run) =>
              run.public_id !== baselineRun.public_id &&
              run.source_case_routing_run_public_id ===
                baselineRun.source_case_routing_run_public_id,
          )
        : [],
    [baselineRun, runs],
  );

  useEffect(() => {
    if (runs.length && !runs.some((run) => run.public_id === baselineRunId)) {
      setBaselineRunId(
        runs.find((run) => run.outcome === "CLUSTERED")?.public_id ??
          runs[0].public_id,
      );
    }
  }, [baselineRunId, runs]);

  useEffect(() => {
    if (
      candidateRuns.length &&
      !candidateRuns.some((run) => run.public_id === candidateRunId)
    ) {
      setCandidateRunId(candidateRuns[0].public_id);
    } else if (!candidateRuns.length && candidateRunId) {
      setCandidateRunId("");
    }
  }, [candidateRunId, candidateRuns]);

  async function createPolicy() {
    if (!name.trim()) return;
    setActionId("policy");
    onNotice(null);
    try {
      await evalHubApi.createSemanticRegressionPolicy({
        namespace_id: namespaceId,
        name: name.trim(),
        description:
          "Offline pairwise assignment, cluster count, eligibility, and centroid similarity gate",
      });
      onNotice({
        tone: "success",
        text: zh
          ? "语义簇回归策略已创建；下一步冻结质量阈值。"
          : "Semantic cluster regression policy created; freeze quality thresholds next.",
      });
      await onChanged();
    } catch {
      onNotice({
        tone: "error",
        text: zh
          ? "语义簇回归策略创建失败。"
          : "Semantic cluster regression policy creation failed.",
      });
    } finally {
      setActionId(null);
    }
  }

  async function createVersion(policy: EvaluationSemanticRegressionPolicy) {
    const values = [
      Number(minimumAgreement),
      Number(maximumClusterChange),
      Number(maximumSimilarityDrop),
      Number(maximumEligibleDrop),
    ];
    if (values.some((value) => Number.isNaN(value) || value < 0 || value > 1)) {
      onNotice({
        tone: "error",
        text: zh ? "所有质量阈值都必须位于 0～1。" : "All quality thresholds must be between 0 and 1.",
      });
      return;
    }
    setActionId(policy.public_id);
    onNotice(null);
    try {
      await evalHubApi.createSemanticRegressionPolicyVersion(policy.public_id, {
        minimum_pairwise_assignment_agreement: values[0],
        maximum_cluster_count_change_ratio: values[1],
        maximum_mean_centroid_similarity_drop: values[2],
        maximum_eligible_cluster_ratio_drop: values[3],
      });
      onNotice({
        tone: "success",
        text: zh ? "不可变语义回归阈值已创建。" : "Immutable semantic regression thresholds created.",
      });
      await onChanged();
    } catch {
      onNotice({
        tone: "error",
        text: zh ? "语义回归版本创建失败；请检查重复配置。" : "Semantic regression version failed; check for duplicate configuration.",
      });
    } finally {
      setActionId(null);
    }
  }

  async function compare(version: EvaluationSemanticRegressionPolicyVersion) {
    if (!baselineRunId || !candidateRunId) return;
    setActionId(version.public_id);
    onNotice(null);
    try {
      const response = await evalHubApi.createSemanticRegressionComparison(
        {
          namespace_id: namespaceId,
          baseline_run_public_id: baselineRunId,
          candidate_run_public_id: candidateRunId,
          policy_version_public_id: version.public_id,
        },
        stableClientKey(
          `semantic-regression-${version.public_id}-${baselineRunId}`,
          candidateRunId,
        ),
      );
      onNotice({
        tone: response.data.outcome === "PASS" ? "success" : "error",
        text: zh
          ? `离线语义回归结论：${response.data.outcome}。`
          : `Offline semantic regression outcome: ${response.data.outcome}.`,
      });
      await onChanged();
    } catch {
      onNotice({
        tone: "error",
        text: zh
          ? "语义回归比较失败；基线与候选必须来自同一 Case Routing 运行及相同来源。"
          : "Semantic regression comparison failed; baseline and candidate must share one Case Routing run and source set.",
      });
    } finally {
      setActionId(null);
    }
  }

  return (
    <Card
      title={zh ? "语义簇质量与漂移门禁" : "Semantic cluster quality and drift gate"}
      description={
        zh
          ? "对同一批来源的两个不可变语义运行做离线比较；精确校验内容摘要，再评估成对归属一致率、簇数变化、合格簇比例和中心相似度。"
          : "Compare two immutable semantic runs over the same sources; verify exact content digests, then evaluate pairwise assignment, cluster count, eligible ratio, and centroid similarity."
      }
      padded
    >
      <div className="space-y-4">
        <div className="grid gap-3 lg:grid-cols-5">
          <label className="block text-sm font-medium text-slate-700">
            {zh ? "门禁策略名称" : "Gate policy name"}
            <input aria-label={zh ? "语义回归策略名称" : "Semantic regression policy name"} className="mt-1 h-10 w-full rounded-lg border border-slate-200 px-3 text-sm" value={name} onChange={(event) => setName(event.target.value)} />
          </label>
          <label className="block text-sm font-medium text-slate-700">
            {zh ? "最低归属一致率" : "Min assignment agreement"}
            <input aria-label={zh ? "最低成对归属一致率" : "Minimum pairwise assignment agreement"} type="number" min="0" max="1" step="0.01" className="mt-1 h-10 w-full rounded-lg border border-slate-200 px-3 text-sm" value={minimumAgreement} onChange={(event) => setMinimumAgreement(event.target.value)} />
          </label>
          <label className="block text-sm font-medium text-slate-700">
            {zh ? "最大簇数变化" : "Max cluster change"}
            <input aria-label={zh ? "最大簇数变化率" : "Maximum cluster count change ratio"} type="number" min="0" max="1" step="0.01" className="mt-1 h-10 w-full rounded-lg border border-slate-200 px-3 text-sm" value={maximumClusterChange} onChange={(event) => setMaximumClusterChange(event.target.value)} />
          </label>
          <label className="block text-sm font-medium text-slate-700">
            {zh ? "最大中心相似度下降" : "Max centroid drop"}
            <input aria-label={zh ? "最大中心相似度下降" : "Maximum centroid similarity drop"} type="number" min="0" max="1" step="0.01" className="mt-1 h-10 w-full rounded-lg border border-slate-200 px-3 text-sm" value={maximumSimilarityDrop} onChange={(event) => setMaximumSimilarityDrop(event.target.value)} />
          </label>
          <label className="block text-sm font-medium text-slate-700">
            {zh ? "最大合格簇比例下降" : "Max eligible-ratio drop"}
            <input aria-label={zh ? "最大合格簇比例下降" : "Maximum eligible cluster ratio drop"} type="number" min="0" max="1" step="0.01" className="mt-1 h-10 w-full rounded-lg border border-slate-200 px-3 text-sm" value={maximumEligibleDrop} onChange={(event) => setMaximumEligibleDrop(event.target.value)} />
          </label>
        </div>
        <div className="grid gap-3 lg:grid-cols-[1fr_1fr_auto] lg:items-end">
          <label className="text-sm font-medium text-slate-700">
            {zh ? "基线语义运行" : "Baseline semantic run"}
            <select aria-label={zh ? "语义回归基线运行" : "Semantic regression baseline run"} className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm" value={baselineRunId} onChange={(event) => setBaselineRunId(event.target.value)}>
              {!runs.length ? <option value="">—</option> : null}
              {runs.map((run) => <option key={run.public_id} value={run.public_id}>{run.public_id} · {run.outcome} · {run.cluster_count} clusters</option>)}
            </select>
          </label>
          <label className="text-sm font-medium text-slate-700">
            {zh ? "候选语义运行" : "Candidate semantic run"}
            <select aria-label={zh ? "语义回归候选运行" : "Semantic regression candidate run"} className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm" value={candidateRunId} onChange={(event) => setCandidateRunId(event.target.value)}>
              {!candidateRuns.length ? <option value="">—</option> : null}
              {candidateRuns.map((run) => <option key={run.public_id} value={run.public_id}>{run.public_id} · {run.outcome} · {run.cluster_count} clusters</option>)}
            </select>
          </label>
          <Button onClick={() => void createPolicy()} disabled={actionId !== null || !name.trim()} icon={<GitCompareArrows className="h-4 w-4" />}>{zh ? "创建门禁策略" : "Create gate policy"}</Button>
        </div>
        <div className="grid gap-3 xl:grid-cols-2">
          {policies.map((policy) => (
            <div key={policy.public_id} className="rounded-lg border border-slate-200 p-4">
              <div className="flex items-center justify-between gap-2">
                <div><div className="font-medium text-slate-800">{policy.name}</div><div className="font-mono text-xs text-slate-400">{policy.public_id}</div></div>
                <Button variant="secondary" onClick={() => void createVersion(policy)} disabled={actionId !== null}>{zh ? "冻结当前阈值" : "Freeze thresholds"}</Button>
              </div>
              <div className="mt-3 space-y-2">
                {policy.versions.map((version) => (
                  <div key={version.public_id} className="rounded-md bg-slate-50 px-3 py-3">
                    <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-600">
                      <div className="flex flex-wrap items-center gap-2"><Badge tone="violet">v{version.version}</Badge><span>agreement≥{version.minimum_pairwise_assignment_agreement}</span><span>clusters≤{version.maximum_cluster_count_change_ratio}</span><span>centroid drop≤{version.maximum_mean_centroid_similarity_drop}</span><span>eligible drop≤{version.maximum_eligible_cluster_ratio_drop}</span></div>
                      <Button onClick={() => void compare(version)} disabled={!baselineRunId || !candidateRunId || actionId !== null}>{actionId === version.public_id ? zh ? "比较中…" : "Comparing…" : zh ? "执行离线门禁" : "Run offline gate"}</Button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
        {comparisons.length ? (
          <div className="rounded-lg border border-slate-200 divide-y divide-slate-100">
            {comparisons.slice(0, 8).map((comparison) => (
              <div key={comparison.public_id} className="px-4 py-3 text-xs text-slate-600">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-2"><Badge tone={comparison.outcome === "PASS" ? "emerald" : comparison.outcome === "DRIFTED" ? "rose" : "amber"}>{comparison.outcome}</Badge><span>agreement {comparison.pairwise_assignment_agreement ?? "—"}</span><span>cluster Δ {comparison.cluster_count_change_ratio}</span><span>eligible ↓ {comparison.eligible_cluster_ratio_drop}</span><span>centroid ↓ {comparison.mean_centroid_similarity_drop}</span></div>
                  <span>{formatTime(comparison.created_at, locale)}</span>
                </div>
                <div className="mt-1">{comparison.reason_codes.join(", ")}</div>
                <div className="mt-1 font-mono text-[11px] text-slate-400">{comparison.public_id} · baseline:{comparison.baseline_run_public_id} · candidate:{comparison.candidate_run_public_id} · evidence:{shortDigest(comparison.reproducibility_digest)}</div>
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </Card>
  );
}


function SemanticMonitorPanel({
  zh,
  locale,
  namespaceId,
  semanticPolicies,
  semanticRuns,
  regressionPolicies,
  monitors,
  monitorRuns,
  alerts,
  onChanged,
  onNotice,
}: {
  zh: boolean;
  locale: string;
  namespaceId: number;
  semanticPolicies: EvaluationSemanticClusteringPolicy[];
  semanticRuns: EvaluationSemanticClusteringRun[];
  regressionPolicies: EvaluationSemanticRegressionPolicy[];
  monitors: EvaluationSemanticMonitor[];
  monitorRuns: EvaluationSemanticMonitorRun[];
  alerts: EvaluationSemanticMonitorAlert[];
  onChanged: () => Promise<unknown>;
  onNotice: (notice: { tone: "success" | "error"; text: string } | null) => void;
}) {
  const [name, setName] = useState("semantic-drift-watch");
  const [baselineRunId, setBaselineRunId] = useState("");
  const [candidateVersionId, setCandidateVersionId] = useState("");
  const [regressionVersionId, setRegressionVersionId] = useState("");
  const [intervalMinutes, setIntervalMinutes] = useState("60");
  const [acknowledgementNotes, setAcknowledgementNotes] = useState<Record<string, string>>({});
  const [actionId, setActionId] = useState<string | null>(null);

  const semanticVersions = useMemo(
    () => semanticPolicies.flatMap((policy) => policy.versions),
    [semanticPolicies],
  );
  const regressionVersions = useMemo(
    () => regressionPolicies.flatMap((policy) => policy.versions),
    [regressionPolicies],
  );
  const baselineRun = semanticRuns.find((run) => run.public_id === baselineRunId);
  const baselineVersion = semanticVersions.find(
    (version) => version.public_id === baselineRun?.policy_version_public_id,
  );
  const candidateVersions = useMemo(
    () =>
      baselineVersion
        ? semanticVersions.filter(
            (version) =>
              version.source_case_routing_policy_version_public_id ===
              baselineVersion.source_case_routing_policy_version_public_id,
          )
        : [],
    [baselineVersion, semanticVersions],
  );

  useEffect(() => {
    const clustered = semanticRuns.filter((run) => run.outcome === "CLUSTERED");
    if (clustered.length && !clustered.some((run) => run.public_id === baselineRunId)) {
      setBaselineRunId(clustered[0].public_id);
    }
  }, [baselineRunId, semanticRuns]);

  useEffect(() => {
    if (
      candidateVersions.length &&
      !candidateVersions.some((version) => version.public_id === candidateVersionId)
    ) {
      setCandidateVersionId(candidateVersions[0].public_id);
    }
  }, [candidateVersionId, candidateVersions]);

  useEffect(() => {
    if (
      regressionVersions.length &&
      !regressionVersions.some((version) => version.public_id === regressionVersionId)
    ) {
      setRegressionVersionId(regressionVersions[0].public_id);
    }
  }, [regressionVersionId, regressionVersions]);

  async function createMonitor() {
    const minutes = Number(intervalMinutes);
    if (
      !name.trim() ||
      !baselineRunId ||
      !candidateVersionId ||
      !regressionVersionId ||
      !Number.isFinite(minutes) ||
      minutes < 1 ||
      minutes > 43200
    ) {
      onNotice({
        tone: "error",
        text: zh
          ? "请选择精确基线与两个策略版本，周期须为 1～43200 分钟。"
          : "Select exact baseline and policy versions; interval must be 1–43200 minutes.",
      });
      return;
    }
    setActionId("create");
    onNotice(null);
    try {
      await evalHubApi.createSemanticMonitor({
        namespace_id: namespaceId,
        name: name.trim(),
        description: "Exact-source semantic drift monitor with in-app alerts.",
        baseline_run_public_id: baselineRunId,
        candidate_policy_version_public_id: candidateVersionId,
        regression_policy_version_public_id: regressionVersionId,
        interval_seconds: Math.round(minutes * 60),
      });
      onNotice({
        tone: "success",
        text: zh
          ? "定时漂移监控已创建；Beat 会持久化到期任务。"
          : "Scheduled drift monitor created; Beat will persist due runs.",
      });
      await onChanged();
    } catch {
      onNotice({
        tone: "error",
        text: zh
          ? "监控创建失败；请检查精确针脚或重复配置。"
          : "Monitor creation failed; check exact pins or duplicate configuration.",
      });
    } finally {
      setActionId(null);
    }
  }

  async function toggleMonitor(monitor: EvaluationSemanticMonitor) {
    setActionId(`toggle:${monitor.public_id}`);
    onNotice(null);
    try {
      if (monitor.status === "ACTIVE") {
        await evalHubApi.pauseSemanticMonitor(monitor.public_id);
      } else {
        await evalHubApi.resumeSemanticMonitor(monitor.public_id);
      }
      await onChanged();
    } catch {
      onNotice({ tone: "error", text: zh ? "监控状态更新失败。" : "Monitor state update failed." });
    } finally {
      setActionId(null);
    }
  }

  async function runNow(monitor: EvaluationSemanticMonitor) {
    setActionId(`run:${monitor.public_id}`);
    onNotice(null);
    try {
      await evalHubApi.runSemanticMonitorNow(
        monitor.public_id,
        `semantic-monitor-now-${Date.now()}`,
      );
      onNotice({
        tone: "success",
        text: zh ? "监控执行已持久化排队。" : "Monitor run was durably queued.",
      });
      await onChanged();
    } catch {
      onNotice({ tone: "error", text: zh ? "监控排队失败。" : "Monitor queueing failed." });
    } finally {
      setActionId(null);
    }
  }

  async function acknowledge(alert: EvaluationSemanticMonitorAlert) {
    setActionId(`ack:${alert.public_id}`);
    onNotice(null);
    try {
      await evalHubApi.acknowledgeSemanticMonitorAlert(
        alert.public_id,
        acknowledgementNotes[alert.public_id] ?? null,
      );
      onNotice({ tone: "success", text: zh ? "告警已最终确认。" : "Alert acknowledged finally." });
      await onChanged();
    } catch {
      onNotice({ tone: "error", text: zh ? "告警确认失败或已被确认。" : "Alert acknowledgement failed or is already final." });
    } finally {
      setActionId(null);
    }
  }

  return (
    <Card
      title={zh ? "定时语义漂移监控" : "Scheduled semantic drift monitoring"}
      description={
        zh
          ? "固定一个 CLUSTERED 基线、候选聚类版本与回归阈值；周期重跑相同 Case Routing 来源。PASS 静默，DRIFTED/INCONCLUSIVE/最终失败形成站内告警，不会自动修改 Agent。"
          : "Pin a CLUSTERED baseline, candidate clustering version, and regression thresholds; rerun the same Case Routing source. PASS stays silent, while drift, inconclusive results, and final failures open in-app alerts without mutating Agents."
      }
      padded
    >
      <div className="space-y-4">
        <div className="grid gap-3 xl:grid-cols-5">
          <label className="text-sm font-medium text-slate-700">
            {zh ? "监控名称" : "Monitor name"}
            <input aria-label={zh ? "语义监控名称" : "Semantic monitor name"} className="mt-1 h-10 w-full rounded-lg border border-slate-200 px-3 text-sm" value={name} onChange={(event) => setName(event.target.value)} />
          </label>
          <label className="text-sm font-medium text-slate-700">
            {zh ? "CLUSTERED 基线" : "CLUSTERED baseline"}
            <select aria-label={zh ? "语义监控基线运行" : "Semantic monitor baseline run"} className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm" value={baselineRunId} onChange={(event) => setBaselineRunId(event.target.value)}>
              {!semanticRuns.some((run) => run.outcome === "CLUSTERED") ? <option value="">—</option> : null}
              {semanticRuns.filter((run) => run.outcome === "CLUSTERED").map((run) => <option key={run.public_id} value={run.public_id}>{run.public_id} · {run.cluster_count} clusters</option>)}
            </select>
          </label>
          <label className="text-sm font-medium text-slate-700">
            {zh ? "候选聚类版本" : "Candidate clustering version"}
            <select aria-label={zh ? "语义监控候选版本" : "Semantic monitor candidate version"} className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm" value={candidateVersionId} onChange={(event) => setCandidateVersionId(event.target.value)}>
              {!candidateVersions.length ? <option value="">—</option> : null}
              {candidateVersions.map((version) => <option key={version.public_id} value={version.public_id}>{version.public_id} · {version.model_ref}</option>)}
            </select>
          </label>
          <label className="text-sm font-medium text-slate-700">
            {zh ? "回归阈值版本" : "Regression thresholds"}
            <select aria-label={zh ? "语义监控回归版本" : "Semantic monitor regression version"} className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm" value={regressionVersionId} onChange={(event) => setRegressionVersionId(event.target.value)}>
              {!regressionVersions.length ? <option value="">—</option> : null}
              {regressionVersions.map((version) => <option key={version.public_id} value={version.public_id}>{version.public_id}</option>)}
            </select>
          </label>
          <label className="text-sm font-medium text-slate-700">
            {zh ? "周期（分钟）" : "Interval (minutes)"}
            <div className="mt-1 flex gap-2">
              <input aria-label={zh ? "语义监控周期分钟" : "Semantic monitor interval minutes"} type="number" min="1" max="43200" className="h-10 min-w-0 flex-1 rounded-lg border border-slate-200 px-3 text-sm" value={intervalMinutes} onChange={(event) => setIntervalMinutes(event.target.value)} />
              <Button onClick={() => void createMonitor()} disabled={actionId !== null || !baselineRunId || !candidateVersionId || !regressionVersionId} icon={<Clock3 className="h-4 w-4" />}>{zh ? "创建" : "Create"}</Button>
            </div>
          </label>
        </div>

        {monitors.length ? (
          <div className="grid gap-3 xl:grid-cols-2">
            {monitors.map((monitor) => (
              <div key={monitor.public_id} className="rounded-lg border border-slate-200 p-4 text-xs text-slate-600">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div><div className="text-sm font-medium text-slate-800">{monitor.name}</div><div className="font-mono text-[11px] text-slate-400">{monitor.public_id}</div></div>
                  <div className="flex items-center gap-2"><Badge tone={monitor.status === "ACTIVE" ? "emerald" : "amber"}>{monitor.status}</Badge><Button variant="secondary" onClick={() => void toggleMonitor(monitor)} disabled={actionId !== null}>{monitor.status === "ACTIVE" ? zh ? "暂停" : "Pause" : zh ? "恢复" : "Resume"}</Button><Button onClick={() => void runNow(monitor)} disabled={actionId !== null}>{zh ? "立即运行" : "Run now"}</Button></div>
                </div>
                <div className="mt-2">{zh ? "下次运行" : "Next run"}: {formatTime(monitor.next_run_at, locale)} · {monitor.interval_seconds / 60} min</div>
                <div className="mt-1 font-mono text-[11px] text-slate-400">baseline:{monitor.baseline_run_public_id} · candidate:{monitor.candidate_policy_version_public_id} · gate:{monitor.regression_policy_version_public_id}</div>
              </div>
            ))}
          </div>
        ) : <EmptyState text={zh ? "尚未创建定时语义漂移监控。" : "No scheduled semantic drift monitor yet."} />}

        {alerts.length ? (
          <div className="rounded-lg border border-slate-200 divide-y divide-slate-100">
            {alerts.slice(0, 8).map((alert) => (
              <div key={alert.public_id} className="px-4 py-3 text-xs text-slate-600">
                <div className="flex flex-wrap items-center justify-between gap-2"><div className="flex items-center gap-2"><Badge tone={alert.severity === "CRITICAL" ? "rose" : "amber"}>{alert.severity}</Badge><Badge tone={alert.status === "OPEN" ? "rose" : "neutral"}>{alert.status}</Badge><span>{alert.reason_codes.join(", ") || alert.error_code}</span></div><span>{formatTime(alert.created_at, locale)}</span></div>
                <div className="mt-1 font-mono text-[11px] text-slate-400">{alert.public_id} · run:{alert.monitor_run_public_id}</div>
                {alert.status === "OPEN" ? <div className="mt-2 flex gap-2"><input aria-label={`${zh ? "告警确认备注" : "Alert acknowledgement note"} ${alert.public_id}`} className="h-9 min-w-0 flex-1 rounded-md border border-slate-200 px-3 text-sm" value={acknowledgementNotes[alert.public_id] ?? ""} onChange={(event) => setAcknowledgementNotes((current) => ({ ...current, [alert.public_id]: event.target.value }))} placeholder={zh ? "可选备注（不会进入 Audit/Outbox）" : "Optional note (excluded from Audit/Outbox)"} /><Button variant="secondary" onClick={() => void acknowledge(alert)} disabled={actionId !== null}>{zh ? "确认" : "Acknowledge"}</Button></div> : alert.acknowledgement_note ? <div className="mt-2 text-slate-500">{alert.acknowledgement_note}</div> : null}
              </div>
            ))}
          </div>
        ) : null}

        {monitorRuns.length ? <div className="flex flex-wrap gap-2">{monitorRuns.slice(0, 12).map((run) => <MonoPill key={run.public_id}>{run.status} · {run.outcome ?? run.error_code ?? run.public_id}</MonoPill>)}</div> : null}
      </div>
    </Card>
  );
}


function ExperienceAssetsPanel({
  zh,
  locale,
  extractionRuns,
  assets,
  candidateId,
  assetName,
  assetDescription,
  selectedAssetId,
  versionBody,
  versionApplicability,
  versionChangeSummary,
  activationNote,
  activationComments,
  actionId,
  onChangeCandidate,
  onChangeAssetName,
  onChangeAssetDescription,
  onChangeSelectedAsset,
  onChangeVersionBody,
  onChangeVersionApplicability,
  onChangeVersionChangeSummary,
  onChangeActivationNote,
  onChangeActivationComment,
  onCreateAsset,
  onCreateVersion,
  onRequestActivation,
  onReviewActivation,
}: {
  zh: boolean;
  locale: string;
  extractionRuns: EvaluationExperienceExtractionRun[];
  assets: EvaluationExperienceAsset[];
  candidateId: string;
  assetName: string;
  assetDescription: string;
  selectedAssetId: string;
  versionBody: string;
  versionApplicability: string;
  versionChangeSummary: string;
  activationNote: string;
  activationComments: Record<string, string>;
  actionId: string | null;
  onChangeCandidate: (value: string) => void;
  onChangeAssetName: (value: string) => void;
  onChangeAssetDescription: (value: string) => void;
  onChangeSelectedAsset: (value: string) => void;
  onChangeVersionBody: (value: string) => void;
  onChangeVersionApplicability: (value: string) => void;
  onChangeVersionChangeSummary: (value: string) => void;
  onChangeActivationNote: (value: string) => void;
  onChangeActivationComment: (publicId: string, comment: string) => void;
  onCreateAsset: () => void;
  onCreateVersion: () => void;
  onRequestActivation: (version: EvaluationExperienceAssetVersion) => void;
  onReviewActivation: (
    version: EvaluationExperienceAssetVersion,
    decision: "APPROVED" | "REJECTED",
  ) => void;
}) {
  const claimedCandidateIds = new Set(
    assets.map((asset) => asset.source_candidate_public_id),
  );
  const approvedCandidates = extractionRuns
    .flatMap((run) => run.candidates)
    .filter(
      (candidate) =>
        candidate.status === "APPROVED" &&
        !claimedCandidateIds.has(candidate.public_id),
    );

  const versionTone: Record<
    EvaluationExperienceAssetVersion["status"],
    Tone
  > = {
    DRAFT: "neutral",
    PENDING_ACTIVATION: "amber",
    ACTIVE: "emerald",
    REJECTED: "rose",
    RETIRED: "neutral",
  };

  return (
    <Card
      title={zh ? "Experience 资产与独立激活" : "Experience assets and independent activation"}
      description={
        zh
          ? "已批准候选 → 人工编写不可变版本 → 第二人审批激活。激活不自动下发运行时。"
          : "Approved candidate → immutable authored version → four-eyes activation. Activation does not push to runtimes."
      }
      padded
    >
      <div className="space-y-5">
        <div className="rounded-lg border border-indigo-100 bg-indigo-50 px-4 py-3 text-sm text-indigo-800">
          {zh
            ? "正文仅保存在 DuckDock；Audit 与 Outbox 只记录 digest。作者和激活请求人都不能担任最终审批人，Langfuse 与 Hermes 不接收任何自动写入。"
            : "Content stays in DuckDock; Audit and Outbox receive digests only. Authors/requesters cannot approve, and no automatic write reaches Langfuse or Hermes."}
        </div>

        <div className="grid gap-3 xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)_minmax(0,1fr)_auto]">
          <label className="block text-sm font-medium text-slate-700">
            {zh ? "已批准且未认领候选" : "Approved unclaimed candidate"}
            <select
              aria-label={zh ? "Experience 来源候选" : "Experience source candidate"}
              className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm"
              value={candidateId}
              onChange={(event) => onChangeCandidate(event.target.value)}
            >
              {!approvedCandidates.length ? <option value="">—</option> : null}
              {approvedCandidates.map((candidate) => (
                <option key={candidate.public_id} value={candidate.public_id}>
                  {candidate.category} · {candidate.public_id}
                </option>
              ))}
            </select>
          </label>
          <label className="block text-sm font-medium text-slate-700">
            {zh ? "资产名称" : "Asset name"}
            <input
              aria-label={zh ? "Experience 资产名称" : "Experience asset name"}
              className="mt-1 h-10 w-full rounded-lg border border-slate-200 px-3 text-sm"
              value={assetName}
              onChange={(event) => onChangeAssetName(event.target.value)}
            />
          </label>
          <label className="block text-sm font-medium text-slate-700">
            {zh ? "说明" : "Description"}
            <input
              aria-label={zh ? "Experience 资产说明" : "Experience asset description"}
              className="mt-1 h-10 w-full rounded-lg border border-slate-200 px-3 text-sm"
              value={assetDescription}
              onChange={(event) => onChangeAssetDescription(event.target.value)}
            />
          </label>
          <div className="flex items-end">
            <Button
              onClick={onCreateAsset}
              disabled={!candidateId || !assetName.trim() || actionId === candidateId}
            >
              {actionId === candidateId
                ? zh ? "创建中…" : "Creating…"
                : zh ? "创建资产" : "Create asset"}
            </Button>
          </div>
        </div>

        <div className="rounded-lg border border-slate-200 p-4">
          <div className="grid gap-3 xl:grid-cols-2">
            <label className="block text-sm font-medium text-slate-700">
              {zh ? "目标 Experience 资产" : "Target Experience asset"}
              <select
                aria-label={zh ? "Experience 版本目标资产" : "Experience version target asset"}
                className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm"
                value={selectedAssetId}
                onChange={(event) => onChangeSelectedAsset(event.target.value)}
              >
                {!assets.length ? <option value="">—</option> : null}
                {assets.map((asset) => (
                  <option key={asset.public_id} value={asset.public_id}>
                    {asset.name} · {asset.public_id}
                  </option>
                ))}
              </select>
            </label>
            <label className="block text-sm font-medium text-slate-700">
              {zh ? "变更摘要" : "Change summary"}
              <input
                aria-label={zh ? "Experience 版本变更摘要" : "Experience version change summary"}
                className="mt-1 h-10 w-full rounded-lg border border-slate-200 px-3 text-sm"
                value={versionChangeSummary}
                onChange={(event) => onChangeVersionChangeSummary(event.target.value)}
              />
            </label>
            <label className="block text-sm font-medium text-slate-700">
              {zh ? "经验正文" : "Experience body"}
              <textarea
                aria-label={zh ? "Experience 经验正文" : "Experience body"}
                className="mt-1 min-h-24 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm"
                value={versionBody}
                onChange={(event) => onChangeVersionBody(event.target.value)}
              />
            </label>
            <label className="block text-sm font-medium text-slate-700">
              {zh ? "适用边界" : "Applicability"}
              <textarea
                aria-label={zh ? "Experience 适用边界" : "Experience applicability"}
                className="mt-1 min-h-24 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm"
                value={versionApplicability}
                onChange={(event) => onChangeVersionApplicability(event.target.value)}
              />
            </label>
          </div>
          <div className="mt-3 flex justify-end">
            <Button
              onClick={onCreateVersion}
              disabled={
                !selectedAssetId ||
                versionBody.trim().length < 10 ||
                versionApplicability.trim().length < 5 ||
                actionId === selectedAssetId
              }
            >
              {actionId === selectedAssetId
                ? zh ? "保存中…" : "Saving…"
                : zh ? "创建不可变草稿" : "Create immutable draft"}
            </Button>
          </div>
        </div>

        <label className="block text-sm font-medium text-slate-700">
          {zh ? "激活申请说明（应用于下一次提交）" : "Activation note (next request)"}
          <input
            aria-label={zh ? "Experience 激活申请说明" : "Experience activation request note"}
            className="mt-1 h-10 w-full rounded-lg border border-slate-200 px-3 text-sm"
            value={activationNote}
            onChange={(event) => onChangeActivationNote(event.target.value)}
          />
        </label>

        <div className="space-y-3">
          {assets.map((asset) => (
            <div key={asset.public_id} className="rounded-lg border border-slate-200 p-4">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                  <div className="font-medium text-slate-800">{asset.name}</div>
                  <div className="mt-1 text-xs text-slate-500">
                    {asset.description || (zh ? "无说明" : "No description")}
                  </div>
                </div>
                <div className="text-right font-mono text-[11px] text-slate-400">
                  <div>{asset.public_id}</div>
                  <div>candidate:{asset.source_candidate_public_id}</div>
                </div>
              </div>
              <div className="mt-3 space-y-3">
                {asset.versions.map((version) => {
                  const activationRequest = version.activation_request;
                  const review = activationRequest?.review;
                  return (
                    <div key={version.public_id} className="rounded-md bg-slate-50 p-3">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="flex flex-wrap items-center gap-2 text-xs text-slate-600">
                          <Badge tone={versionTone[version.status]}>{version.status}</Badge>
                          <span>v{version.version}</span>
                          <span>{formatTime(version.created_at, locale)}</span>
                          <span className="font-mono">content:{shortDigest(version.content_digest)}</span>
                        </div>
                        <span className="font-mono text-[11px] text-slate-400">
                          {version.public_id}
                        </span>
                      </div>
                      <div className="mt-2 whitespace-pre-wrap text-sm text-slate-700">
                        {version.body}
                      </div>
                      <div className="mt-2 text-xs text-slate-500">
                        {zh ? "适用边界" : "Applicability"}: {version.applicability}
                      </div>
                      {version.status === "DRAFT" ? (
                        <div className="mt-3 flex justify-end">
                          <Button
                            variant="secondary"
                            onClick={() => onRequestActivation(version)}
                            disabled={actionId === version.public_id}
                          >
                            {actionId === version.public_id
                              ? zh ? "提交中…" : "Submitting…"
                              : zh ? "申请激活" : "Request activation"}
                          </Button>
                        </div>
                      ) : null}
                      {version.status === "PENDING_ACTIVATION" && activationRequest ? (
                        <div className="mt-3 grid gap-2 lg:grid-cols-[minmax(0,1fr)_auto_auto]">
                          <input
                            aria-label={`${zh ? "Experience 激活审批意见" : "Experience activation review comment"} ${activationRequest.public_id}`}
                            className="h-9 rounded-lg border border-slate-200 px-3 text-sm"
                            placeholder={
                              zh
                                ? "需由另一位成员审批；拒绝时至少 5 个字符"
                                : "Another member must review; 5 characters to reject"
                            }
                            value={activationComments[activationRequest.public_id] ?? ""}
                            onChange={(event) =>
                              onChangeActivationComment(
                                activationRequest.public_id,
                                event.target.value,
                              )
                            }
                          />
                          <Button
                            onClick={() => onReviewActivation(version, "APPROVED")}
                            disabled={actionId === activationRequest.public_id}
                            icon={<CheckCircle2 className="h-4 w-4" />}
                          >
                            {zh ? "批准激活" : "Approve"}
                          </Button>
                          <Button
                            variant="secondary"
                            danger
                            onClick={() => onReviewActivation(version, "REJECTED")}
                            disabled={actionId === activationRequest.public_id}
                            icon={<XCircle className="h-4 w-4" />}
                          >
                            {zh ? "拒绝" : "Reject"}
                          </Button>
                        </div>
                      ) : null}
                      {review ? (
                        <div className="mt-2 text-xs text-slate-500">
                          {zh ? "最终激活审批" : "Final activation review"}: {review.decision}
                          {review.comment ? ` · ${review.comment}` : ""}
                        </div>
                      ) : null}
                    </div>
                  );
                })}
                {!asset.versions.length ? (
                  <div className="text-xs text-amber-700">
                    {zh ? "尚无版本草稿。" : "No version draft yet."}
                  </div>
                ) : null}
              </div>
            </div>
          ))}
          {!assets.length ? (
            <EmptyState
              text={zh ? "还没有 Experience 资产。" : "No Experience assets yet."}
            />
          ) : null}
        </div>
      </div>
    </Card>
  );
}


function TraceDatasetPanel({
  zh,
  locale,
  datasets,
  materializations,
  curationBatches,
  samplingPolicies,
  samplingRuns,
  annotationProviderQueues,
  annotationQueueBindings,
  annotationDispatches,
  promotionPolicies,
  promotionRuns,
  caseRoutingPolicies,
  caseRoutingRuns,
  semanticClusteringPolicies,
  semanticClusteringRuns,
  failureTaxonomyPolicies,
  experienceExtractionRuns,
  selectedDatasetId,
  traceId,
  observationId,
  materializing,
  candidates,
  selectedCandidateRefs,
  candidateName,
  candidateEnvironment,
  candidateType,
  candidateWindowHours,
  candidateRootOnly,
  candidateLoading,
  curationSubmitting,
  curationActionId,
  curationComments,
  samplingPolicyName,
  samplingSampleSize,
  samplingMinimumSize,
  samplingSaving,
  samplingActionId,
  annotationProviderQueueRef,
  annotationBindingId,
  annotationCurationBatchId,
  annotationSaving,
  annotationActionId,
  promotionPolicyName,
  promotionScoreConfigId,
  promotionScoreDataType,
  promotionMinimumScore,
  promotionAcceptedValues,
  promotionDiversityDimension,
  promotionMinimumBuckets,
  promotionDispatchId,
  promotionSaving,
  promotionActionId,
  caseRoutingPolicyName,
  caseRoutingSourcePromotionVersionId,
  caseRoutingGoldenTargetSize,
  caseRoutingGoldenMinimumSize,
  caseRoutingBadCaseTargetSize,
  caseRoutingBadCaseMinimumSize,
  caseRoutingPromotionRunIds,
  caseRoutingGoldenDatasetId,
  caseRoutingBadCaseDatasetId,
  caseRoutingSaving,
  caseRoutingActionId,
  failureTaxonomyPolicyName,
  failureTaxonomySourceRoutingVersionId,
  failureTaxonomySemanticVersionId,
  failureTaxonomyMinOccurrences,
  failureTaxonomyMinSourceRuns,
  failureTaxonomyIncludeIsolated,
  failureTaxonomyMaxCandidates,
  experienceSourceRoutingRunId,
  experienceSemanticRunId,
  failureTaxonomySaving,
  failureTaxonomyActionId,
  experienceReviewComments,
  onSelectDataset,
  onChangeTrace,
  onChangeObservation,
  onSubmit,
  onChangeCandidateName,
  onChangeCandidateEnvironment,
  onChangeCandidateType,
  onChangeCandidateWindowHours,
  onChangeCandidateRootOnly,
  onSearchCandidates,
  onToggleCandidate,
  onSubmitCuration,
  onChangeSamplingPolicyName,
  onChangeSamplingSampleSize,
  onChangeSamplingMinimumSize,
  onCreateSamplingPolicy,
  onCreateSamplingVersion,
  onRunSamplingVersion,
  onChangeAnnotationProviderQueue,
  onChangeAnnotationBinding,
  onChangeAnnotationCurationBatch,
  onBindAnnotationQueue,
  onDispatchAnnotationBatch,
  onRetryAnnotationDispatch,
  onReconcileAnnotationDispatch,
  onChangePromotionPolicyName,
  onChangePromotionScoreConfig,
  onChangePromotionScoreDataType,
  onChangePromotionMinimumScore,
  onChangePromotionAcceptedValues,
  onChangePromotionDiversityDimension,
  onChangePromotionMinimumBuckets,
  onChangePromotionDispatch,
  onCreatePromotionPolicy,
  onCreatePromotionVersion,
  onRunPromotionVersion,
  onChangeCaseRoutingPolicyName,
  onChangeCaseRoutingSourcePromotionVersion,
  onChangeCaseRoutingGoldenTargetSize,
  onChangeCaseRoutingGoldenMinimumSize,
  onChangeCaseRoutingBadCaseTargetSize,
  onChangeCaseRoutingBadCaseMinimumSize,
  onChangeCaseRoutingPromotionRuns,
  onChangeCaseRoutingGoldenDataset,
  onChangeCaseRoutingBadCaseDataset,
  onCreateCaseRoutingPolicy,
  onCreateCaseRoutingVersion,
  onRunCaseRoutingVersion,
  onChangeFailureTaxonomyPolicyName,
  onChangeFailureTaxonomySourceRoutingVersion,
  onChangeFailureTaxonomySemanticVersion,
  onChangeFailureTaxonomyMinOccurrences,
  onChangeFailureTaxonomyMinSourceRuns,
  onChangeFailureTaxonomyIncludeIsolated,
  onChangeFailureTaxonomyMaxCandidates,
  onChangeExperienceSourceRoutingRun,
  onChangeExperienceSemanticRun,
  onCreateFailureTaxonomyPolicy,
  onCreateFailureTaxonomyVersion,
  onRunFailureTaxonomyVersion,
  onChangeExperienceReviewComment,
  onReviewExperienceCandidate,
  onChangeCurationComment,
  onReviewCuration,
  onMaterializeCuration,
}: {
  zh: boolean;
  locale: string;
  datasets: EvaluationDataset[];
  materializations: EvaluationDatasetMaterialization[];
  curationBatches: EvaluationDatasetCurationBatch[];
  samplingPolicies: EvaluationSamplingPolicy[];
  samplingRuns: EvaluationSamplingRun[];
  annotationProviderQueues: EvaluationProviderAnnotationQueue[];
  annotationQueueBindings: EvaluationAnnotationQueueBinding[];
  annotationDispatches: EvaluationAnnotationDispatch[];
  promotionPolicies: EvaluationPromotionPolicy[];
  promotionRuns: EvaluationPromotionRun[];
  caseRoutingPolicies: EvaluationCaseRoutingPolicy[];
  caseRoutingRuns: EvaluationCaseRoutingRun[];
  semanticClusteringPolicies: EvaluationSemanticClusteringPolicy[];
  semanticClusteringRuns: EvaluationSemanticClusteringRun[];
  failureTaxonomyPolicies: EvaluationFailureTaxonomyPolicy[];
  experienceExtractionRuns: EvaluationExperienceExtractionRun[];
  selectedDatasetId: string;
  traceId: string;
  observationId: string;
  materializing: boolean;
  candidates: TraceDatasetCandidate[];
  selectedCandidateRefs: Set<string>;
  candidateName: string;
  candidateEnvironment: string;
  candidateType: string;
  candidateWindowHours: string;
  candidateRootOnly: boolean;
  candidateLoading: boolean;
  curationSubmitting: boolean;
  curationActionId: string | null;
  curationComments: Record<string, string>;
  samplingPolicyName: string;
  samplingSampleSize: string;
  samplingMinimumSize: string;
  samplingSaving: boolean;
  samplingActionId: string | null;
  annotationProviderQueueRef: string;
  annotationBindingId: string;
  annotationCurationBatchId: string;
  annotationSaving: boolean;
  annotationActionId: string | null;
  promotionPolicyName: string;
  promotionScoreConfigId: string;
  promotionScoreDataType: EvaluationPromotionScoreDataType;
  promotionMinimumScore: string;
  promotionAcceptedValues: string;
  promotionDiversityDimension: EvaluationPromotionDiversityDimension;
  promotionMinimumBuckets: string;
  promotionDispatchId: string;
  promotionSaving: boolean;
  promotionActionId: string | null;
  caseRoutingPolicyName: string;
  caseRoutingSourcePromotionVersionId: string;
  caseRoutingGoldenTargetSize: string;
  caseRoutingGoldenMinimumSize: string;
  caseRoutingBadCaseTargetSize: string;
  caseRoutingBadCaseMinimumSize: string;
  caseRoutingPromotionRunIds: string[];
  caseRoutingGoldenDatasetId: string;
  caseRoutingBadCaseDatasetId: string;
  caseRoutingSaving: boolean;
  caseRoutingActionId: string | null;
  failureTaxonomyPolicyName: string;
  failureTaxonomySourceRoutingVersionId: string;
  failureTaxonomySemanticVersionId: string;
  failureTaxonomyMinOccurrences: string;
  failureTaxonomyMinSourceRuns: string;
  failureTaxonomyIncludeIsolated: boolean;
  failureTaxonomyMaxCandidates: string;
  experienceSourceRoutingRunId: string;
  experienceSemanticRunId: string;
  failureTaxonomySaving: boolean;
  failureTaxonomyActionId: string | null;
  experienceReviewComments: Record<string, string>;
  onSelectDataset: (value: string) => void;
  onChangeTrace: (value: string) => void;
  onChangeObservation: (value: string) => void;
  onSubmit: () => void;
  onChangeCandidateName: (value: string) => void;
  onChangeCandidateEnvironment: (value: string) => void;
  onChangeCandidateType: (value: string) => void;
  onChangeCandidateWindowHours: (value: string) => void;
  onChangeCandidateRootOnly: (value: boolean) => void;
  onSearchCandidates: () => void;
  onToggleCandidate: (candidate: TraceDatasetCandidate) => void;
  onSubmitCuration: () => void;
  onChangeSamplingPolicyName: (value: string) => void;
  onChangeSamplingSampleSize: (value: string) => void;
  onChangeSamplingMinimumSize: (value: string) => void;
  onCreateSamplingPolicy: () => void;
  onCreateSamplingVersion: (policy: EvaluationSamplingPolicy) => void;
  onRunSamplingVersion: (version: EvaluationSamplingPolicyVersion) => void;
  onChangeAnnotationProviderQueue: (value: string) => void;
  onChangeAnnotationBinding: (value: string) => void;
  onChangeAnnotationCurationBatch: (value: string) => void;
  onBindAnnotationQueue: () => void;
  onDispatchAnnotationBatch: () => void;
  onRetryAnnotationDispatch: (dispatch: EvaluationAnnotationDispatch) => void;
  onReconcileAnnotationDispatch: (dispatch: EvaluationAnnotationDispatch) => void;
  onChangePromotionPolicyName: (value: string) => void;
  onChangePromotionScoreConfig: (value: string) => void;
  onChangePromotionScoreDataType: (value: EvaluationPromotionScoreDataType) => void;
  onChangePromotionMinimumScore: (value: string) => void;
  onChangePromotionAcceptedValues: (value: string) => void;
  onChangePromotionDiversityDimension: (value: EvaluationPromotionDiversityDimension) => void;
  onChangePromotionMinimumBuckets: (value: string) => void;
  onChangePromotionDispatch: (value: string) => void;
  onCreatePromotionPolicy: () => void;
  onCreatePromotionVersion: (policy: EvaluationPromotionPolicy) => void;
  onRunPromotionVersion: (version: EvaluationPromotionPolicyVersion) => void;
  onChangeCaseRoutingPolicyName: (value: string) => void;
  onChangeCaseRoutingSourcePromotionVersion: (value: string) => void;
  onChangeCaseRoutingGoldenTargetSize: (value: string) => void;
  onChangeCaseRoutingGoldenMinimumSize: (value: string) => void;
  onChangeCaseRoutingBadCaseTargetSize: (value: string) => void;
  onChangeCaseRoutingBadCaseMinimumSize: (value: string) => void;
  onChangeCaseRoutingPromotionRuns: (values: string[]) => void;
  onChangeCaseRoutingGoldenDataset: (value: string) => void;
  onChangeCaseRoutingBadCaseDataset: (value: string) => void;
  onCreateCaseRoutingPolicy: () => void;
  onCreateCaseRoutingVersion: (policy: EvaluationCaseRoutingPolicy) => void;
  onRunCaseRoutingVersion: (version: EvaluationCaseRoutingPolicyVersion) => void;
  onChangeFailureTaxonomyPolicyName: (value: string) => void;
  onChangeFailureTaxonomySourceRoutingVersion: (value: string) => void;
  onChangeFailureTaxonomySemanticVersion: (value: string) => void;
  onChangeFailureTaxonomyMinOccurrences: (value: string) => void;
  onChangeFailureTaxonomyMinSourceRuns: (value: string) => void;
  onChangeFailureTaxonomyIncludeIsolated: (value: boolean) => void;
  onChangeFailureTaxonomyMaxCandidates: (value: string) => void;
  onChangeExperienceSourceRoutingRun: (value: string) => void;
  onChangeExperienceSemanticRun: (value: string) => void;
  onCreateFailureTaxonomyPolicy: () => void;
  onCreateFailureTaxonomyVersion: (policy: EvaluationFailureTaxonomyPolicy) => void;
  onRunFailureTaxonomyVersion: (version: EvaluationFailureTaxonomyPolicyVersion) => void;
  onChangeExperienceReviewComment: (publicId: string, comment: string) => void;
  onReviewExperienceCandidate: (
    candidate: EvaluationExperienceCandidate,
    decision: "APPROVED" | "REJECTED",
  ) => void;
  onChangeCurationComment: (publicId: string, comment: string) => void;
  onReviewCuration: (
    batch: EvaluationDatasetCurationBatch,
    decision: "APPROVED" | "REJECTED",
  ) => void;
  onMaterializeCuration: (batch: EvaluationDatasetCurationBatch) => void;
}) {
  const eligibleDatasets = datasets.filter(
    (dataset) =>
      dataset.provider === "LANGFUSE" &&
      dataset.status === "ACTIVE" &&
      dataset.provider_dataset_ref,
  );
  const selectedPromotionBinding = annotationQueueBindings.find(
    (binding) => binding.public_id === annotationBindingId,
  );
  const completedPromotionDispatches = annotationDispatches.filter(
    (dispatch) =>
      dispatch.binding_public_id === annotationBindingId &&
      dispatch.status === "SYNCED" &&
      dispatch.completed_count === dispatch.item_count,
  );
  const bucketedPromotionVersions = promotionPolicies.flatMap((policy) =>
    policy.versions
      .filter((version) => version.diversity_dimension !== "NONE")
      .map((version) => ({ policy, version })),
  );
  const routablePromotionRuns = promotionRuns.filter(
    (run) =>
      run.policy_version_public_id === caseRoutingSourcePromotionVersionId,
  );
  const caseRoutingVersions = caseRoutingPolicies.flatMap((policy) =>
    policy.versions.map((version) => ({ policy, version })),
  );
  const eligibleExperienceSourceRuns = caseRoutingRuns.filter(
    (run) =>
      run.outcome === "ROUTED" &&
      run.policy_version_public_id === failureTaxonomySourceRoutingVersionId,
  );
  const eligibleSemanticVersions = semanticClusteringPolicies.flatMap((policy) =>
    policy.versions
      .filter(
        (version) =>
          version.source_case_routing_policy_version_public_id ===
          failureTaxonomySourceRoutingVersionId,
      )
      .map((version) => ({ policy, version })),
  );
  const eligibleSemanticRuns = semanticClusteringRuns.filter(
    (run) =>
      run.policy_version_public_id === failureTaxonomySemanticVersionId &&
      run.source_case_routing_run_public_id === experienceSourceRoutingRunId,
  );
  return (
    <div className="space-y-4">
      <Card
        title={zh ? "可复现生产采样策略" : "Reproducible production sampling"}
        description={
          zh
            ? "把当前 metadata-only 筛选冻结为版本，并用确定性哈希生成待人工审批批次。"
            : "Freeze metadata-only filters as a version and deterministically create a review batch."
        }
        padded
      >
        <div className="space-y-4">
          <div className="rounded-lg border border-emerald-100 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
            {zh
              ? "采样运行显式绑定策略版本、Dataset 和时间窗口；已治理来源总是排除。运行只保存候选计数、引用和摘要，不读取原始 IO。"
              : "Runs pin a policy version, Dataset, and window. Governed sources are always excluded; only counts, references, and digests are stored."}
          </div>
          <div className="grid gap-3 md:grid-cols-4">
            <label className="block text-sm font-medium text-slate-700 md:col-span-2">
              {zh ? "策略名称" : "Policy name"}
              <input
                aria-label={zh ? "采样策略名称" : "Sampling policy name"}
                className="mt-1 h-10 w-full rounded-lg border border-slate-200 px-3 text-sm"
                value={samplingPolicyName}
                onChange={(event) => onChangeSamplingPolicyName(event.target.value)}
              />
            </label>
            <label className="block text-sm font-medium text-slate-700">
              {zh ? "目标样本数" : "Target size"}
              <input
                aria-label={zh ? "采样目标样本数" : "Sampling target size"}
                type="number"
                min="1"
                max="20"
                className="mt-1 h-10 w-full rounded-lg border border-slate-200 px-3 text-sm"
                value={samplingSampleSize}
                onChange={(event) => onChangeSamplingSampleSize(event.target.value)}
              />
            </label>
            <label className="block text-sm font-medium text-slate-700">
              {zh ? "最低样本数" : "Minimum size"}
              <input
                aria-label={zh ? "采样最低样本数" : "Sampling minimum size"}
                type="number"
                min="1"
                max="20"
                className="mt-1 h-10 w-full rounded-lg border border-slate-200 px-3 text-sm"
                value={samplingMinimumSize}
                onChange={(event) => onChangeSamplingMinimumSize(event.target.value)}
              />
            </label>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <Button
              onClick={onCreateSamplingPolicy}
              disabled={samplingSaving}
              icon={<GitFork className="h-4 w-4" />}
            >
              {samplingSaving
                ? zh ? "正在创建…" : "Creating…"
                : zh ? "创建策略与 v1" : "Create policy + v1"}
            </Button>
            <span className="text-xs text-slate-500">
              {zh
                ? "版本采用下方候选区的名称、环境、类型和 root-only 条件；候选上限固定为 100。"
                : "Versions use the candidate filters below; candidate limit is fixed at 100."}
            </span>
          </div>
          <div className="grid gap-3 xl:grid-cols-2">
            {samplingPolicies.map((policy) => (
              <div key={policy.public_id} className="rounded-lg border border-slate-200 p-4">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <div className="font-medium text-slate-800">{policy.name}</div>
                    <div className="mt-1 font-mono text-xs text-slate-400">{policy.public_id}</div>
                  </div>
                  <Button
                    variant="secondary"
                    onClick={() => onCreateSamplingVersion(policy)}
                    disabled={samplingActionId === policy.public_id}
                  >
                    {zh ? "保存当前筛选为新版本" : "Add version from filters"}
                  </Button>
                </div>
                <div className="mt-3 space-y-2">
                  {policy.versions.map((version) => (
                    <div key={version.public_id} className="rounded-md bg-slate-50 px-3 py-3">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="flex flex-wrap items-center gap-2 text-xs text-slate-600">
                          <Badge tone="indigo">v{version.version}</Badge>
                          <span>{version.strategy}</span>
                          <span>{version.sample_size} / min {version.minimum_sample_size}</span>
                          <span className="font-mono">{shortDigest(version.config_digest)}</span>
                        </div>
                        <Button
                          onClick={() => onRunSamplingVersion(version)}
                          disabled={
                            !eligibleDatasets.length ||
                            samplingActionId === version.public_id
                          }
                          icon={<Search className="h-4 w-4" />}
                        >
                          {samplingActionId === version.public_id
                            ? zh ? "采样中…" : "Sampling…"
                            : zh ? "按当前窗口运行" : "Run current window"}
                        </Button>
                      </div>
                      <div className="mt-2 text-xs text-slate-500">
                        {version.observation_name ?? "*"} · {version.environment ?? "*"} · {version.observation_type ?? "*"} · {version.root_only ? "root-only" : "all"}
                      </div>
                    </div>
                  ))}
                  {!policy.versions.length ? (
                    <div className="text-xs text-amber-700">
                      {zh ? "此策略尚无版本，请保存当前筛选。" : "No version yet; save current filters."}
                    </div>
                  ) : null}
                </div>
              </div>
            ))}
            {!samplingPolicies.length ? (
              <EmptyState text={zh ? "还没有采样策略。" : "No sampling policies yet."} />
            ) : null}
          </div>
          {samplingRuns.length ? (
            <div className="rounded-lg border border-slate-200">
              <div className="border-b border-slate-100 px-4 py-2 text-sm font-medium text-slate-700">
                {zh ? "最近采样运行" : "Recent sampling runs"}
              </div>
              <div className="divide-y divide-slate-100">
                {samplingRuns.slice(0, 6).map((run) => (
                  <div key={run.public_id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-3 text-xs text-slate-600">
                    <span>
                      v{run.policy_version} · {run.selected_count}/{run.eligible_count} {zh ? "已选/可选" : "selected/eligible"}
                    </span>
                    <span className="font-mono">batch:{run.curation_batch_public_id}</span>
                    <span className="font-mono">run:{shortDigest(run.run_digest)}</span>
                    <span>{formatTime(run.created_at, locale)}</span>
                  </div>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      </Card>

      <Card
        title={zh ? "Langfuse 人工标注队列" : "Langfuse annotation queues"}
        description={
          zh
            ? "绑定现有 Langfuse Queue，把不可变策展批次异步派发并对账完成状态。"
            : "Bind an existing Langfuse queue, dispatch immutable curation batches, and reconcile completion."
        }
        padded
      >
        <div className="space-y-4">
          <div className="rounded-lg border border-amber-100 bg-amber-50 px-4 py-3 text-sm text-amber-900">
            {zh
              ? "DuckDock 只保存队列、Observation 和状态引用；评分与修订内容留在 Langfuse。Langfuse 的 COMPLETED 只表示标注进度，不会自动批准或入集 DuckDock 策展批次。"
              : "DuckDock stores only queue, Observation, and status references. Scores stay in Langfuse, and COMPLETED never auto-approves a DuckDock curation batch."}
          </div>
          <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto]">
            <label className="block text-sm font-medium text-slate-700">
              {zh ? "Provider 队列" : "Provider queue"}
              <select
                aria-label={zh ? "Langfuse Provider 队列" : "Langfuse provider queue"}
                className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm"
                value={annotationProviderQueueRef}
                onChange={(event) => onChangeAnnotationProviderQueue(event.target.value)}
              >
                {!annotationProviderQueues.length ? <option value="">—</option> : null}
                {annotationProviderQueues.map((queue) => (
                  <option key={queue.provider_queue_ref} value={queue.provider_queue_ref}>
                    {queue.name} · {queue.score_config_ids.length} Score Config
                    {queue.already_bound ? (zh ? " · 已绑定" : " · bound") : ""}
                  </option>
                ))}
              </select>
            </label>
            <div className="flex items-end">
              <Button
                onClick={onBindAnnotationQueue}
                disabled={
                  annotationSaving ||
                  !annotationProviderQueueRef ||
                  annotationProviderQueues.find(
                    (queue) => queue.provider_queue_ref === annotationProviderQueueRef,
                  )?.already_bound === true
                }
                icon={<GitFork className="h-4 w-4" />}
              >
                {annotationSaving
                  ? zh ? "绑定中…" : "Binding…"
                  : zh ? "绑定并冻结配置" : "Bind and snapshot"}
              </Button>
            </div>
          </div>
          {!annotationProviderQueues.length ? (
            <div className="text-xs text-amber-700">
              {zh
                ? "未发现可用队列，或 Langfuse 当前不可达。请先在 Langfuse 创建带 Score Config 的 Annotation Queue。"
                : "No queue was found or Langfuse is unavailable. Create a queue with a score config first."}
            </div>
          ) : null}
          <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
            <label className="block text-sm font-medium text-slate-700">
              {zh ? "已绑定队列" : "Bound queue"}
              <select
                aria-label={zh ? "已绑定标注队列" : "Bound annotation queue"}
                className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm"
                value={annotationBindingId}
                onChange={(event) => onChangeAnnotationBinding(event.target.value)}
              >
                {!annotationQueueBindings.length ? <option value="">—</option> : null}
                {annotationQueueBindings.map((binding) => (
                  <option key={binding.public_id} value={binding.public_id}>
                    {binding.provider_queue_name} · {binding.score_config_ids.length} Score Config
                  </option>
                ))}
              </select>
            </label>
            <label className="block text-sm font-medium text-slate-700">
              {zh ? "策展批次" : "Curation batch"}
              <select
                aria-label={zh ? "待派发策展批次" : "Curation batch to dispatch"}
                className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm"
                value={annotationCurationBatchId}
                onChange={(event) => onChangeAnnotationCurationBatch(event.target.value)}
              >
                {!curationBatches.length ? <option value="">—</option> : null}
                {curationBatches.map((batch) => (
                  <option key={batch.public_id} value={batch.public_id}>
                    {batch.public_id} · {batch.item_count} {zh ? "条" : "items"} · {batch.status}
                  </option>
                ))}
              </select>
            </label>
            <div className="flex items-end">
              <Button
                onClick={onDispatchAnnotationBatch}
                disabled={
                  !annotationBindingId ||
                  !annotationCurationBatchId ||
                  annotationActionId === annotationCurationBatchId
                }
                icon={<ListChecks className="h-4 w-4" />}
              >
                {annotationActionId === annotationCurationBatchId
                  ? zh ? "派发中…" : "Dispatching…"
                  : zh ? "持久化并异步派发" : "Persist and dispatch"}
              </Button>
            </div>
          </div>
          {annotationDispatches.length ? (
            <div className="rounded-lg border border-slate-200">
              <div className="border-b border-slate-100 px-4 py-2 text-sm font-medium text-slate-700">
                {zh ? "最近标注派发" : "Recent annotation dispatches"}
              </div>
              <div className="divide-y divide-slate-100">
                {annotationDispatches.slice(0, 8).map((dispatch) => {
                  const statusTone: Tone =
                    dispatch.status === "SYNCED"
                      ? "emerald"
                      : dispatch.status === "FAILED"
                        ? "rose"
                        : dispatch.status === "RUNNING"
                          ? "indigo"
                          : "amber";
                  return (
                    <div key={dispatch.public_id} className="space-y-2 px-4 py-3">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="flex flex-wrap items-center gap-2 text-xs text-slate-600">
                          <Badge tone={statusTone}>{dispatch.status}</Badge>
                          <span>{dispatch.provider_queue_name}</span>
                          <span className="font-mono">{dispatch.curation_batch_public_id}</span>
                          <span>
                            {dispatch.synced_count}/{dispatch.item_count} {zh ? "已同步" : "synced"}
                          </span>
                          <span>
                            {dispatch.completed_count}/{dispatch.item_count} {zh ? "已完成" : "completed"}
                          </span>
                          <span>{formatTime(dispatch.created_at, locale)}</span>
                        </div>
                        <div className="flex gap-2">
                          {dispatch.status === "FAILED" ? (
                            <Button
                              variant="secondary"
                              onClick={() => onRetryAnnotationDispatch(dispatch)}
                              disabled={annotationActionId === dispatch.public_id}
                            >
                              {zh ? "重试" : "Retry"}
                            </Button>
                          ) : null}
                          {dispatch.status === "SYNCED" ? (
                            <Button
                              variant="secondary"
                              onClick={() => onReconcileAnnotationDispatch(dispatch)}
                              disabled={annotationActionId === dispatch.public_id}
                            >
                              {zh ? "对账 Langfuse 状态" : "Reconcile Langfuse"}
                            </Button>
                          ) : null}
                        </div>
                      </div>
                      <div className="font-mono text-[11px] text-slate-400">
                        {dispatch.public_id} · {shortDigest(dispatch.request_digest)}
                        {dispatch.error_code ? ` · ${dispatch.error_code}` : ""}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          ) : (
            <EmptyState text={zh ? "还没有标注派发。" : "No annotation dispatches yet."} />
          )}
        </div>
      </Card>

      <Card
        title={zh ? "标注驱动 Promotion 策略" : "Annotation-driven promotion policy"}
        description={
          zh
            ? "把 Langfuse 人工评分和 metadata-only 多样性转成可审计推荐；人工审批与 Dataset 物化保持独立。"
            : "Turn Langfuse scores and metadata-only diversity into auditable recommendations while keeping approval and materialization separate."
        }
        padded
      >
        <div className="space-y-4">
          <div className="rounded-lg border border-indigo-100 bg-indigo-50 px-4 py-3 text-sm text-indigo-900">
            {zh
              ? "运行要求派发 100% 完成并严格匹配冻结的 Score Config。DuckDock 只保存通过/阻断、原因码和哈希证据，不保存原始分值、评论或修订内容；RECOMMENDED 也不会自动入集。"
              : "Runs require 100% completion and the pinned score config. DuckDock stores only decisions, reason codes, and evidence hashes; RECOMMENDED never auto-promotes."}
          </div>
          <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto]">
            <label className="block text-sm font-medium text-slate-700">
              {zh ? "Promotion 策略名称" : "Promotion policy name"}
              <input
                aria-label={zh ? "Promotion 策略名称" : "Promotion policy name"}
                className="mt-1 h-10 w-full rounded-lg border border-slate-200 px-3 text-sm"
                value={promotionPolicyName}
                onChange={(event) => onChangePromotionPolicyName(event.target.value)}
              />
            </label>
            <div className="flex items-end">
              <Button
                onClick={onCreatePromotionPolicy}
                disabled={promotionSaving || !promotionPolicyName.trim()}
                icon={<GitFork className="h-4 w-4" />}
              >
                {promotionSaving
                  ? zh ? "创建中…" : "Creating…"
                  : zh ? "创建策略身份" : "Create policy"}
              </Button>
            </div>
          </div>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-6">
            <label className="block text-sm font-medium text-slate-700 xl:col-span-2">
              {zh ? "冻结的 Score Config" : "Pinned score config"}
              <select
                aria-label={zh ? "Promotion Score Config" : "Promotion score config"}
                className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm"
                value={promotionScoreConfigId}
                onChange={(event) => onChangePromotionScoreConfig(event.target.value)}
              >
                {!selectedPromotionBinding?.score_config_ids.length ? <option value="">—</option> : null}
                {selectedPromotionBinding?.score_config_ids.map((value) => (
                  <option key={value} value={value}>{value}</option>
                ))}
              </select>
            </label>
            <label className="block text-sm font-medium text-slate-700">
              {zh ? "评分类型" : "Score type"}
              <select
                aria-label={zh ? "Promotion 评分类型" : "Promotion score type"}
                className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm"
                value={promotionScoreDataType}
                onChange={(event) =>
                  onChangePromotionScoreDataType(event.target.value as EvaluationPromotionScoreDataType)
                }
              >
                <option value="NUMERIC">NUMERIC</option>
                <option value="BOOLEAN">BOOLEAN</option>
                <option value="CATEGORICAL">CATEGORICAL</option>
              </select>
            </label>
            <label className="block text-sm font-medium text-slate-700">
              {promotionScoreDataType === "NUMERIC"
                ? zh ? "最低分" : "Minimum score"
                : zh ? "允许值（逗号）" : "Allowed values (CSV)"}
              <input
                aria-label={zh ? "Promotion 质量条件" : "Promotion quality rule"}
                type={promotionScoreDataType === "NUMERIC" ? "number" : "text"}
                step={promotionScoreDataType === "NUMERIC" ? "0.01" : undefined}
                className="mt-1 h-10 w-full rounded-lg border border-slate-200 px-3 text-sm"
                value={
                  promotionScoreDataType === "NUMERIC"
                    ? promotionMinimumScore
                    : promotionAcceptedValues
                }
                onChange={(event) =>
                  promotionScoreDataType === "NUMERIC"
                    ? onChangePromotionMinimumScore(event.target.value)
                    : onChangePromotionAcceptedValues(event.target.value)
                }
                placeholder={promotionScoreDataType === "BOOLEAN" ? "true" : undefined}
              />
            </label>
            <label className="block text-sm font-medium text-slate-700">
              {zh ? "多样性维度" : "Diversity dimension"}
              <select
                aria-label={zh ? "Promotion 多样性维度" : "Promotion diversity dimension"}
                className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm"
                value={promotionDiversityDimension}
                onChange={(event) =>
                  onChangePromotionDiversityDimension(
                    event.target.value as EvaluationPromotionDiversityDimension,
                  )
                }
              >
                <option value="NONE">NONE</option>
                <option value="OBSERVATION_NAME">OBSERVATION_NAME</option>
                <option value="OBSERVATION_TYPE">OBSERVATION_TYPE</option>
                <option value="ENVIRONMENT">ENVIRONMENT</option>
              </select>
            </label>
            <label className="block text-sm font-medium text-slate-700">
              {zh ? "最低桶数" : "Minimum buckets"}
              <input
                aria-label={zh ? "Promotion 最低多样性桶数" : "Promotion minimum buckets"}
                type="number"
                min="1"
                max="20"
                disabled={promotionDiversityDimension === "NONE"}
                className="mt-1 h-10 w-full rounded-lg border border-slate-200 px-3 text-sm disabled:bg-slate-50"
                value={promotionDiversityDimension === "NONE" ? "1" : promotionMinimumBuckets}
                onChange={(event) => onChangePromotionMinimumBuckets(event.target.value)}
              />
            </label>
          </div>
          <div className="grid gap-3 xl:grid-cols-2">
            {promotionPolicies.map((policy) => (
              <div key={policy.public_id} className="rounded-lg border border-slate-200 p-4">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <div className="font-medium text-slate-800">{policy.name}</div>
                    <div className="mt-1 font-mono text-xs text-slate-400">{policy.public_id}</div>
                  </div>
                  <Button
                    variant="secondary"
                    onClick={() => onCreatePromotionVersion(policy)}
                    disabled={
                      !annotationBindingId ||
                      !promotionScoreConfigId ||
                      promotionActionId === policy.public_id
                    }
                  >
                    {promotionActionId === policy.public_id
                      ? zh ? "保存中…" : "Saving…"
                      : zh ? "新增当前规则版本" : "Add current rule version"}
                  </Button>
                </div>
                <div className="mt-3 space-y-2">
                  {policy.versions.map((version) => (
                    <div key={version.public_id} className="rounded-md bg-slate-50 px-3 py-3">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="flex flex-wrap items-center gap-2 text-xs text-slate-600">
                          <Badge tone="indigo">v{version.version}</Badge>
                          <span>{version.score_data_type}</span>
                          <span>{version.diversity_dimension} ≥ {version.min_distinct_buckets}</span>
                          <span className="font-mono">{shortDigest(version.config_digest)}</span>
                        </div>
                        <Button
                          onClick={() => onRunPromotionVersion(version)}
                          disabled={
                            !promotionDispatchId ||
                            version.binding_public_id !== annotationBindingId ||
                            promotionActionId === version.public_id
                          }
                          icon={<ShieldCheck className="h-4 w-4" />}
                        >
                          {promotionActionId === version.public_id
                            ? zh ? "评估中…" : "Evaluating…"
                            : zh ? "评估已完成派发" : "Evaluate completed dispatch"}
                        </Button>
                      </div>
                      <div className="mt-2 font-mono text-[11px] text-slate-400">
                        {version.score_config_id} · queue:{shortDigest(version.provider_queue_ref)}
                      </div>
                    </div>
                  ))}
                  {!policy.versions.length ? (
                    <div className="text-xs text-amber-700">
                      {zh ? "尚无规则版本。" : "No rule version yet."}
                    </div>
                  ) : null}
                </div>
              </div>
            ))}
            {!promotionPolicies.length ? (
              <EmptyState text={zh ? "还没有 Promotion 策略。" : "No promotion policies yet."} />
            ) : null}
          </div>
          <label className="block text-sm font-medium text-slate-700">
            {zh ? "已全部完成的派发" : "Fully completed dispatch"}
            <select
              aria-label={zh ? "Promotion 已完成派发" : "Promotion completed dispatch"}
              className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm"
              value={promotionDispatchId}
              onChange={(event) => onChangePromotionDispatch(event.target.value)}
            >
              {!completedPromotionDispatches.length ? <option value="">—</option> : null}
              {completedPromotionDispatches.map((dispatch) => (
                <option key={dispatch.public_id} value={dispatch.public_id}>
                  {dispatch.public_id} · {dispatch.curation_batch_public_id} · {dispatch.item_count} {zh ? "条" : "items"}
                </option>
              ))}
            </select>
          </label>
          {promotionRuns.length ? (
            <div className="rounded-lg border border-slate-200">
              <div className="border-b border-slate-100 px-4 py-2 text-sm font-medium text-slate-700">
                {zh ? "最近 Promotion 推荐" : "Recent promotion recommendations"}
              </div>
              <div className="divide-y divide-slate-100">
                {promotionRuns.slice(0, 8).map((run) => (
                  <div key={run.public_id} className="space-y-1 px-4 py-3">
                    <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-600">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge tone={run.outcome === "RECOMMENDED" ? "emerald" : "rose"}>
                          {run.outcome}
                        </Badge>
                        <span>{run.passed_count}/{run.item_count} {zh ? "质量通过" : "quality passed"}</span>
                        <span>{run.distinct_bucket_count} {zh ? "多样性桶" : "diversity buckets"}</span>
                        <span>{run.reason_codes.join(", ")}</span>
                      </div>
                      <span>{formatTime(run.created_at, locale)}</span>
                    </div>
                    <div className="font-mono text-[11px] text-slate-400">
                      {run.public_id} · batch:{run.curation_batch_public_id} · evidence:{shortDigest(run.evidence_digest)}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      </Card>

      <Card
        title={zh ? "跨批次 Golden / Bad Case 路由" : "Cross-batch Golden / Bad Case routing"}
        description={
          zh
            ? "把同一 Promotion 版本的多个运行按冻结聚类桶均衡路由为两个独立待审核批次。"
            : "Cluster-balance multiple runs from one Promotion version into two independent review batches."
        }
        padded
      >
        <div className="space-y-4">
          <div className="rounded-lg border border-amber-100 bg-amber-50 px-4 py-3 text-sm text-amber-900">
            {zh
              ? "质量通过项进入 Golden 候选，已有评分但未通过项进入 Bad Case 候选；缺评分或聚类证据的项只记为 EXCLUDED。最低数量不足时整次 BLOCKED，绝不生成半批次；ROUTED 后两个批次也必须分别人工审批。"
              : "Quality passes become Golden candidates, scored failures become Bad Case candidates, and missing evidence is EXCLUDED. Minimum shortfalls block atomically; routed batches still require separate human approval."}
          </div>

          <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto]">
            <label className="block text-sm font-medium text-slate-700">
              {zh ? "路由策略名称" : "Routing policy name"}
              <input
                aria-label={zh ? "Golden Bad Case 路由策略名称" : "Golden Bad Case routing policy name"}
                className="mt-1 h-10 w-full rounded-lg border border-slate-200 px-3 text-sm"
                value={caseRoutingPolicyName}
                onChange={(event) => onChangeCaseRoutingPolicyName(event.target.value)}
              />
            </label>
            <div className="flex items-end">
              <Button
                onClick={onCreateCaseRoutingPolicy}
                disabled={caseRoutingSaving || !caseRoutingPolicyName.trim()}
                icon={<GitFork className="h-4 w-4" />}
              >
                {caseRoutingSaving
                  ? zh ? "创建中…" : "Creating…"
                  : zh ? "创建路由策略" : "Create routing policy"}
              </Button>
            </div>
          </div>

          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
            <label className="block text-sm font-medium text-slate-700 xl:col-span-1">
              {zh ? "来源 Promotion 版本" : "Source Promotion version"}
              <select
                aria-label={zh ? "路由来源 Promotion 版本" : "Routing source Promotion version"}
                className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm"
                value={caseRoutingSourcePromotionVersionId}
                onChange={(event) =>
                  onChangeCaseRoutingSourcePromotionVersion(event.target.value)
                }
              >
                {!bucketedPromotionVersions.length ? <option value="">—</option> : null}
                {bucketedPromotionVersions.map(({ policy, version }) => (
                  <option key={version.public_id} value={version.public_id}>
                    {policy.name} v{version.version} · {version.diversity_dimension}
                  </option>
                ))}
              </select>
            </label>
            {[
              [zh ? "Golden 目标数" : "Golden target", caseRoutingGoldenTargetSize, onChangeCaseRoutingGoldenTargetSize, "case-routing-golden-target"],
              [zh ? "Golden 最低数" : "Golden minimum", caseRoutingGoldenMinimumSize, onChangeCaseRoutingGoldenMinimumSize, "case-routing-golden-minimum"],
              [zh ? "Bad Case 目标数" : "Bad Case target", caseRoutingBadCaseTargetSize, onChangeCaseRoutingBadCaseTargetSize, "case-routing-bad-target"],
              [zh ? "Bad Case 最低数" : "Bad Case minimum", caseRoutingBadCaseMinimumSize, onChangeCaseRoutingBadCaseMinimumSize, "case-routing-bad-minimum"],
            ].map(([label, value, onChange, key]) => (
              <label key={key as string} className="block text-sm font-medium text-slate-700">
                {label as string}
                <input
                  aria-label={label as string}
                  type="number"
                  min="1"
                  max="20"
                  className="mt-1 h-10 w-full rounded-lg border border-slate-200 px-3 text-sm"
                  value={value as string}
                  onChange={(event) =>
                    (onChange as (next: string) => void)(event.target.value)
                  }
                />
              </label>
            ))}
          </div>

          <div className="grid gap-3 xl:grid-cols-2">
            {caseRoutingPolicies.map((policy) => (
              <div key={policy.public_id} className="rounded-lg border border-slate-200 p-4">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <div className="font-medium text-slate-800">{policy.name}</div>
                    <div className="mt-1 font-mono text-xs text-slate-400">{policy.public_id}</div>
                  </div>
                  <Button
                    variant="secondary"
                    onClick={() => onCreateCaseRoutingVersion(policy)}
                    disabled={
                      !caseRoutingSourcePromotionVersionId ||
                      caseRoutingActionId === policy.public_id
                    }
                  >
                    {caseRoutingActionId === policy.public_id
                      ? zh ? "保存中…" : "Saving…"
                      : zh ? "新增当前路由版本" : "Add routing version"}
                  </Button>
                </div>
                <div className="mt-3 space-y-2">
                  {policy.versions.map((version) => (
                    <div key={version.public_id} className="rounded-md bg-slate-50 px-3 py-3">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="flex flex-wrap items-center gap-2 text-xs text-slate-600">
                          <Badge tone="indigo">v{version.version}</Badge>
                          <span>{version.strategy}</span>
                          <span>G {version.golden_min_items}/{version.golden_target_size}</span>
                          <span>B {version.bad_case_min_items}/{version.bad_case_target_size}</span>
                          <span className="font-mono">{shortDigest(version.config_digest)}</span>
                        </div>
                        <Button
                          onClick={() => onRunCaseRoutingVersion(version)}
                          disabled={
                            version.source_promotion_policy_version_public_id !==
                              caseRoutingSourcePromotionVersionId ||
                            !caseRoutingPromotionRunIds.length ||
                            !caseRoutingGoldenDatasetId ||
                            !caseRoutingBadCaseDatasetId ||
                            caseRoutingGoldenDatasetId === caseRoutingBadCaseDatasetId ||
                            caseRoutingActionId === version.public_id
                          }
                          icon={<GitCompareArrows className="h-4 w-4" />}
                        >
                          {caseRoutingActionId === version.public_id
                            ? zh ? "路由中…" : "Routing…"
                            : zh ? "执行跨批次路由" : "Run cross-batch routing"}
                        </Button>
                      </div>
                      <div className="mt-2 font-mono text-[11px] text-slate-400">
                        source:{version.source_promotion_policy_version_public_id}
                      </div>
                    </div>
                  ))}
                  {!policy.versions.length ? (
                    <div className="text-xs text-amber-700">
                      {zh ? "尚无路由版本。" : "No routing version yet."}
                    </div>
                  ) : null}
                </div>
              </div>
            ))}
            {!caseRoutingPolicies.length ? (
              <EmptyState text={zh ? "还没有跨批次路由策略。" : "No cross-batch routing policies yet."} />
            ) : null}
          </div>

          <div className="grid gap-3 lg:grid-cols-3">
            <label className="block text-sm font-medium text-slate-700">
              {zh ? "同版本 Promotion 运行（可多选）" : "Promotion runs from one version"}
              <select
                multiple
                aria-label={zh ? "路由 Promotion 运行" : "Routing Promotion runs"}
                className="mt-1 min-h-28 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm"
                value={caseRoutingPromotionRunIds}
                onChange={(event) =>
                  onChangeCaseRoutingPromotionRuns(
                    Array.from(event.target.selectedOptions, (option) => option.value),
                  )
                }
              >
                {routablePromotionRuns.map((run) => (
                  <option key={run.public_id} value={run.public_id}>
                    {run.public_id} · {run.outcome} · {run.passed_count}/{run.item_count}
                  </option>
                ))}
              </select>
              <span className="mt-1 block text-xs text-slate-400">
                {zh
                  ? `已选择 ${caseRoutingPromotionRunIds.length} 个互不重叠运行`
                  : `${caseRoutingPromotionRunIds.length} non-overlapping runs selected`}
              </span>
            </label>
            <label className="block text-sm font-medium text-slate-700">
              {zh ? "Golden Dataset" : "Golden Dataset"}
              <select
                aria-label="Golden Dataset"
                className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm"
                value={caseRoutingGoldenDatasetId}
                onChange={(event) => onChangeCaseRoutingGoldenDataset(event.target.value)}
              >
                {!eligibleDatasets.length ? <option value="">—</option> : null}
                {eligibleDatasets.map((dataset) => (
                  <option key={dataset.public_id} value={dataset.public_id}>{dataset.name}</option>
                ))}
              </select>
            </label>
            <label className="block text-sm font-medium text-slate-700">
              {zh ? "Bad Case Dataset" : "Bad Case Dataset"}
              <select
                aria-label="Bad Case Dataset"
                className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm"
                value={caseRoutingBadCaseDatasetId}
                onChange={(event) => onChangeCaseRoutingBadCaseDataset(event.target.value)}
              >
                {!eligibleDatasets.length ? <option value="">—</option> : null}
                {eligibleDatasets.map((dataset) => (
                  <option key={dataset.public_id} value={dataset.public_id}>{dataset.name}</option>
                ))}
              </select>
            </label>
          </div>

          {caseRoutingRuns.length ? (
            <div className="rounded-lg border border-slate-200">
              <div className="border-b border-slate-100 px-4 py-2 text-sm font-medium text-slate-700">
                {zh ? "最近跨批次路由" : "Recent cross-batch routing"}
              </div>
              <div className="divide-y divide-slate-100">
                {caseRoutingRuns.slice(0, 8).map((run) => (
                  <div key={run.public_id} className="space-y-1 px-4 py-3">
                    <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-600">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge tone={run.outcome === "ROUTED" ? "emerald" : "rose"}>
                          {run.outcome}
                        </Badge>
                        <span>Golden {run.golden_selected_count}/{run.golden_candidate_count}</span>
                        <span>Bad Case {run.bad_case_selected_count}/{run.bad_case_candidate_count}</span>
                        <span>EXCLUDED {run.excluded_count}</span>
                        <span>{run.reason_codes.join(", ")}</span>
                      </div>
                      <span>{formatTime(run.created_at, locale)}</span>
                    </div>
                    <div className="font-mono text-[11px] text-slate-400">
                      {run.public_id} · G:{run.golden_curation_batch_public_id ?? "—"} · B:{run.bad_case_curation_batch_public_id ?? "—"} · routing:{shortDigest(run.routing_digest)}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      </Card>

      <Card
        title={zh ? "失败分类与 Experience 候选" : "Failure taxonomy and Experience candidates"}
        description={
          zh
            ? "把已路由 Bad Case 按冻结 cluster 归类，只生成可人工评审的元数据候选。"
            : "Classify routed Bad Cases by frozen cluster and create metadata-only candidates for human review."
        }
        padded
      >
        <div className="space-y-4">
          <div className="rounded-lg border border-violet-100 bg-violet-50 px-4 py-3 text-sm text-violet-900">
            {zh
              ? "默认继续使用 Case Routing 元数据；显式绑定语义版本时，只消费语义运行留下的 content/vector 摘要、相似度与簇成员。原文和向量不会写入 DuckDock；候选批准也不会自动修改生产 Agent。"
              : "The metadata path remains the default. When explicitly pinned, taxonomy consumes only content/vector digests, similarity, and membership from a semantic run. Raw content and vectors are never stored in DuckDock, and approval never modifies a production Agent."}
          </div>

          <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto]">
            <label className="block text-sm font-medium text-slate-700">
              {zh ? "失败分类策略名称" : "Failure taxonomy policy name"}
              <input
                aria-label={zh ? "失败分类策略名称" : "Failure taxonomy policy name"}
                className="mt-1 h-10 w-full rounded-lg border border-slate-200 px-3 text-sm"
                value={failureTaxonomyPolicyName}
                onChange={(event) =>
                  onChangeFailureTaxonomyPolicyName(event.target.value)
                }
              />
            </label>
            <div className="flex items-end">
              <Button
                onClick={onCreateFailureTaxonomyPolicy}
                disabled={failureTaxonomySaving || !failureTaxonomyPolicyName.trim()}
                icon={<ListChecks className="h-4 w-4" />}
              >
                {failureTaxonomySaving
                  ? zh ? "创建中…" : "Creating…"
                  : zh ? "创建分类策略" : "Create taxonomy policy"}
              </Button>
            </div>
          </div>

          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
            <label className="block text-sm font-medium text-slate-700 xl:col-span-2">
              {zh ? "来源 Case Routing 版本" : "Source Case Routing version"}
              <select
                aria-label={zh ? "失败分类来源路由版本" : "Failure taxonomy source routing version"}
                className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm"
                value={failureTaxonomySourceRoutingVersionId}
                onChange={(event) =>
                  onChangeFailureTaxonomySourceRoutingVersion(event.target.value)
                }
              >
                {!caseRoutingVersions.length ? <option value="">—</option> : null}
                {caseRoutingVersions.map(({ policy, version }) => (
                  <option key={version.public_id} value={version.public_id}>
                    {policy.name} v{version.version} · {version.public_id}
                  </option>
                ))}
              </select>
            </label>
            <label className="block text-sm font-medium text-slate-700 xl:col-span-2">
              {zh ? "语义聚类版本（可选）" : "Semantic clustering version (optional)"}
              <select
                aria-label={zh ? "失败分类语义聚类版本" : "Failure taxonomy semantic version"}
                className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm"
                value={failureTaxonomySemanticVersionId}
                onChange={(event) =>
                  onChangeFailureTaxonomySemanticVersion(event.target.value)
                }
              >
                <option value="">{zh ? "不使用（兼容元数据路径）" : "None (metadata compatibility path)"}</option>
                {eligibleSemanticVersions.map(({ policy, version }) => (
                  <option key={version.public_id} value={version.public_id}>
                    {policy.name} v{version.version} · {version.model_ref}
                  </option>
                ))}
              </select>
            </label>
            {[
              [zh ? "重复条数阈值" : "Occurrence threshold", failureTaxonomyMinOccurrences, onChangeFailureTaxonomyMinOccurrences, "taxonomy-min-occurrences", "2"],
              [zh ? "跨运行阈值" : "Source-run threshold", failureTaxonomyMinSourceRuns, onChangeFailureTaxonomyMinSourceRuns, "taxonomy-min-runs", "2"],
              [zh ? "候选上限" : "Candidate cap", failureTaxonomyMaxCandidates, onChangeFailureTaxonomyMaxCandidates, "taxonomy-max-candidates", "1"],
            ].map(([label, value, onChange, key, min]) => (
              <label key={key as string} className="block text-sm font-medium text-slate-700">
                {label as string}
                <input
                  aria-label={label as string}
                  type="number"
                  min={min as string}
                  max="20"
                  className="mt-1 h-10 w-full rounded-lg border border-slate-200 px-3 text-sm"
                  value={value as string}
                  onChange={(event) =>
                    (onChange as (next: string) => void)(event.target.value)
                  }
                />
              </label>
            ))}
          </div>

          <label className="flex items-center gap-2 text-sm text-slate-700">
            <input
              aria-label={zh ? "允许孤立失败候选" : "Include isolated failure candidates"}
              type="checkbox"
              checked={failureTaxonomyIncludeIsolated}
              onChange={(event) =>
                onChangeFailureTaxonomyIncludeIsolated(event.target.checked)
              }
            />
            {zh
              ? "显式允许孤立失败生成候选（默认关闭，避免噪声）"
              : "Explicitly include isolated failures (off by default to limit noise)"}
          </label>

          <div className="grid gap-3 md:grid-cols-2">
            <label className="block text-sm font-medium text-slate-700">
              {zh ? "来源 ROUTED 运行" : "Source ROUTED run"}
              <select
                aria-label={zh ? "Experience 来源路由运行" : "Experience source routing run"}
                className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm"
                value={experienceSourceRoutingRunId}
                onChange={(event) =>
                  onChangeExperienceSourceRoutingRun(event.target.value)
                }
              >
                {!eligibleExperienceSourceRuns.length ? <option value="">—</option> : null}
                {eligibleExperienceSourceRuns.map((run) => (
                  <option key={run.public_id} value={run.public_id}>
                    {run.public_id} · Bad Case {run.bad_case_selected_count} · {formatTime(run.created_at, locale)}
                  </option>
                ))}
              </select>
            </label>
            <label className="block text-sm font-medium text-slate-700">
              {zh ? "匹配的语义聚类运行" : "Matching semantic clustering run"}
              <select
                aria-label={zh ? "Experience 语义聚类运行" : "Experience semantic clustering run"}
                className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm"
                value={experienceSemanticRunId}
                disabled={!failureTaxonomySemanticVersionId}
                onChange={(event) => onChangeExperienceSemanticRun(event.target.value)}
              >
                <option value="">{failureTaxonomySemanticVersionId ? "—" : zh ? "未启用语义聚类" : "Semantic clustering not enabled"}</option>
                {eligibleSemanticRuns.map((run) => (
                  <option key={run.public_id} value={run.public_id}>
                    {run.public_id} · {run.outcome} · {run.cluster_count} clusters
                  </option>
                ))}
              </select>
            </label>
          </div>

          <div className="grid gap-3 xl:grid-cols-2">
            {failureTaxonomyPolicies.map((policy) => (
              <div key={policy.public_id} className="rounded-lg border border-slate-200 p-4">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <div className="font-medium text-slate-800">{policy.name}</div>
                    <div className="mt-1 font-mono text-xs text-slate-400">
                      {policy.public_id}
                    </div>
                  </div>
                  <Button
                    variant="secondary"
                    onClick={() => onCreateFailureTaxonomyVersion(policy)}
                    disabled={
                      !failureTaxonomySourceRoutingVersionId ||
                      failureTaxonomyActionId === policy.public_id
                    }
                  >
                    {failureTaxonomyActionId === policy.public_id
                      ? zh ? "保存中…" : "Saving…"
                      : zh ? "新增分类版本" : "Add taxonomy version"}
                  </Button>
                </div>
                <div className="mt-3 space-y-2">
                  {policy.versions.map((version) => (
                    <div key={version.public_id} className="rounded-md bg-slate-50 px-3 py-3">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="flex flex-wrap items-center gap-2 text-xs text-slate-600">
                          <Badge tone="indigo">v{version.version}</Badge>
                          <span>items≥{version.min_cluster_occurrences}</span>
                          <span>runs≥{version.min_source_runs}</span>
                          <span>{version.include_isolated ? "isolated:on" : "isolated:off"}</span>
                          <span>max {version.max_candidates}</span>
                        </div>
                        <Button
                          onClick={() => onRunFailureTaxonomyVersion(version)}
                          disabled={
                            version.source_case_routing_policy_version_public_id !==
                              failureTaxonomySourceRoutingVersionId ||
                            !experienceSourceRoutingRunId ||
                            (Boolean(version.source_semantic_clustering_policy_version_public_id) &&
                              !experienceSemanticRunId) ||
                            failureTaxonomyActionId === version.public_id
                          }
                          icon={<FlaskConical className="h-4 w-4" />}
                        >
                          {failureTaxonomyActionId === version.public_id
                            ? zh ? "提取中…" : "Extracting…"
                            : zh ? "提取 Experience 候选" : "Extract candidates"}
                        </Button>
                      </div>
                      <div className="mt-2 font-mono text-[11px] text-slate-400">
                        source:{version.source_case_routing_policy_version_public_id} · semantic:{version.source_semantic_clustering_policy_version_public_id ?? "metadata"} · config:{shortDigest(version.config_digest)}
                      </div>
                    </div>
                  ))}
                  {!policy.versions.length ? (
                    <div className="text-xs text-amber-700">
                      {zh ? "尚无分类版本。" : "No taxonomy version yet."}
                    </div>
                  ) : null}
                </div>
              </div>
            ))}
            {!failureTaxonomyPolicies.length ? (
              <EmptyState text={zh ? "还没有失败分类策略。" : "No failure taxonomy policies yet."} />
            ) : null}
          </div>

          {experienceExtractionRuns.length ? (
            <div className="rounded-lg border border-slate-200">
              <div className="border-b border-slate-100 px-4 py-2 text-sm font-medium text-slate-700">
                {zh ? "最近 Experience 提取与评审" : "Recent Experience extraction and review"}
              </div>
              <div className="divide-y divide-slate-100">
                {experienceExtractionRuns.slice(0, 8).map((run) => (
                  <div key={run.public_id} className="space-y-3 px-4 py-3">
                    <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-600">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge tone={run.outcome === "EXTRACTED" ? "emerald" : "rose"}>
                          {run.outcome}
                        </Badge>
                        <span>{run.source_bad_case_count} Bad Cases</span>
                        <span>{run.cluster_count} clusters</span>
                        <span>{run.candidate_count} candidates</span>
                        <span>{run.reason_codes.join(", ")}</span>
                      </div>
                      <span>{formatTime(run.created_at, locale)}</span>
                    </div>
                    <div className="font-mono text-[11px] text-slate-400">
                      {run.public_id} · source:{run.source_case_routing_run_public_id} · semantic:{run.source_semantic_clustering_run_public_id ?? "metadata"} · extraction:{shortDigest(run.extraction_digest)}
                    </div>
                    {run.candidates.map((candidate) => (
                      <div key={candidate.public_id} className="rounded-md border border-slate-100 bg-slate-50 p-3">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <div className="flex flex-wrap items-center gap-2 text-xs text-slate-600">
                            <Badge
                              tone={
                                candidate.status === "APPROVED"
                                  ? "emerald"
                                  : candidate.status === "REJECTED"
                                    ? "rose"
                                    : "amber"
                              }
                            >
                              {candidate.status}
                            </Badge>
                            <Badge tone="indigo">{candidate.category}</Badge>
                            <span>{candidate.source_item_count} items</span>
                            <span>{candidate.source_run_count} runs</span>
                            <span className="font-mono">cluster:{shortDigest(candidate.cluster_digest)}</span>
                          </div>
                          <span className="font-mono text-[11px] text-slate-400">
                            {candidate.public_id}
                          </span>
                        </div>
                        {candidate.status === "PENDING_REVIEW" ? (
                          <div className="mt-3 grid gap-2 lg:grid-cols-[minmax(0,1fr)_auto_auto]">
                            <input
                              aria-label={`${zh ? "Experience 候选评审意见" : "Experience candidate review comment"} ${candidate.public_id}`}
                              className="h-9 rounded-lg border border-slate-200 px-3 text-sm"
                              placeholder={zh ? "拒绝时至少 5 个字符" : "At least 5 characters when rejecting"}
                              value={experienceReviewComments[candidate.public_id] ?? ""}
                              onChange={(event) =>
                                onChangeExperienceReviewComment(
                                  candidate.public_id,
                                  event.target.value,
                                )
                              }
                            />
                            <Button
                              onClick={() => onReviewExperienceCandidate(candidate, "APPROVED")}
                              disabled={failureTaxonomyActionId === candidate.public_id}
                              icon={<CheckCircle2 className="h-4 w-4" />}
                            >
                              {zh ? "批准候选" : "Approve"}
                            </Button>
                            <Button
                              variant="secondary"
                              danger
                              onClick={() => onReviewExperienceCandidate(candidate, "REJECTED")}
                              disabled={failureTaxonomyActionId === candidate.public_id}
                              icon={<XCircle className="h-4 w-4" />}
                            >
                              {zh ? "拒绝候选" : "Reject"}
                            </Button>
                          </div>
                        ) : (
                          <div className="mt-2 text-xs text-slate-500">
                            {zh ? "最终评审" : "Final review"}: {candidate.review?.decision ?? candidate.status}
                            {candidate.review?.comment ? ` · ${candidate.review.comment}` : ""}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      </Card>

      <Card
        title={zh ? "Langfuse 候选策展" : "Langfuse candidate curation"}
        description={
          zh
            ? "按 Observation 元数据筛选，最多选择 20 条并冻结为待审核批次。"
            : "Filter Observation metadata, select up to 20, and freeze a review batch."
        }
        padded
      >
        <div className="space-y-4">
          <div className="rounded-lg border border-indigo-100 bg-indigo-50 px-4 py-3 text-sm text-indigo-800">
            {zh
              ? "候选检索不请求 input/output；批准入集时原始内容只在 Langfuse 内复制。DuckDock MySQL 仅保存引用、审核、Provider item、Manifest 摘要和不可变版本。"
              : "Candidate search never requests input/output. Approved content is copied only inside Langfuse; DuckDock stores governance metadata."}
          </div>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
            <label className="block text-sm font-medium text-slate-700">
              {zh ? "目标 Dataset" : "Target Dataset"}
              <select
                aria-label={zh ? "目标 Dataset" : "Target Dataset"}
                className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm"
                value={selectedDatasetId}
                onChange={(event) => onSelectDataset(event.target.value)}
              >
                {eligibleDatasets.map((dataset) => (
                  <option key={dataset.public_id} value={dataset.public_id}>
                    {dataset.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="block text-sm font-medium text-slate-700">
              {zh ? "时间窗口" : "Time window"}
              <select
                aria-label={zh ? "候选时间窗口" : "Candidate time window"}
                className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm"
                value={candidateWindowHours}
                onChange={(event) => onChangeCandidateWindowHours(event.target.value)}
              >
                <option value="1">{zh ? "最近 1 小时" : "Last hour"}</option>
                <option value="24">{zh ? "最近 24 小时" : "Last 24 hours"}</option>
                <option value="168">{zh ? "最近 7 天" : "Last 7 days"}</option>
              </select>
            </label>
            <label className="block text-sm font-medium text-slate-700">
              {zh ? "Observation 名称（精确）" : "Observation name (exact)"}
              <input
                aria-label={zh ? "候选 Observation 名称" : "Candidate Observation name"}
                className="mt-1 h-10 w-full rounded-lg border border-slate-200 px-3 text-sm"
                value={candidateName}
                onChange={(event) => onChangeCandidateName(event.target.value)}
                placeholder="duckdock.agent.run"
              />
            </label>
            <label className="block text-sm font-medium text-slate-700">
              {zh ? "环境" : "Environment"}
              <input
                aria-label={zh ? "候选环境" : "Candidate environment"}
                className="mt-1 h-10 w-full rounded-lg border border-slate-200 px-3 text-sm"
                value={candidateEnvironment}
                onChange={(event) => onChangeCandidateEnvironment(event.target.value)}
                placeholder="production"
              />
            </label>
            <label className="block text-sm font-medium text-slate-700">
              {zh ? "类型" : "Type"}
              <select
                aria-label={zh ? "候选 Observation 类型" : "Candidate Observation type"}
                className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm"
                value={candidateType}
                onChange={(event) => onChangeCandidateType(event.target.value)}
              >
                <option value="">{zh ? "全部" : "All"}</option>
                {["SPAN", "GENERATION", "AGENT", "TOOL", "CHAIN", "EVALUATOR"].map((value) => (
                  <option key={value} value={value}>{value}</option>
                ))}
              </select>
            </label>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-sm text-slate-600">
              <input
                type="checkbox"
                checked={candidateRootOnly}
                onChange={(event) => onChangeCandidateRootOnly(event.target.checked)}
              />
              {zh ? "只看根 Observation" : "Root Observations only"}
            </label>
            <Button
              variant="secondary"
              onClick={onSearchCandidates}
              disabled={!eligibleDatasets.length || candidateLoading}
              icon={<Search className={`h-4 w-4 ${candidateLoading ? "animate-pulse" : ""}`} />}
            >
              {candidateLoading ? (zh ? "正在检索…" : "Searching…") : (zh ? "检索候选" : "Search candidates")}
            </Button>
            <Button
              onClick={onSubmitCuration}
              disabled={!selectedCandidateRefs.size || curationSubmitting}
              icon={<ListChecks className="h-4 w-4" />}
            >
              {curationSubmitting
                ? zh ? "正在冻结批次…" : "Submitting…"
                : zh ? `提交审核（${selectedCandidateRefs.size}）` : `Submit for review (${selectedCandidateRefs.size})`}
            </Button>
            <span className="text-xs text-slate-500">
              {zh ? "已治理候选不可重复选择；批准前不会读取原始 IO。" : "Governed candidates cannot be selected; IO is not read before approval."}
            </span>
          </div>
          {candidates.length ? (
            <div className="max-h-[420px] space-y-2 overflow-y-auto rounded-lg border border-slate-200 p-2">
              {candidates.map((candidate) => {
                const ref = `${candidate.source_trace_ref}:${candidate.source_observation_ref}`;
                const selected = selectedCandidateRefs.has(ref);
                return (
                  <label
                    key={ref}
                    className={`flex cursor-pointer items-start gap-3 rounded-lg border px-3 py-3 ${
                      candidate.already_governed
                        ? "cursor-not-allowed border-slate-100 bg-slate-50 opacity-60"
                        : selected
                          ? "border-indigo-300 bg-indigo-50"
                          : "border-slate-200 bg-white hover:border-indigo-200"
                    }`}
                  >
                    <input
                      type="checkbox"
                      aria-label={`${zh ? "选择候选" : "Select candidate"} ${candidate.name}`}
                      className="mt-1"
                      checked={selected}
                      disabled={candidate.already_governed}
                      onChange={() => onToggleCandidate(candidate)}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-center gap-2">
                        <span className="font-medium text-slate-800">{candidate.name}</span>
                        <Badge tone="neutral">{candidate.observation_type}</Badge>
                        {candidate.environment ? <Badge tone="indigo">{candidate.environment}</Badge> : null}
                        {candidate.already_materialized ? <Badge tone="emerald">{zh ? "已入集" : "Materialized"}</Badge> : null}
                        {candidate.already_governed && !candidate.already_materialized ? <Badge tone="amber">{zh ? "已治理" : "Governed"}</Badge> : null}
                      </span>
                      <span className="mt-1 grid gap-1 text-xs text-slate-500 sm:grid-cols-2">
                        <span className="truncate font-mono">trace:{candidate.source_trace_ref}</span>
                        <span className="truncate font-mono">observation:{candidate.source_observation_ref}</span>
                        <span>{formatTime(candidate.start_time, locale)}</span>
                      </span>
                    </span>
                  </label>
                );
              })}
            </div>
          ) : null}
        </div>
      </Card>

      <Card
        title={zh ? "策展审核队列" : "Curation review queue"}
        description={zh ? `${curationBatches.length} 个不可变批次` : `${curationBatches.length} immutable batches`}
        padded
      >
        <div className="grid gap-3 lg:grid-cols-2">
          {curationBatches.map((batch) => {
            const dataset = datasets.find((value) => value.public_id === batch.dataset_public_id);
            const actionLoading = curationActionId === batch.public_id;
            const tone: Tone =
              batch.status === "MATERIALIZED"
                ? "emerald"
                : batch.status === "REJECTED"
                  ? "rose"
                  : batch.status === "APPROVED"
                    ? "indigo"
                    : "amber";
            return (
              <div key={batch.public_id} className="rounded-lg border border-slate-200 px-4 py-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <div className="font-medium text-slate-800">{dataset?.name ?? batch.dataset_public_id}</div>
                    <div className="mt-1 text-xs text-slate-500">
                      {batch.item_count} {zh ? "条来源" : "sources"} · {formatTime(batch.created_at, locale)}
                    </div>
                  </div>
                  <Badge tone={tone}>{batch.status}</Badge>
                </div>
                <div className="mt-3 space-y-1 text-xs text-slate-500">
                  {batch.items.map((item) => (
                    <div key={item.position} className="truncate font-mono">
                      {item.position}. {item.source_trace_ref}:{item.source_observation_ref}
                    </div>
                  ))}
                  <div className="font-mono">selection:{shortDigest(batch.selection_digest)}</div>
                  {batch.review ? <div className="font-mono">review:{shortDigest(batch.review.review_digest)}</div> : null}
                  {batch.materialization ? (
                    <div className="font-mono">
                      version:v{batch.materialization.dataset_version} · manifest:{shortDigest(batch.materialization.manifest_digest)} · {batch.materialization.dataset_item_count} items
                    </div>
                  ) : null}
                </div>
                {batch.status === "PENDING_REVIEW" ? (
                  <div className="mt-3 space-y-2">
                    <input
                      aria-label={`${zh ? "批次审核备注" : "Batch review comment"} ${batch.public_id}`}
                      className="h-9 w-full rounded-lg border border-slate-200 px-3 text-sm"
                      value={curationComments[batch.public_id] ?? ""}
                      onChange={(event) => onChangeCurationComment(batch.public_id, event.target.value)}
                      placeholder={zh ? "批准备注可选；拒绝原因至少 5 个字符" : "Optional approval note; rejection needs 5 characters"}
                    />
                    <div className="flex flex-wrap gap-2">
                      <Button
                        onClick={() => onReviewCuration(batch, "APPROVED")}
                        disabled={actionLoading}
                        icon={<CheckCircle2 className="h-4 w-4" />}
                      >
                        {actionLoading ? (zh ? "处理中…" : "Working…") : (zh ? "批准并批量入集" : "Approve & materialize")}
                      </Button>
                      <Button
                        variant="secondary"
                        onClick={() => onReviewCuration(batch, "REJECTED")}
                        disabled={actionLoading}
                        icon={<XCircle className="h-4 w-4" />}
                      >
                        {zh ? "拒绝" : "Reject"}
                      </Button>
                    </div>
                  </div>
                ) : null}
                {batch.status === "APPROVED" ? (
                  <div className="mt-3">
                    <Button
                      onClick={() => onMaterializeCuration(batch)}
                      disabled={actionLoading}
                      icon={<RefreshCw className={`h-4 w-4 ${actionLoading ? "animate-spin" : ""}`} />}
                    >
                      {zh ? "重试批量入集" : "Retry materialization"}
                    </Button>
                  </div>
                ) : null}
                {batch.review?.comment ? (
                  <div className="mt-3 rounded-md bg-slate-50 px-3 py-2 text-xs text-slate-600">
                    {batch.review.comment}
                  </div>
                ) : null}
              </div>
            );
          })}
          {!curationBatches.length ? (
            <EmptyState text={zh ? "还没有待审核的策展批次。" : "No curation batches yet."} />
          ) : null}
        </div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
        <Card
          title={zh ? "单条快速入集" : "Single-source quick add"}
          description={zh ? "保留兼容入口；无需审核，适合明确来源的运维操作。" : "Compatibility path for an explicitly known source."}
          padded
        >
          <div className="space-y-4">
            <label className="block text-sm font-medium text-slate-700">
              Trace ID
              <input
                aria-label="Trace ID"
                className="mt-1 h-10 w-full rounded-lg border border-slate-200 px-3 font-mono text-sm"
                value={traceId}
                onChange={(event) => onChangeTrace(event.target.value)}
                placeholder="32 hex"
              />
            </label>
            <label className="block text-sm font-medium text-slate-700">
              Observation ID
              <input
                aria-label="Observation ID"
                className="mt-1 h-10 w-full rounded-lg border border-slate-200 px-3 font-mono text-sm"
                value={observationId}
                onChange={(event) => onChangeObservation(event.target.value)}
                placeholder={zh ? "可选；留空选择唯一根节点" : "Optional; blank selects the unique root"}
              />
            </label>
            <Button
              onClick={onSubmit}
              disabled={!eligibleDatasets.length || materializing}
              icon={<GitFork className="h-4 w-4" />}
            >
              {materializing
                ? zh ? "正在写入并固定版本…" : "Materializing…"
                : zh ? "写入 Dataset 并固定版本" : "Add to Dataset and pin version"}
            </Button>
          </div>
        </Card>

        <Card
          title={zh ? "单条不可变入集记录" : "Single-source materializations"}
          description={zh ? `${materializations.length} 次兼容写入` : `${materializations.length} compatibility writes`}
          padded
        >
          <div className="space-y-3">
            {materializations.map((item) => {
              const dataset = datasets.find((value) => value.public_id === item.dataset_public_id);
              return (
                <div key={item.public_id} className="rounded-lg border border-slate-200 px-4 py-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="font-medium text-slate-800">{dataset?.name ?? item.dataset_public_id}</div>
                    <Badge tone="indigo">v{item.dataset_version} · {item.item_count} items</Badge>
                  </div>
                  <div className="mt-2 grid gap-1 text-xs text-slate-500">
                    <span className="font-mono">trace:{item.source_trace_ref}</span>
                    <span className="font-mono">observation:{item.source_observation_ref}</span>
                    <span className="font-mono">manifest:{shortDigest(item.manifest_digest)}</span>
                    <span>{formatTime(item.created_at, locale)}</span>
                  </div>
                </div>
              );
            })}
            {!materializations.length ? (
              <EmptyState text={zh ? "还没有单条入集记录。" : "No single-source materializations yet."} />
            ) : null}
          </div>
        </Card>
      </div>
    </div>
  );
}

function OverviewPanel({
  zh,
  datasets,
  evaluations,
  comparisons,
  bindings,
  onOpenComparisons,
  onOpenRelease,
}: {
  zh: boolean;
  datasets: Array<{ public_id: string; name: string; provider: string; versions: unknown[] }>;
  evaluations: Array<{ public_id: string; status: string; result_completeness: string | null }>;
  comparisons: EvaluationComparison[];
  bindings: ReleaseCandidateEvaluationBinding[];
  onOpenComparisons: () => void;
  onOpenRelease: () => void;
}) {
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card
        title={zh ? "评测资产" : "Evaluation assets"}
        description={zh ? "来自 provider 的输入与执行结果" : "Provider inputs and execution results"}
        padded
      >
        <div className="space-y-3">
          {datasets.slice(0, 4).map((dataset) => (
            <div key={dataset.public_id} className="flex items-center justify-between gap-3 rounded-lg border border-slate-200 px-4 py-3">
              <div>
                <div className="font-medium text-slate-800">{dataset.name}</div>
                <div className="mt-1 text-xs text-slate-500">{dataset.provider}</div>
              </div>
              <Badge tone="indigo">{dataset.versions.length} versions</Badge>
            </div>
          ))}
          {!datasets.length ? <EmptyState text={zh ? "暂无评测数据集。" : "No datasets yet."} /> : null}
          <div className="border-t border-slate-100 pt-3 text-sm text-slate-500">
            {zh ? "执行结果" : "Runs"}: {evaluations.length} ·{" "}
            {zh ? "完整结果" : "complete"}:{" "}
            {evaluations.filter((item) => item.result_completeness === "COMPLETE").length}
          </div>
        </div>
      </Card>

      <Card
        title={zh ? "发布治理闭环" : "Release governance loop"}
        description={zh ? "比较 → 精确绑定 → 人工决定 → 门禁摘要" : "Compare → bind → review → gate digest"}
        padded
      >
        <div className="grid gap-3 sm:grid-cols-3">
          <OverviewStep label={zh ? "对比" : "Compare"} value={comparisons.length} />
          <OverviewStep label={zh ? "候选绑定" : "Bindings"} value={bindings.length} />
          <OverviewStep
            label={zh ? "最终评审" : "Reviews"}
            value={bindings.filter((item) => item.review !== null).length}
          />
        </div>
        <div className="mt-5 flex flex-wrap gap-2">
          <Button variant="secondary" onClick={onOpenComparisons}>
            {zh ? "查看基线对比" : "View comparisons"}
          </Button>
          <Button onClick={onOpenRelease}>{zh ? "进入候选门禁" : "Open release gates"}</Button>
        </div>
      </Card>
    </div>
  );
}

function OverviewStep({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-4">
      <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-2 text-2xl font-bold text-slate-900">{value}</div>
    </div>
  );
}

function ComparisonPanel({
  zh,
  locale,
  comparisons,
}: {
  zh: boolean;
  locale: string;
  comparisons: EvaluationComparison[];
}) {
  if (!comparisons.length) {
    return (
      <Card padded>
        <EmptyState text={zh ? "暂无可复现的基线对比。" : "No reproducible comparisons yet."} />
      </Card>
    );
  }
  return (
    <div className="space-y-4">
      {comparisons.map((comparison) => (
        <Card
          key={comparison.public_id}
          title={
            <div className="flex flex-wrap items-center gap-2">
              <span>{comparison.candidate.target_ref}</span>
              <Badge tone={comparisonTone[comparison.outcome]}>{comparison.outcome}</Badge>
            </div>
          }
          description={<MonoPill>{comparison.public_id}</MonoPill>}
          action={<span className="text-xs text-slate-400">{formatTime(comparison.created_at, locale)}</span>}
        >
          <div className="grid gap-4 p-5 md:grid-cols-[1fr_auto_1fr]">
            <ComparisonSide
              label={zh ? "基线" : "Baseline"}
              target={comparison.baseline.target_ref}
              score={comparison.baseline.score}
              passRate={comparison.baseline.pass_rate}
              manifest={comparison.baseline.manifest_public_id}
            />
            <div className="flex flex-row items-center justify-center gap-2 text-xs text-slate-500 md:flex-col">
              <GitCompareArrows className="h-5 w-5 text-indigo-500" />
              <span>{formatDelta(comparison.score_delta)}</span>
            </div>
            <ComparisonSide
              label={zh ? "候选" : "Candidate"}
              target={comparison.candidate.target_ref}
              score={comparison.candidate.score}
              passRate={comparison.candidate.pass_rate}
              manifest={comparison.candidate.manifest_public_id}
            />
          </div>
          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 px-5 py-4 text-xs text-slate-500">
            <span>
              {zh ? "策略" : "Policy"}: {comparison.policy.policy_name} v{comparison.policy.version} ·{" "}
              {comparison.reason_code}
            </span>
            <span className="font-mono" title={comparison.reproducibility_digest}>
              digest:{shortDigest(comparison.reproducibility_digest)}
            </span>
          </div>
        </Card>
      ))}
    </div>
  );
}

function ComparisonSide({
  label,
  target,
  score,
  passRate,
  manifest,
}: {
  label: string;
  target: string;
  score: number | null;
  passRate: number | null;
  manifest: string;
}) {
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50 p-4">
      <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-2 break-all text-sm font-semibold text-slate-800">{target}</div>
      <div className="mt-4 grid grid-cols-2 gap-3 text-sm">
        <div>
          <div className="text-xs text-slate-500">Score</div>
          <div className="mt-1 text-lg font-bold text-slate-900">{formatPercent(score)}</div>
        </div>
        <div>
          <div className="text-xs text-slate-500">Pass rate</div>
          <div className="mt-1 text-lg font-bold text-slate-900">{formatPercent(passRate)}</div>
        </div>
      </div>
      <div className="mt-3">
        <MonoPill>{manifest}</MonoPill>
      </div>
    </div>
  );
}

function ReleasePanel({
  zh,
  locale,
  bindings,
  reviewDraft,
  reviewing,
  gateLoading,
  gateDecisions,
  onOpenReview,
  onChangeComment,
  onCancelReview,
  onSubmitReview,
  onEvaluateGate,
}: {
  zh: boolean;
  locale: string;
  bindings: ReleaseCandidateEvaluationBinding[];
  reviewDraft: ReviewDraft | null;
  reviewing: boolean;
  gateLoading: string | null;
  gateDecisions: Record<string, ReleaseCandidateGateDecision>;
  onOpenReview: (
    binding: ReleaseCandidateEvaluationBinding,
    decision: ReleaseCandidateReviewDecision,
  ) => void;
  onChangeComment: (comment: string) => void;
  onCancelReview: () => void;
  onSubmitReview: () => void;
  onEvaluateGate: (binding: ReleaseCandidateEvaluationBinding) => void;
}) {
  if (!bindings.length) {
    return (
      <Card padded>
        <EmptyState text={zh ? "暂无候选版本评测绑定。" : "No candidate evaluation bindings yet."} />
      </Card>
    );
  }
  return (
    <div className="space-y-4">
      {bindings.map((binding) => {
        const gate = gateDecisions[binding.public_id];
        const draftOpen = reviewDraft?.binding.public_id === binding.public_id;
        return (
          <Card
            key={binding.public_id}
            title={
              <div className="flex flex-wrap items-center gap-2">
                <span>{binding.release_candidate_ref}</span>
                <Badge tone={comparisonTone[binding.comparison_outcome]}>
                  {binding.comparison_outcome}
                </Badge>
                {binding.review ? (
                  <Badge tone={binding.review.decision === "APPROVED" ? "emerald" : "rose"}>
                    {binding.review.decision}
                  </Badge>
                ) : (
                  <Badge tone="amber" icon={<Clock3 className="h-3.5 w-3.5" />}>
                    {zh ? "待人工评审" : "REVIEW PENDING"}
                  </Badge>
                )}
              </div>
            }
            description={<MonoPill>{binding.public_id}</MonoPill>}
            action={<span className="text-xs text-slate-400">{formatTime(binding.created_at, locale)}</span>}
          >
            <div className="grid gap-4 p-5 lg:grid-cols-[1fr_1fr_auto]">
              <ReleaseInfo
                title={zh ? "精确部署" : "Exact deployment"}
                rows={[
                  [zh ? "Deployment" : "Deployment", binding.deployment_public_id],
                  [zh ? "Revision" : "Revision", binding.deployment_revision],
                  [zh ? "配置摘要" : "Config digest", shortDigest(binding.deployment_configuration_digest)],
                ]}
              />
              <ReleaseInfo
                title={zh ? "评测证据" : "Evaluation evidence"}
                rows={[
                  [zh ? "对比" : "Comparison", binding.evaluation_comparison_public_id],
                  [zh ? "候选评测" : "Candidate eval", binding.candidate_evaluation_public_id],
                  [zh ? "绑定摘要" : "Binding digest", shortDigest(binding.binding_digest)],
                ]}
              />
              <div className="flex min-w-44 flex-col items-stretch justify-center gap-2">
                {!binding.review ? (
                  <>
                    <Button
                      size="sm"
                      icon={<CheckCircle2 className="h-4 w-4" />}
                      onClick={() => onOpenReview(binding, "APPROVED")}
                    >
                      {zh ? "批准证据" : "Approve evidence"}
                    </Button>
                    <Button
                      size="sm"
                      variant="secondary"
                      danger
                      icon={<XCircle className="h-4 w-4" />}
                      onClick={() => onOpenReview(binding, "REJECTED")}
                    >
                      {zh ? "拒绝候选" : "Reject candidate"}
                    </Button>
                  </>
                ) : (
                  <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600">
                    <div className="font-semibold">{zh ? "最终决定" : "Final decision"}</div>
                    <div className="mt-1 font-mono" title={binding.review.review_digest}>
                      {shortDigest(binding.review.review_digest)}
                    </div>
                  </div>
                )}
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={gateLoading === binding.public_id}
                  onClick={() => onEvaluateGate(binding)}
                  icon={
                    <RefreshCw
                      className={`h-4 w-4 ${gateLoading === binding.public_id ? "animate-spin" : ""}`}
                    />
                  }
                >
                  {zh ? "复算门禁" : "Evaluate gate"}
                </Button>
              </div>
            </div>

            {draftOpen && reviewDraft ? (
              <div className="border-t border-slate-200 bg-slate-50 px-5 py-4">
                <div className="flex items-center gap-2 text-sm font-semibold text-slate-800">
                  {reviewDraft.decision === "APPROVED" ? (
                    <CheckCircle2 className="h-4 w-4 text-emerald-600" />
                  ) : (
                    <XCircle className="h-4 w-4 text-rose-600" />
                  )}
                  {reviewDraft.decision === "APPROVED"
                    ? zh
                      ? "确认批准为最终证据"
                      : "Approve as final evidence"
                    : zh
                      ? "确认拒绝并阻断候选"
                      : "Reject and block candidate"}
                </div>
                <textarea
                  value={reviewDraft.comment}
                  onChange={(event) => onChangeComment(event.target.value)}
                  rows={3}
                  maxLength={1000}
                  aria-label={zh ? "评审备注" : "Review comment"}
                  placeholder={
                    reviewDraft.decision === "REJECTED"
                      ? zh
                        ? "拒绝原因（至少 5 个字符，写入审计）"
                        : "Rejection reason (at least 5 characters)"
                      : zh
                        ? "评审备注（可选）"
                        : "Review note (optional)"
                  }
                  className="mt-3 w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-indigo-300 focus:ring-4 focus:ring-indigo-100"
                />
                <div className="mt-3 flex gap-2">
                  <Button
                    size="sm"
                    danger={reviewDraft.decision === "REJECTED"}
                    disabled={reviewing}
                    onClick={onSubmitReview}
                  >
                    {reviewing
                      ? zh
                        ? "写入中…"
                        : "Saving…"
                      : reviewDraft.decision === "APPROVED"
                        ? zh
                          ? "确认批准"
                          : "Confirm approval"
                        : zh
                          ? "确认拒绝"
                          : "Confirm rejection"}
                  </Button>
                  <Button size="sm" variant="ghost" disabled={reviewing} onClick={onCancelReview}>
                    {zh ? "取消" : "Cancel"}
                  </Button>
                </div>
              </div>
            ) : null}

            {binding.review?.comment ? (
              <div className="border-t border-slate-200 px-5 py-4 text-sm text-slate-600">
                <span className="font-semibold text-slate-700">{zh ? "评审备注：" : "Review note: "}</span>
                {binding.review.comment}
              </div>
            ) : null}

            {gate ? <GateDecisionView gate={gate} zh={zh} /> : null}
          </Card>
        );
      })}
    </div>
  );
}

function ReleaseInfo({
  title,
  rows,
}: {
  title: string;
  rows: Array<[string, string]>;
}) {
  return (
    <div>
      <div className="text-sm font-semibold text-slate-800">{title}</div>
      <dl className="mt-3 space-y-2 text-xs">
        {rows.map(([label, value]) => (
          <div key={label} className="flex items-start justify-between gap-3">
            <dt className="text-slate-500">{label}</dt>
            <dd className="max-w-[65%] break-all text-right font-mono text-slate-700">{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function GateDecisionView({ gate, zh }: { gate: ReleaseCandidateGateDecision; zh: boolean }) {
  return (
    <div
      className={`border-t px-5 py-4 ${
        gate.outcome === "PASS"
          ? "border-emerald-200 bg-emerald-50"
          : "border-rose-200 bg-rose-50"
      }`}
      data-testid="gate-decision"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Badge tone={gate.outcome === "PASS" ? "emerald" : "rose"}>{gate.outcome}</Badge>
          <span className="text-sm font-medium text-slate-700">
            {gate.reason_codes.join(", ")}
          </span>
        </div>
        <span className="font-mono text-xs text-slate-500" title={gate.decision_digest}>
          {zh ? "决策摘要" : "Decision digest"}: {shortDigest(gate.decision_digest)}
        </span>
      </div>
      <div className="mt-2 text-xs text-slate-500">
        {zh ? "证据数量" : "Evidence"}: {gate.evidence_count} ·{" "}
        {gate.evidence.map((item) => `${item.evidence_kind}:${item.verdict}`).join(" · ")}
      </div>
    </div>
  );
}
