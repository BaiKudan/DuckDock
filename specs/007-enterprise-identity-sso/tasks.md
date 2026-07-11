# 007 Enterprise Identity / SSO JIT Tasks

## Done In This Round

- [x] **IAM-01** Add enterprise UID to users.
  - Files: `backend/app/models/user.py`, `backend/app/schemas/user.py`, `backend/alembic/versions/20260709_0026_enterprise_identity_links.py`
  - Acceptance: existing users get deterministic `duid_*`; new SSO users prefer IdP employee UID.

- [x] **IAM-02** Add identity links.
  - Files: `backend/app/models/iam.py`, `backend/app/schemas/iam.py`, `backend/app/services/sso_service.py`
  - Acceptance: OIDC and LDAP subjects can map to the same user through `enterprise_uid`.

- [x] **IAM-03** Add SSO claim/group to RBAC mappings.
  - Files: `backend/app/models/iam.py`, `backend/app/schemas/iam.py`, `backend/app/services/sso_service.py`
  - Acceptance: matching claims create/update SSO-managed `RoleBinding`; missing claims revoke only SSO-managed bindings.

- [x] **IAM-04** Add IAM management APIs.
  - Files: `backend/app/api/v1/endpoints/iam.py`, `frontend/src/api/client.ts`
  - Acceptance: list identity links; CRUD role mappings; validate role scope and namespace/org targets.

- [x] **IAM-05** Add regression coverage.
  - Files: `backend/tests/test_sso_enterprise_identity.py`
  - Acceptance:
    - same enterprise UID across OIDC/LDAP resolves to one user;
    - role mapping grants and revokes permissions;
    - management API helpers create/list/update/delete role mappings.

- [x] **IAM-06** Update architecture and roadmap docs.
  - Files: `README.md`, `docs/architecture.md`, `docs/enterprise-iam-roadmap.zh-CN.md`, `docs/next-development-roadmap.zh-CN.md`, `docs/permission-matrix.zh-CN.md`
  - Acceptance: docs distinguish completed backend JIT from future SCIM/UI/real-IdP validation.

- [x] **IAM-FE-01** Frontend IAM page for identity links and SSO role mappings.
  - Files: `frontend/src/pages/IamPage.tsx`, `frontend/src/App.tsx`, `frontend/src/components/Layout.tsx`, `frontend/src/api/client.ts`, `frontend/src/authRoutes.ts`, `frontend/src/test/authRoutes.test.ts`
  - Acceptance: admin / `iam.manage` / `sso.manage` users can open `/iam`, inspect providers and identity links, and create/update/toggle/delete SSO claim/group role mappings using provider/role/namespace/org selectors.

## Verification

- `cd backend && ../.venv/bin/ruff check app/models/user.py app/models/iam.py app/schemas/user.py app/schemas/iam.py app/services/sso_service.py app/api/v1/endpoints/iam.py app/api/v1/endpoints/people.py tests/test_sso_enterprise_identity.py`
- `cd backend && ../.venv/bin/mypy app/models/iam.py app/services/sso_service.py app/api/v1/endpoints/iam.py app/models/user.py app/schemas/iam.py app/schemas/user.py --show-error-codes --no-error-summary`
- `cd backend && ../.venv/bin/python -m pytest tests/test_sso_enterprise_identity.py tests/test_sso_security.py tests/test_iam_binding_scope.py tests/test_rbac.py`
- `cd frontend && npm run test && npm run lint && npm run build`

## Open

- [ ] **IAM-IDP-01** Real/staging IdP E2E with documented claim samples.
- [ ] **IAM-SCIM-01** SCIM/HRIS directory sync design and implementation.
- [ ] **IAM-UX-01** Show `enterprise_uid` in employee/agent detail and handover views.
