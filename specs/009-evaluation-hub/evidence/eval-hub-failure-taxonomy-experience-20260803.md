# NEXT-013 — failure taxonomy and Experience candidate evidence

Date: 2026-08-03

## Accepted contract

- A Namespace-scoped policy owns immutable, digest-addressed taxonomy
  versions. Every version pins one exact Case Routing policy version,
  recurrence thresholds, isolated-case inclusion and a candidate cap.
- Extraction accepts only a `ROUTED` run from the exact pinned version and
  consumes only selected Bad Case lane, frozen cluster digest, source
  references and controlled reason codes.
- Clusters are deterministically classified as `CROSS_RUN_RECURRING`,
  `SINGLE_RUN_RECURRING` or `ISOLATED`. Isolated failures are excluded unless
  the immutable version explicitly opts in.
- Extraction creates only metadata candidates in `PENDING_REVIEW`. One final
  APPROVED/REJECTED review is allowed. Approval confirms a candidate but does
  not synthesize or deploy Prompt, Skill, Memory, rule text or Agent changes.
- Observation IO, score values and annotation comments/corrections remain in
  Langfuse. MySQL, Audit and Outbox contain only exact pins, references,
  categories, counts, reason codes and SHA-256 digests.

## Real local browser validation

- Development UI: `http://127.0.0.1:5174/eval-hub`
- Existing real Hermes Case Routing source:
  `ecrr_8eacce1b23a34e53b35c58f98e1ee5e1`, outcome `ROUTED`.
- Created taxonomy policy `eftp_c5f2e5a56f23475fa065b373b2e2abcb`
  and version `eftv_994c564595c34b3bb25e611c492c1b7d` with:
  - minimum cluster occurrences: 2
  - minimum source runs: 2
  - isolated clusters: excluded
  - candidate cap: 20
- Extraction run `eer_be0e837ef4f342e7b9e59f3bcf02fc06`:
  - outcome `EXTRACTED`
  - 2 selected Bad Cases
  - 1 frozen cluster
  - 1 eligible/captured candidate
- Candidate `eec_a2eab4ade2f3447eb466910d8780f178`:
  - category `SINGLE_RUN_RECURRING`
  - 2 exact evidence items from Promotion run
    `epr_0190872feb36478389420937382957c9`
  - transitioned through the UI from `PENDING_REVIEW` to `APPROVED`
  - no Prompt/Skill/Memory/Agent state was created or changed
- SQL joins confirmed both exact Trace/Observation references. Outbox events
  `EvaluationExperienceExtractionRunCreated` and
  `EvaluationExperienceCandidateReviewed`, plus their Audit entries, contain
  no input/output/comment/score-value fields.
- Temporary UI user `next013e2e` was demoted to USER, disabled and removed
  from every Namespace after validation. Its immutable actor references remain
  available for audit provenance.

## Migration and regression evidence

- Development MySQL head: `20260803_0052`; `alembic check` reports no pending
  operations.
- Isolated MySQL validation passed full chain to 0051,
  `0051 -> 0052 -> 0051 -> 0052`, final head/check, and populated downgrade
  refusal. The temporary database and grant were removed afterward.
- Backend: `963 passed, 19 skipped`.
- Frontend: `46 passed`; lint 0 errors / 9 existing warnings; production build
  passed.
- Focused source mypy: no issues in the new service/API/schema/model surface.
- Real Langfuse v4 compatibility gate: `57 passed`; server `4.1.0`, SDK
  `4.14.2`, with Clinic, Annotation Queue, Scores v3 Promotion, Dataset,
  Experiment and direct OTLP lanes passing.
