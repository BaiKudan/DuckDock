# DuckDock Evaluation Hub — Langfuse-first vertical slice

Status: Implemented (EH-01～EH-06 + NEXT-007 Trace2Dataset + NEXT-008
governed curation queue + NEXT-009 reproducible production sampling + NEXT-010
decoupled Annotation Queue synchronization + NEXT-011 annotation-driven
Promotion recommendation + NEXT-012 cross-batch Golden/Bad Case routing +
NEXT-013 versioned failure taxonomy/Experience candidate extraction + NEXT-014
Experience assets/four-eyes activation + NEXT-015 provider-neutral semantic
failure clustering + NEXT-016 semantic cluster quality/drift regression +
NEXT-017 scheduled semantic drift monitoring/in-app alerts), 2026-08-04

## Purpose

DuckDock owns evaluation governance and release provenance. Langfuse owns
dataset items, experiment traces, observation data and provider-native score
details. DeepEval is an optional execution engine, not a DuckDock storage
dependency.

## Implemented contract

- `EvaluationDataset` and immutable `EvaluationDatasetVersion`
  - Namespace-scoped identity.
  - Optional Langfuse dataset synchronization.
  - Version stores only SHA-256 digest, item count, schema identity and an
    opaque provider version reference.
- `EvaluationDatasetMaterialization`
  - Records one immutable, idempotent Trace2Dataset write from a Langfuse
    Trace/Observation into a synchronized Langfuse Dataset.
  - The provider adapter reads raw v4 Observation `io` ephemerally and writes
    it directly back to Langfuse as Dataset `input`/`expected_output`; raw
    content is never returned to DuckDock core or persisted in MySQL.
  - Uses a deterministic provider item UUID, exact source linkage, a
    provider-index manifest digest and `updated_at + 1 ms` Dataset pin.
  - Stores only source/provider references, request digest, schema identity,
    actor and the resulting immutable DatasetVersion.
- Governed Trace2Dataset curation
  - Queries Langfuse Observation candidates with metadata-only
    `core,basic,time`; candidate responses never contain input or output.
  - Supports exact observation-name, environment, observation-type, root-only
    and bounded time-window filters.
  - Records an immutable, idempotent `EvaluationDatasetCurationBatch` with
    one to twenty explicitly selected Trace/Observation source references and
    a canonical selection digest.
  - Records a separate immutable APPROVED or REJECTED review. A rejected or
    unreviewed batch cannot be materialized.
  - Materializes every approved source inside the provider adapter, creates
    one deterministic Langfuse Dataset item per source, and acknowledges the
    batch as exactly one immutable DuckDock DatasetVersion.
  - Keeps the batch, review and materialization receipts independent so that
    approval provenance is retained across safe idempotent retries.
- Reproducible production sampling
  - Stores a Namespace-scoped `EvaluationSamplingPolicy` and immutable,
    digest-addressed policy versions. Every version pins exact metadata-only
    filters, stable-hash strategy, target/minimum sizes, candidate bound and
    schema identity.
  - Requires an explicit policy version, Dataset and bounded time window for
    every run. It never resolves a mutable `latest` policy alias.
  - Ranks source references deterministically and records an immutable run
    snapshot containing only counts, references, rank/selection/run digests
    and the exact generated curation batch.
  - Always excludes sources already submitted, reviewed, materialized or
    directly added to the Dataset. A run below its configured minimum fails
    closed without creating partial governance records.
  - Automatically creates a `PENDING_REVIEW` curation batch; sampling never
    bypasses the existing independent human review/materialization boundary.
- Decoupled Langfuse Annotation Queue synchronization
  - Discovers existing provider queues and binds one exact queue plus an
    immutable Score Config ID snapshot to a Namespace. DuckDock never creates
    a remote queue inside its governance transaction.
  - Persists a durable dispatch intent for one exact curation batch before any
    provider call. Beat/Worker lease, bounded attempts and exponential backoff
    make provider outage recoverable.
  - Reconciles existing Observation items before create, so a crash after a
    provider success but before local acknowledgement does not create a
    duplicate item. One binding/batch and request digest are unique in MySQL.
  - Fails closed if the provider queue's Score Config IDs drift from the bound
    snapshot. Queue deletion or malformed provider responses are sanitized as
    provider failures.
  - Stores only queue/item/Observation references, counts, status and digests.
    Annotation values, corrections and score detail remain in Langfuse.
  - Reconciles Langfuse `PENDING`/`COMPLETED` progress independently.
    `COMPLETED` never creates a DuckDock APPROVED review or DatasetVersion.
- Annotation-driven Promotion recommendation
  - Stores a Namespace-scoped `EvaluationPromotionPolicy` and immutable,
    digest-addressed versions. Every version pins one exact Annotation Queue
    binding, Score Config ID/data type, typed quality rule and optional
    metadata-only diversity dimension/minimum bucket count.
  - Accepts only a fully synchronized, 100% `COMPLETED` dispatch whose Queue
    and Score Config still match the frozen version. Missing scores, failed
    quality, missing diversity metadata and insufficient diversity all fail
    closed with explicit reason codes.
  - Reads Langfuse Scores API v3 using exact `ANNOTATION` source, queue,
    config and Trace/Observation subject filters. Numeric, boolean and
    categorical rules are evaluated ephemerally inside the provider adapter.
  - Stores one immutable/idempotent run with only `RECOMMENDED` or `BLOCKED`,
    bounded counts, reason codes, evidence digests and per-item booleans/hash
    evidence. Raw score values, annotation comments and corrections stay in
    Langfuse.
  - A `RECOMMENDED` outcome is advisory. It never creates a curation review,
    materialization or DatasetVersion; the existing human approval boundary
    remains independent.
- Cross-batch Golden/Bad Case routing
  - Stores a Namespace-scoped `EvaluationCaseRoutingPolicy` and immutable,
    digest-addressed versions. Each version pins one exact bucketed Promotion
    policy version plus Golden/Bad Case target and minimum sizes.
  - Accepts one to twenty non-overlapping Promotion runs from that exact
    version. Quality-pass items become Golden candidates, scored quality
    failures become Bad Case candidates, and missing score/bucket evidence is
    recorded as `EXCLUDED`.
  - Uses deterministic cluster round-robin over the already persisted
    `diversity_bucket_digest`; it never rereads raw Langfuse scores or provider
    content and never silently substitutes another Promotion version.
  - Fails closed atomically when either minimum cannot be met. A `BLOCKED`
    run creates no partial batch; a `ROUTED` run creates two existing
    `PENDING_REVIEW` curation batches with non-overlapping selections.
  - Golden and Bad Case review/materialization remain independent. Routing
    cannot create an APPROVED review or a DatasetVersion.
- Versioned failure taxonomy and Experience candidate extraction
  - Stores a Namespace-scoped `EvaluationFailureTaxonomyPolicy` and immutable,
    digest-addressed versions. Each version pins one exact Case Routing policy
    version, recurrence thresholds, isolated-case inclusion and candidate cap.
  - Accepts only a `ROUTED` run from the pinned version and consumes only its
    selected Bad Case lane, frozen cluster digest, source references and reason
    codes. It never calls Langfuse or reads raw Observation/annotation content.
  - Classifies each cluster deterministically as `CROSS_RUN_RECURRING`,
    `SINGLE_RUN_RECURRING` or `ISOLATED`. Isolated clusters are excluded by
    default; a version must opt in explicitly.
  - Stores an immutable/idempotent extraction run and exact per-candidate
    lineage to the routed items. No eligible cluster produces a `BLOCKED` run,
    not a synthetic Experience.
  - Every extracted candidate starts `PENDING_REVIEW`. One immutable human
    `APPROVED` or `REJECTED` decision is allowed; approval confirms only the
    candidate and cannot modify or deploy Prompt, Skill, Memory or Agent state.
- Provider-neutral semantic failure clustering
  - Stores a Namespace-scoped `EvaluationSemanticClusteringPolicy` and
    immutable, digest-addressed versions. Each version pins one exact Case
    Routing version, embedding profile/model/dimensions, cosine threshold,
    minimum cluster size and bounded input limits.
  - Accepts only one `ROUTED` run from the exact pinned routing version and
    embeds its selected Bad Case Observation sources through a provider port.
    The default adapter reads bounded Langfuse IO and calls an independently
    configured OpenAI-compatible embedding endpoint.
  - Normalizes vectors and performs deterministic centroid/cosine clustering
    in the provider-neutral service. Runs are immutable and idempotent by exact
    version, routing run and evidence digest; provider errors fail closed.
  - Persists only source references, content/embedding digests, semantic
    cluster digest, cluster size and centroid similarity. Observation content
    and embedding vectors remain ephemeral and never enter MySQL, Audit,
    Outbox or API responses.
  - Failure Taxonomy may optionally pin one semantic version. When pinned, an
    extraction must also pin the matching semantic run and consumes its exact
    cluster membership; omitting the pin preserves the existing metadata-only
    compatibility path.
- Semantic cluster quality and drift regression
  - Stores a Namespace-scoped `EvaluationSemanticRegressionPolicy` and
    immutable threshold versions. Every version freezes minimum pairwise
    assignment agreement plus maximum cluster-count change, eligible-cluster
    ratio drop and mean centroid-similarity drop.
  - Every comparison explicitly pins different baseline/candidate semantic
    runs and one exact regression policy version. Both runs must reference the
    same Case Routing run and exact Trace/Observation source set.
  - Exact content digests are compared before assignment metrics. Changed
    content or a non-clustered baseline produces `INCONCLUSIVE`; a blocked
    candidate or breached metric produces `DRIFTED`; only a fully comparable
    candidate inside all frozen thresholds returns `PASS`.
  - Pairwise co-assignment agreement is calculated over all source pairs, so
    cluster digest labels may change without creating a false drift result.
    The comparison also records normalized cluster-count change, eligible
    ratio drop and mean centroid-similarity drop.
  - Comparisons are immutable and idempotent by exact baseline/candidate/policy
    pins. They consume only DuckDock's content-free semantic receipts and do
    not call Langfuse or the embedding provider.
- Scheduled semantic drift monitoring and in-app alerts
  - Stores a Namespace-scoped monitor that immutably pins one `CLUSTERED`
    baseline run, one candidate semantic-clustering policy version, one
    semantic-regression policy version and a bounded interval. The candidate
    policy must pin the baseline run's exact Case Routing policy version; no
    mutable `latest` alias is resolved.
  - Celery Beat is the single scheduler and only persists/dispatches durable
    due-run receipts. A leased Worker repeats clustering over the baseline's
    exact frozen Case Routing run with the pinned candidate policy and then
    creates one immutable comparison against the exact baseline.
  - Repeated observations are intentionally allowed to produce independent
    clustering receipts even when their evidence digest is unchanged. Public
    manual clustering keeps duplicate-evidence rejection; monitor execution
    enables repetition only through its private service seam, while Namespace
    idempotency keys still make every scheduled receipt replay-safe.
  - Each monitor run has bounded attempts, lease recovery and exponential
    backoff. `PASS` completes without an alert; `DRIFTED` opens a `CRITICAL`
    alert; `INCONCLUSIVE` or a final sanitized execution failure opens a
    `WARNING` alert.
  - Alerts are DuckDock in-app governance records with one final acknowledgement
    and an optional bounded operator note. Monitor pause/resume and manual
    run-now affect scheduling only; they never change Agent, Prompt, Skill,
    Memory, Experience activation, Deployment or Release Gate state.
  - Monitor, run and alert API/Audit/Outbox payloads contain only exact pins,
    states, reason codes, aggregate outcome and digests. External notification
    connectors are outside NEXT-017.
- Versioned Experience assets and independent activation
  - One provider-neutral `EvaluationExperienceAsset` may be created from one
    approved candidate. Candidate and asset must share a Namespace; pending or
    rejected candidates fail closed, and a candidate cannot be claimed twice.
  - Operators author bounded `body`, `applicability` and optional change
    summary fields in immutable, digest-addressed versions. The content is
    DuckDock-authored governance data, never generated from or copied out of
    Langfuse Observation IO.
  - Only a `DRAFT` version may submit one immutable activation request. A final
    reviewer must be different from both the version author and activation
    requester; rejection requires a reason and every decision is final.
  - Approval marks the exact version `ACTIVE` and atomically retires a prior
    active version of the same asset. This status is a DuckDock control-plane
    designation only: it does not write Langfuse, Hermes, Prompt, Skill,
    Memory, Deployment or Release Gate state.
  - Audit and Outbox contain only identifiers, lifecycle state and SHA-256
    digests. Experience body, applicability, request note and review comment
    stay out of those propagation paths.
- `Evaluator` and immutable `EvaluatorVersion`
  - Provider-neutral kind/provider identity.
  - Version stores only configuration digest and an opaque implementation
    reference such as `deepeval://answer_relevancy`.
- `Experiment`
  - Pins one exact dataset version and one exact target digest.
  - Stores an opaque Langfuse experiment reference when the external runner
    has created it.
- `Evaluation`
  - Pins one exact evaluator version inside an Experiment.
  - Stores bounded summary score/counts, state, timestamps and an opaque
    provider evaluation reference.
  - Uses a Namespace-scoped execution key for idempotent creation and
    dispatch.
- `EvaluationResultManifest`
  - Adds an immutable, versioned metadata index for each provider-hosted
    result set.
  - Stores only provider Dataset/Experiment references, schema identity,
    SHA-256 digest, bounded counts, completeness and a safe loss reason.
  - Separates expected, processed, scored, passed, failed and error counts so
    missing items/scores are not misclassified as quality failures.
  - A fully observed run is `COMPLETED/COMPLETE`; a provider result gap is
    `PARTIAL/PARTIAL`. Retrying a partial Evaluation preserves all prior
    manifests and creates a new manifest version on acknowledgement.
- `RegressionPolicy` and immutable `RegressionPolicyVersion`
  - Namespace-scoped policy identity with immutable, digest-addressed
    threshold versions.
  - Supports a candidate score floor plus maximum aggregate-score and
    pass-rate drops.
  - Requires complete baseline and candidate results; partial results never
    become an accidental pass or regression.
- `EvaluationComparison`
  - Requires explicit baseline/candidate Evaluation and result-manifest IDs
    plus an explicit policy-version ID.
  - Accepts only the same DatasetVersion, EvaluatorVersion, target type,
    provider Dataset and result schema.
  - Produces `PASS`, `REGRESSION` or `INCONCLUSIVE`, bounded deltas and
    individual policy-breach flags.
  - Pins Dataset/Evaluator/Target/Provider/Result/Policy identities into a
    canonical SHA-256 reproducibility digest.
  - Remains stable when a compared Evaluation is later retried because all
    metrics are read from the selected immutable result manifest.
- `ReleaseCandidateEvaluationBinding`
  - Binds one exact release-candidate reference, Deployment public ID/revision
    and immutable `EvaluationComparison`.
  - Requires the comparison's candidate target reference and digest to match
    the selected candidate and Deployment configuration digest.
  - Snapshots Deployment identity/configuration plus comparison identity,
    outcome and reproducibility digest into a canonical binding digest.
  - Makes a REGISTERED Deployment revision immutable after formal evaluation
    evidence has been bound.
- Candidate Release Gate
  - Reads evidence only through the exact Foundation selector; it never
    queries the latest Namespace Evaluation or newest Deployment.
  - Returns `PASS` only when at least one bound comparison exists and every
    comparison passed.
  - Missing, `REGRESSION` and `INCONCLUSIVE` evidence fail closed as `BLOCKED`
    with explicit reason codes and a reproducible decision digest.
  - Is isolated from the legacy Skill/Clinic Gate, whose compatibility
    behavior remains unchanged.
- Evaluation execution
  - Transactional `EvaluationQueued` Outbox events and periodic recovery
    dispatch use the same lease-protected Worker path.
  - Row-locked leases, heartbeats and expiry reclaim prevent concurrent
    workers from acknowledging the same Evaluation.
  - Failed attempts use bounded exponential backoff; exhausted attempts become
    terminal and an administrator can explicitly retry a failed Evaluation.
  - Pending cancellation is immediate. Running cancellation is cooperative:
    lease renewal stops and any later Worker acknowledgement transitions the
    Evaluation to `CANCELLED` without accepting its result.
  - Failed and partial Evaluations can be explicitly retried. Operational
    summary fields reset, while immutable prior result manifests remain.
- Langfuse Experiment runner
  - Loads an exact provider-hosted Dataset version instead of the latest
    Dataset state.
  - Uses the Langfuse v4 high-level Experiment SDK for task execution,
    per-item evaluator scoring and provider-native experiment traces.
  - The first governed target is `dataset-replay`; target execution remains
    behind a provider-neutral port for later Agent/Release Candidate targets.
  - `rule://exact_match` is type-strict. `deepeval://<metric>@<threshold>`
    uses the optional DeepEval adapter.
  - Returns provider identity, aggregate score, explicit completeness counts
    and a canonical result-reference digest to MySQL. Dataset content, output
    and item-level scores stay in Langfuse.
  - The digest can be independently reconstructed from Langfuse Experiment
    Items `core,scores` data without requesting `io`.
- DeepEval adapter
  - Imports the optional SDK outside the core service layer.
  - Disables DeepEval dotenv auto-loading.
  - Accepts content only as an ephemeral in-memory case and returns metric
    summaries without reasons or case content.

## Security and tenancy

- MySQL does not store provider dataset items, prompt text, model output, trace
  content, metric reasons or tool payloads. It may store bounded,
  operator-authored Experience body/applicability after a candidate is
  approved; those fields are not provider content and never enter Audit or
  Outbox.
- Trace2Dataset API, audit and `EvaluationDatasetMaterialized` Outbox payloads
  contain only references, versions, counts and digests. The provider adapter
  enforces a 1 MiB bound per selected input/output and never logs raw values.
- Candidate, curation-batch, review and batch-materialization APIs, audit rows
  and Outbox events contain only filters, source references, decisions,
  versions, counts and digests. Raw provider content crosses only the
  Langfuse adapter's bounded in-memory copy path.
- Sampling policy/run APIs, Audit rows and `EvaluationSamplingRunCreated`
  events are content-free allowlists. They contain filters, identifiers,
  bounded counts and digests only; sampling does not request provider IO.
- Annotation Queue binding/dispatch APIs, Audit rows and Outbox events use
  metadata-only allowlists. Dispatch items contain Observation/provider item
  references but never annotation values, corrections, input or output.
- Promotion policy/run APIs, Audit rows and Outbox events contain only exact
  pins, typed rule metadata, booleans, bounded counts, reason codes and
  digests. Promotion run items have no score-value/comment/correction column.
- Case-routing APIs, Audit rows and `EvaluationCaseRoutingRunCreated` events
  contain only exact pins, source references, lane/selection booleans, bounded
  counts, reason codes and SHA-256 digests. They consume persisted Promotion
  evidence and add no new Langfuse API or database dependency.
- Semantic-clustering APIs, Audit rows and
  `EvaluationSemanticClusteringRunCreated` events contain only exact pins,
  source references, bounded counts, cosine summaries and SHA-256 digests.
  Raw Observation content and embedding vectors are forbidden persistence
  fields and never cross the provider-port result boundary into the API.
- Semantic-regression APIs, Audit rows and
  `EvaluationSemanticRegressionComparisonCreated` events contain only exact
  run/policy pins, aggregate metrics, breach flags, reason codes and a
  reproducibility digest. They never contain item arrays, provider content or
  embedding vectors.
- Semantic-monitor APIs, Audit rows and Outbox events contain only exact
  policy/run pins, cadence, timestamps, lifecycle states, reason codes and
  sanitized error codes. An alert acknowledgement propagates only actor,
  timestamp and status; its free-text note is not copied to Audit or Outbox.
- Result manifest APIs and Outbox events use strict metadata allowlists; no
  input, output, expected output or item array is exposed.
- Comparison creation is idempotent by Namespace and exact pin tuple.
  “latest” is never resolved as a manifest or policy alias.
- `EvaluationComparisonCreated` contains only immutable identifiers, bounded
  metrics, breach flags and the reproducibility digest.
- Release candidate references and Deployment revisions reject
  `latest`/`newest`/`current` aliases. Binding creation is idempotent by
  Namespace and exact pin tuple.
- `ReleaseCandidateEvaluationBound` contains only immutable identifiers,
  outcome and digests; it contains no prompt, output, item or trace content.
- All registry resources are Namespace-scoped.
- Cross-Namespace dataset/evaluator references are resolved as not found.
- Public APIs use RBAC and hide forbidden resources with 404.
- Provider failures fail closed with a sanitized 503 response.
- Every mutation writes a same-transaction audit record containing identifiers,
  versions, digests, counts and states only.

## API

- `POST/GET /api/v2/evaluation-datasets`
- `GET /api/v2/evaluation-datasets/{public_id}`
- `POST /api/v2/evaluation-datasets/{public_id}/versions`
- `POST /api/v2/evaluation-datasets/{public_id}/trace-materializations`
- `GET /api/v2/evaluation-dataset-materializations`
- `GET /api/v2/evaluation-trace-candidates`
- `POST /api/v2/evaluation-datasets/{public_id}/curation-batches`
- `GET /api/v2/evaluation-dataset-curation-batches`
- `POST /api/v2/evaluation-dataset-curation-batches/{public_id}/reviews`
- `POST /api/v2/evaluation-dataset-curation-batches/{public_id}/materializations`
- `POST/GET /api/v2/evaluation-sampling-policies`
- `GET /api/v2/evaluation-sampling-policies/{public_id}`
- `POST /api/v2/evaluation-sampling-policies/{public_id}/versions`
- `POST /api/v2/evaluation-sampling-policy-versions/{public_id}/runs`
- `GET /api/v2/evaluation-sampling-runs`
- `GET /api/v2/evaluation-sampling-runs/{public_id}`
- `GET /api/v2/evaluation-annotation-queue-bindings/provider-queues`
- `POST/GET /api/v2/evaluation-annotation-queue-bindings`
- `GET /api/v2/evaluation-annotation-queue-bindings/{public_id}`
- `POST /api/v2/evaluation-annotation-queue-bindings/{public_id}/dispatches`
- `GET /api/v2/evaluation-annotation-dispatches`
- `GET /api/v2/evaluation-annotation-dispatches/{public_id}`
- `POST /api/v2/evaluation-annotation-dispatches/{public_id}/retry`
- `POST /api/v2/evaluation-annotation-dispatches/{public_id}/reconcile`
- `POST/GET /api/v2/evaluation-promotion-policies`
- `GET /api/v2/evaluation-promotion-policies/{public_id}`
- `POST /api/v2/evaluation-promotion-policies/{public_id}/versions`
- `POST /api/v2/evaluation-promotion-policy-versions/{public_id}/runs`
- `GET /api/v2/evaluation-promotion-runs`
- `GET /api/v2/evaluation-promotion-runs/{public_id}`
- `POST/GET /api/v2/evaluation-case-routing-policies`
- `GET /api/v2/evaluation-case-routing-policies/{public_id}`
- `POST /api/v2/evaluation-case-routing-policies/{public_id}/versions`
- `POST /api/v2/evaluation-case-routing-policy-versions/{public_id}/runs`
- `GET /api/v2/evaluation-case-routing-runs`
- `GET /api/v2/evaluation-case-routing-runs/{public_id}`
- `POST/GET /api/v2/evaluation-semantic-clustering-policies`
- `GET /api/v2/evaluation-semantic-clustering-policies/{public_id}`
- `POST /api/v2/evaluation-semantic-clustering-policies/{public_id}/versions`
- `POST /api/v2/evaluation-semantic-clustering-policy-versions/{public_id}/runs`
- `GET /api/v2/evaluation-semantic-clustering-runs`
- `GET /api/v2/evaluation-semantic-clustering-runs/{public_id}`
- `POST/GET /api/v2/evaluation-semantic-regression-policies`
- `GET /api/v2/evaluation-semantic-regression-policies/{public_id}`
- `POST /api/v2/evaluation-semantic-regression-policies/{public_id}/versions`
- `POST/GET /api/v2/evaluation-semantic-regression-comparisons`
- `GET /api/v2/evaluation-semantic-regression-comparisons/{public_id}`
- `POST/GET /api/v2/evaluation-semantic-monitors`
- `GET /api/v2/evaluation-semantic-monitors/{public_id}`
- `POST /api/v2/evaluation-semantic-monitors/{public_id}/pause`
- `POST /api/v2/evaluation-semantic-monitors/{public_id}/resume`
- `POST /api/v2/evaluation-semantic-monitors/{public_id}/run-now`
- `GET /api/v2/evaluation-semantic-monitor-runs`
- `GET /api/v2/evaluation-semantic-monitor-alerts`
- `POST /api/v2/evaluation-semantic-monitor-alerts/{public_id}/acknowledge`
- `POST/GET /api/v2/evaluation-failure-taxonomy-policies`
- `GET /api/v2/evaluation-failure-taxonomy-policies/{public_id}`
- `POST /api/v2/evaluation-failure-taxonomy-policies/{public_id}/versions`
- `POST /api/v2/evaluation-failure-taxonomy-policy-versions/{public_id}/runs`
- `GET /api/v2/evaluation-experience-extraction-runs`
- `GET /api/v2/evaluation-experience-extraction-runs/{public_id}`
- `GET /api/v2/evaluation-experience-candidates/{public_id}`
- `POST /api/v2/evaluation-experience-candidates/{public_id}/reviews`
- `POST/GET /api/v2/evaluation-experience-assets`
- `GET /api/v2/evaluation-experience-assets/{public_id}`
- `POST /api/v2/evaluation-experience-assets/{public_id}/versions`
- `POST /api/v2/evaluation-experience-asset-versions/{public_id}/activation-requests`
- `POST /api/v2/evaluation-experience-activation-requests/{public_id}/reviews`
- `POST/GET /api/v2/evaluators`
- `GET /api/v2/evaluators/{public_id}`
- `POST /api/v2/evaluators/{public_id}/versions`
- `POST/GET /api/v2/evaluation-experiments`
- `GET /api/v2/evaluation-experiments/{public_id}`
- `POST /api/v2/evaluation-experiments/{public_id}/evaluations`
- `GET /api/v2/evaluations`
- `GET /api/v2/evaluations/{public_id}`
- `GET /api/v2/evaluations/{public_id}/result-manifests`
- `POST /api/v2/evaluations/{public_id}/complete`
- `POST /api/v2/evaluations/{public_id}/cancel`
- `POST /api/v2/evaluations/{public_id}/retry`
- `POST/GET /api/v2/regression-policies`
- `GET /api/v2/regression-policies/{public_id}`
- `POST /api/v2/regression-policies/{public_id}/versions`
- `POST/GET /api/v2/evaluation-comparisons`
- `GET /api/v2/evaluation-comparisons/{public_id}`
- `POST/GET /api/v2/release-evidence/bindings`
- `GET /api/v2/release-evidence/bindings/{public_id}`
- `POST/GET /api/v2/release-evidence/reviews`
- `POST /api/v2/release-gates/evaluations`

## Deliberately deferred

- External notification delivery, learned feature extraction, automated
  Experience/rule body synthesis and runtime delivery. NEXT-017 adds durable
  online scheduling and in-app alerts, but neither synthesizes an Experience
  body nor activates production runtime context.
- Production sampling creates only an operator-reviewed curation batch; it
  never silently materializes production content into a Dataset.
- Annotation Queue synchronization remains an independent provider adapter
  seam. Sampling transaction success never depends on a remote queue side
  effect, and Langfuse completion never bypasses DuckDock approval.

Follow-up workflows must build on immutable dataset/evaluator/target pins; they
must not query “latest evaluation” for release decisions.
