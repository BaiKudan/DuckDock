# NEXT-015 Semantic Failure Clustering Evidence — 2026-08-03

## Outcome

NEXT-015 replaces the prior metadata-only cluster approximation with an
optional, provider-neutral semantic evidence lane while preserving the old
compatibility path:

1. a policy version pins one exact Case Routing version, embedding profile,
   model, dimensions, cosine threshold and bounded input limits;
2. a run accepts only selected Bad Cases from one matching `ROUTED` run;
3. the Langfuse adapter reads exact Observation IO ephemerally and calls an
   independently configured OpenAI-compatible embedding endpoint;
4. the core service performs deterministic normalized cosine/centroid
   clustering without a provider SDK or numerical-library dependency;
5. MySQL, API, Audit and Outbox persist only references, counts, similarity and
   SHA-256 digests—never Observation content or embedding vectors;
6. Failure Taxonomy can pin the exact semantic version/run, while an unpinned
   version continues to use the previous metadata-only path.

## Implementation boundary

Revision `20260803_0054` adds:

- `evaluation_semantic_clustering_policies`
- `evaluation_semantic_clustering_policy_versions`
- `evaluation_semantic_clustering_runs`
- `evaluation_semantic_clustering_run_items`
- optional exact semantic-version pin on Failure Taxonomy versions
- optional exact semantic-run pin on Experience extraction runs

`SemanticEmbeddingEvidencePort` is the core boundary. The default adapter is
Langfuse plus OpenAI-compatible embeddings, but policy, clustering,
idempotency, lineage and taxonomy semantics are provider-neutral. The adapter
enforces source count, URL/timeout, response-size, model-dimension and finite
numeric-vector bounds. Provider failures are exposed only as the sanitized
`semantic embedding evidence failed` error.

The production configuration is fail-closed and disabled by default via
`SEMANTIC_EMBEDDING_ENABLED=false`. Disabling it prevents new semantic runs;
it does not invalidate existing digest evidence or the metadata compatibility
path. Embedding model, dimension or threshold changes require a new immutable
policy version.

## Automated evidence

- Backend full suite: `969 passed, 19 skipped`.
- Semantic HTTP/service suite: idempotent clustering-to-taxonomy flow and
  sanitized provider-failure lane passed.
- Migration static guard: revision/table lineage, forbidden content/vector
  columns and populated downgrade refusal passed.
- Frontend full suite: `47 passed`; Eval Hub component suite: `12 passed`.
- Frontend lint: `0 errors`, `9` pre-existing warnings; production build passed.
- Targeted backend Ruff passed.
- Targeted mypy for service, adapter, Experience integration and endpoints:
  `Success: no issues found`.
- Langfuse v4 upgrade gate after adding NEXT-015: `59 passed`; live Web/Worker
  `4.1.0`, SDK `4.14.2`, Observation v2, Queue, Scores v3, Dataset, Experiment
  and OTLP lanes all passed.
- Development MySQL reports `20260803_0054 (head)` and `alembic check` reports
  no new upgrade operations.

## Real Langfuse + local Hermes validation

The existing local Hermes llama.cpp server provided
`bge-small-en-v1.5` embeddings with `384` dimensions. DuckDock backend reached
it through `host.docker.internal:50070`; the two exact Bad Case sources came
from the real Langfuse project and Case Routing run
`ecrr_8eacce1b23a34e53b35c58f98e1ee5e1`.

The observed pair cosine was `0.985426665`; the immutable policy froze a
`0.9754` threshold:

- policy: `escp_802b73e88c284142b761ef034add6e21`
- policy version: `escv_1a5d327d516a45c3bb3ed99a7a721a56`
- semantic run: `escr_1fdd1c4e9203409f8a0fa78aca581241`
- outcome: `CLUSTERED`
- source items / clusters / eligible clusters: `2 / 1 / 1`
- minimum item-to-centroid similarity: `0.99635`
- clustering digest:
  `2fbd2ce2669342ecff81e33a03ddfa9f541f5a17e2e52f7ca9520aaea2dcff68`

The real downstream lineage then pinned both semantic identities:

- taxonomy policy: `eftp_372b88be5b424c12ab530232833be6b0`
- taxonomy version: `eftv_0bbf29904492441e96d7076ad0f7a918`
- extraction run: `eer_eab29916876c466cbd96297595408ea2`
- outcome / candidates: `EXTRACTED / 1`
- candidate reason: `semantic_failure_cluster_candidate`

An information-schema inspection found no semantic-table column whose name
contains `content`, `input`, `output`, `vector` or `embedding`. Serialized
Outbox and Audit payloads contained only allowlisted identifiers, counts,
similarities and digests; no raw provider content or vectors were present.

## Real browser validation

The local browser logged into `http://127.0.0.1:5174/eval-hub`, selected
`Trace2Dataset`, and confirmed the complete real chain:

- `真实语义失败聚类` rendered the Hermes policy/version and the exact semantic
  run as `CLUSTERED`, `2 items`, `1 clusters`, `1 eligible`;
- Failure Taxonomy exposed both the metadata compatibility option and the
  immutable Hermes semantic version;
- the real taxonomy version displayed
  `semantic:escv_1a5d327d516a45c3bb3ed99a7a721a56`;
- the extraction displayed
  `semantic:escr_1fdd1c4e9203409f8a0fa78aca581241`, one cluster and one
  pending Experience candidate;
- browser error logs were empty.

The temporary local validation identity and membership were removed after
logout. The semantic policy/run and downstream taxonomy/extraction remain as
the immutable validation record.

## Runtime status

- DuckDock backend: healthy on `8801`
- DuckDock frontend dev server: available on `5174`
- MySQL: healthy on `3307`, schema head `0054`
- Langfuse Web/Worker: healthy on `3200`, version `4.1.0`
- local Hermes embedding endpoint: healthy on `127.0.0.1:50070`

No Experience body was generated and no active Experience was delivered to a
runtime. Automated synthesis, online cluster-quality/drift evaluation and
runtime delivery remain separate future control loops.
