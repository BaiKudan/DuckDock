# PGA-07 pre-test security engagement gate — 2026-08-07

## Result

PASS for the repository-side protocol. This result does **not** claim that a real
third-party assessment, penetration test, remediation, deletion attestation or
Security authorization has occurred.

DuckDock no longer accepts a post-hoc assessor report as the only scope record.
The formal path is now a two-party, two-signature protocol:

1. after the immutable execution campaign is created, an approval-policy
   `Security` identity signs `duckdock-ga-security-assessment-engagement-v1`
   under `duckdock-security-assessment-engagement`;
2. the independent, policy-authorized assessor executes only inside that
   engagement and signs `duckdock-ga-security-assessment-report-v2` under
   `duckdock-security-assessment`;
3. `collect_ga_independent_security.py` independently verifies both signatures
   and emits `duckdock-ga-independent-security-evidence-v3`;
4. the final production evaluator reopens the engagement, report, signatures
   and PDF instead of trusting wrapper projections;
5. campaign closure requires the engagement and signature at their two exact
   planned paths and rejects an engagement authorized before campaign creation
   or a test window outside the campaign window.

## Enforced engagement boundary

- exact final target, commit, contract and backend/frontend image digests;
- exact policy-authorized provider, assessor identity and Security authorizer;
- maximum 30-day test window contained by the execution campaign;
- canonical non-wildcard source CIDRs, assessor system IDs and non-secret test
  account IDs;
- mandatory prohibition of denial of service, destructive data modification,
  production-data exfiltration, persistence/backdoors, social engineering,
  physical testing and third-party systems outside the target;
- both Security and Operations stop authority, at least two emergency contacts
  and a maximum 15-minute stop acknowledgement SLA;
- encrypted-at-rest working evidence, no secret recording, no retained
  production data, mandatory deletion attestation and deletion no later than
  90 days after the assessment window;
- signed JSON, final PDF, finding-level evidence, Critical/High retest and
  deletion attestation as mandatory deliverables.

The assessor-signed report must repeat the exact approved source CIDRs, systems
and accounts actually used. It must attest that working evidence was encrypted,
contained no recorded secrets or retained production data, and was deleted by
the engagement deadline. Its start, deletion and completion timestamps must be
ordered and inside the authorized window. Its engagement ID/SHA-256, scope and
methodologies must exactly match the Security-signed engagement.

## Campaign and archive closure

The execution plan now has 69 unique artifacts: 66 external inputs and three
preapproval/closure outputs. The signed end-to-end fixture closes 66 external
artifacts into 90 captured inputs and 135 explicit references. The same fixture
then reaches `APPROVAL_COLLECTION`, freezes the closure, collects four distinct
approvals, reaches `GA_AUTHORIZED`, builds the deterministic archive, verifies it
without host fallback and reaches `GA_PUBLICATION_AUTHORIZED`.

## Negative verification

Tests prove that a validly re-signed document still fails when it:

- removes the mandatory DoS prohibition;
- moves report execution outside the engagement window;
- substitutes another engagement digest;
- reports an unapproved source CIDR;
- claims working evidence was not deleted;
- predates the execution campaign or exceeds its window.

Existing controls still reject an unauthorized assessor key, hidden High
finding, inconsistent severity summary, modified PDF and internal/external key
reuse.

## Verification record

- Backend CI-equivalent suite: `1232 passed, 19 skipped`, three dependency
  deprecation warnings; selected core coverage `81.04%` against `65%` minimum.
- Production authorization suite: `130 passed`.
- Independent security collector suite: `7 passed`.
- Production operations suite: `41 passed` (included in the full backend run).
- Frontend: `11` files / `56` tests; lint and production build passed.
- Repository production baseline: `30/30 PASS`.
- Ruff, compileall, frozen OpenAPI, 16 YAML files, all GA example JSON and four
  Compose configurations passed.
- mypy: `81` errors, below ratchet ceiling `82`.
- Locked backend audit: no known vulnerabilities; npm audit: zero
  vulnerabilities.

## Remaining external work

The release authority must still create the real final campaign and trust
topology, select and contract the assessor, have the real Security identity sign
the engagement, provision its bounded accounts/network access, monitor the test,
remediate and retest all Critical/High findings, receive the assessor-signed v2
report/PDF/deletion attestation, and run the v3 collector. Until those artifacts
exist for the final release and target, PGA-07 remains externally pending and GA
remains blocked.
