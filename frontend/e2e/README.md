# Frontend E2E (Playwright) — specs/005 P3-11

Closed-loop end-to-end coverage for the manual handover flow.

## What's here

- **`auth.smoke.spec.ts`** — smoke test for the login guard and login form.
- **`handover-closeloop.spec.ts`** — the full closed loop
  (`approve → execute → receipt → verify → completed`). It needs a running
  backend stack. CI starts the dev stack, auto-seeds a
  `pending_approval` case through `backend/scripts/seed_handover_e2e.py`, then
  drives the real `HandoverDetailPage` UI selectors.

## Setup (one-time; not in package-lock)

`@playwright/test` is intentionally **not** in `package.json`/`package-lock.json`
(the lock is regenerated only where Node is available). Install it ad-hoc:

```bash
cd frontend
npm i -D @playwright/test@1.61.1 --no-save --package-lock=false
npx playwright install chromium
```

The dev Compose frontend image is Alpine-based, while Playwright's downloaded
Chromium is an Ubuntu/glibc build. Run the browser-backed specs on a normal
Linux/macOS host or the Ubuntu CI job; the Alpine container can still run the
regular Vitest/build checks.

## Run the smoke (no backend needed)

```bash
cd frontend
npm run test:e2e                   # Playwright starts `vite dev` on :5174 itself
```

## Run the full closed loop (needs the stack)

Bring up the full stack (backend :8801/8990, mysql/redis/minio, frontend :5174).
From `frontend/`, let Playwright auto-seed a fresh pending-approval case and
target the already-running app:

```bash
DEBUG=true \
E2E_AUTO_SEED=1 \
E2E_BASE_URL=http://localhost:5174 \
npm run test:e2e -- handover-closeloop.spec.ts
```

The default auto-seed account is dev/test-only:

```text
E2E_ADMIN_USER=e2e-p3-admin
E2E_ADMIN_PASS=DuckDock@E2E2026!
```

To use a pre-seeded case instead:

```bash
eval "$(DEBUG=true ../.venv/bin/python ../backend/scripts/seed_handover_e2e.py --shell)"
export E2E_ADMIN_PASS=DuckDock@E2E2026!
E2E_BASE_URL=http://localhost:5174 npm run test:e2e -- handover-closeloop.spec.ts
```

Or point at an existing pending/approved case:

```bash
E2E_SEEDED=1 \
E2E_ADMIN_USER=ddadmin E2E_ADMIN_PASS='DuckDock@Admin2026' \
E2E_CASE_ID=<case id at pending_approval or approved> \
E2E_BASE_URL=http://localhost:5174 \
npm run test:e2e -- handover-closeloop.spec.ts
```

### Seed contract

`backend/scripts/seed_handover_e2e.py` creates one low-criticality asset and a
single pending approval. The browser test is then responsible for the actual UI
loop: approve, execute, submit a success receipt, and verify the case as
completed. The seed intentionally avoids sensitive traces/high-criticality assets
so this P3-11 test covers the handover FSM itself; evidence-upload-required flows
should be added as a separate E2E.

## CI

The `e2e` job in `.github/workflows/ci.yml` is blocking. It installs Playwright
and Chromium ad-hoc, starts the DuckDock dev stack through `scripts/dev.sh up`,
sets `DEBUG=true` + `E2E_AUTO_SEED=1`, then runs both the auth smoke and the
seeded handover closed-loop spec.
