# NEXT-016 Semantic Clustering Regression Evidence — 2026-08-03

## Outcome

NEXT-016 adds an immutable, provider-independent offline quality gate for two
semantic clustering runs over the exact same Case Routing evidence set:

1. a policy version freezes the minimum pairwise assignment agreement and the
   maximum cluster-count change, eligible-cluster-ratio drop and mean-centroid-
   similarity drop;
2. baseline and candidate must pin the same Case Routing run and contain the
   exact same source refs and content digests;
3. comparison reads only frozen DuckDock clustering summaries and never calls
   Langfuse, Hermes or an embedding endpoint;
4. a clustered baseline and four passing metrics produce `PASS`; an unusable
   candidate or a breached threshold produces `DRIFTED`; baseline/source/
   content incompatibility produces fail-closed `INCONCLUSIVE`;
5. policy versions and comparisons are immutable and idempotent, with explicit
   reason codes and a reproducibility digest.

This is an offline regression control. It does not schedule new clustering,
send alerts, change an Experience, or deliver anything to a runtime.

## Implementation boundary

Revision `20260803_0055` adds:

- `evaluation_semantic_regression_policies`
- `evaluation_semantic_regression_policy_versions`
- `evaluation_semantic_regression_comparisons`
- API v2 create/list/get surfaces for policy identities, immutable versions and
  exact baseline/candidate comparisons
- a content/vector-free
  `EvaluationSemanticRegressionComparisonCreated` Outbox event
- the `语义簇质量与漂移门禁` Eval Hub panel

The comparison service is provider-neutral and uses no semantic adapter. It
derives pairwise co-assignment agreement from stable source membership, uses a
normalized cluster-count delta, compares eligible-cluster ratios, and measures
the drop in mean frozen centroid similarity. Exact source and content-digest
matching prevents a changed evaluation corpus from being misreported as model
or clustering drift.

## Automated evidence

- Backend full suite: `971 passed, 19 skipped`.
- Focused semantic regression HTTP/service and migration suites: `2 passed`.
- Frontend full suite: `48 passed`; Eval Hub component suite: `13 passed`.
- Frontend lint: `0 errors`, `9` pre-existing warnings; production build passed.
- Targeted Ruff, Python compile and mypy checks passed.
- Development MySQL reports `20260803_0055 (head)`; `alembic check` reports no
  new upgrade operations.
- Langfuse v4 real compatibility gate: `60 passed`; live Web/Worker `4.1.0`,
  SDK `4.14.2`, Observation, Queue, Scores, Dataset, Experiment and OTLP lanes
  all passed.

## Real Langfuse + local Hermes validation

Both candidates used the existing local Hermes `bge-small-en-v1.5` embedding
endpoint and the same real evidence as the baseline:

- Case Routing run: `ecrr_8eacce1b23a34e53b35c58f98e1ee5e1`
- baseline semantic run: `escr_1fdd1c4e9203409f8a0fa78aca581241`
- regression policy: `esrp_942993ac28b54a8ab6e2fe2a1fce13b6`
- policy version: `esrv_6420b5bd922c4d5d931de9e934f83ad7`
- thresholds: agreement `0.9`, cluster-count change `0.25`, centroid-similarity
  drop `0.1`, eligible-cluster-ratio drop `0.25`

Stable candidate:

- semantic policy/version: `escp_4c56a43f903d4aa2a8c6539ca0af4934` /
  `escv_be6378007413439fa864d6a2df2b1c12`
- candidate run: `escr_2ecd9fb175034787884feb55fd8035a3`
- comparison: `esrc_fb254656d45f4ad49ac2f21e753176b2`
- result: `PASS`
- assignment agreement `1.0`; cluster-count change `0.0`; eligible-ratio drop
  `0.0`; centroid-similarity drop `0.0`
- digest:
  `3aa7ebdda030cd47a4a1fae4c36a9d3a5ee4c158381985e8e0b36aac818e04fa`

Deliberately drifted candidate:

- semantic policy/version: `escp_bea29b8cf5e1467fa2a581f0c15c1713` /
  `escv_e9e97fcef74b494eaedc2366841eb049`
- candidate run: `escr_b67f640522e04b1cae573b13aab92e2f`
- comparison: `esrc_6a4490eebd284726bb2e3ab8d5966d02`
- result: `DRIFTED`
- assignment agreement `0.0`; cluster-count change `0.5`; eligible-ratio drop
  `1.0`; centroid-similarity drop `0.0`
- reasons: `candidate_not_clustered`,
  `pairwise_assignment_agreement_below_minimum`,
  `cluster_count_change_exceeded`, `eligible_cluster_ratio_drop_exceeded`
- digest:
  `2946eb748de4aad481bef100e317c999214d00e9754ae17c9f849d8ab39ff747`

The stable lane proves unchanged semantic behavior passes. The deliberately
changed clustering threshold proves an unusable/different partition fails
closed without needing another provider call during comparison.

## Privacy and propagation evidence

An information-schema inspection found no raw-content, input, output, vector or
embedding payload column. The only matching name is the required boolean
contract flag `require_exact_source_content`; comparisons reference content only
through the existing SHA-256 digests of their source runs. API schemas, Audit
rows and the Outbox event contain exact pins, outcomes, counts, ratios,
similarities, reason codes and digests only. No Observation body or embedding
vector is persisted or propagated by NEXT-016.

## Local development deployment

The core DuckDock images were rebuilt, revision `0055` was applied, and the
complete development stack was recreated while preserving the existing data
volumes and local Hermes process. Post-deployment health and browser validation
confirmed backend `8801`, frontend `5174`, Langfuse `3200`, MySQL `3307` and
Hermes `50070` were reachable. The browser rendered both the real `PASS` and
`DRIFTED` comparisons in `语义簇质量与漂移门禁`; console errors were empty.

The validation records remain available for review. Any temporary UI login and
an orphaned pre-validation policy identity were removed after verification.
