# NEXT-014 Experience Asset / Activation Evidence — 2026-08-03

## Outcome

NEXT-014 closes the boundary between an approved metadata-only Experience
candidate and a governed, human-authored Experience version:

1. one approved candidate can create one Namespace-scoped Experience asset;
2. operators create bounded, immutable, digest-addressed versions;
3. only a DRAFT version can submit one activation request;
4. a reviewer different from both author and requester makes one final decision;
5. approval activates the exact version and retires the prior active version;
6. activation is DuckDock control-plane state only and performs no Langfuse,
   Hermes, Prompt, Skill, Memory, Deployment or Release Gate write.

This is intentionally a DuckDock-owned lifecycle. It does not reuse the legacy
`MemoryCandidate` report-analysis model and does not couple schema or state to
Langfuse internals.

## Storage and propagation boundary

Revision `20260803_0053` adds:

- `evaluation_experience_assets`
- `evaluation_experience_asset_versions`
- `evaluation_experience_activation_requests`
- `evaluation_experience_activation_reviews`

The Experience body, applicability, request note and review comment are stored
only in their bounded DuckDock governance rows/API response. Audit and
Transactional Outbox receive identifiers, status and SHA-256 digests only.
The three new domain events are:

- `EvaluationExperienceAssetVersionCreated`
- `EvaluationExperienceActivationRequested`
- `EvaluationExperienceActivationReviewed`

## Automated evidence

- Backend full suite: `966 passed, 19 skipped`.
- New/focused Experience suite: `5 passed`.
- Frontend full suite: `47 passed`; the new component test covers authoring,
  activation request, approval controls and candidate claim.
- Frontend lint: `0 errors`, `9` pre-existing warnings; production build passed.
- Backend Ruff: passed.
- Backend mypy ratchet: `82` errors, equal to the existing ceiling `82`; the
  change introduced no baseline increase.
- Langfuse v4 compatibility gate: `57 passed`, then all live health, SDK,
  Observation v2, Annotation Queue, Scores v3, Dataset, Experiment and OTLP
  lanes passed against Web/Worker `4.1.0` and SDK `4.14.2`.
- Development MySQL upgraded `0052 -> 0053`; `alembic current` reports
  `20260803_0053 (head)` and `alembic check` reports no new operations.

## Real MySQL / HTTP / browser validation

The previously approved Hermes-backed candidate was used without copying any
provider IO:

- source candidate: `eec_a2eab4ade2f3447eb466910d8780f178`
- Namespace: `1` (`platform-core`)
- Experience asset: `eea_4e897d137de3460fb261a71e89d693bd`
- immutable version: `eeav_380e5e4907eb4ede968a8c18ad0ad10f`
- activation request: `eear_819fb4a3d7764b44adff8fb85827fb2e`

Two temporary Namespace members exercised the real HTTP endpoints. The author
created the asset/version and requested activation. An approval attempt by the
same author returned HTTP `409`. A different member approved the request and
the exact version became `ACTIVE`.

The local browser opened `http://127.0.0.1:5174/eval-hub`, selected
`Trace2Dataset`, and confirmed:

- the `Experience 资产与独立激活` panel is rendered;
- `next014-recurring-recovery` is rendered once;
- one `ACTIVE` badge is rendered for the governed asset.

Real MySQL then showed exactly one of each new event and one of each expected
Audit action. Searching serialized event and audit JSON for the real Experience
body and activation note returned `0` matches in both stores.

After validation, both temporary users were demoted to `USER`, disabled, and
removed from Namespace membership. Their user rows remain because immutable
version/review evidence references their IDs. The Experience asset and its
activation evidence remain as the real validation record.

## Runtime status

- DuckDock backend: healthy on `8801`
- DuckDock frontend dev server: available on `5174`
- MySQL: healthy on `3307`
- Langfuse Web/Worker: healthy on `3200`, version `4.1.0`

No Experience content was delivered to the local Hermes instance. A future
runtime-delivery adapter must add an explicit target binding, independent
approval/receipt semantics and rollback before DuckDock can claim activated
Experience propagation.
