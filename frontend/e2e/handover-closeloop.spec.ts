import { test, expect, type Page } from "@playwright/test";
import { execSync } from "node:child_process";

// ──────────────────────────────────────────────────────────────────────────
// 手动交接「闭环」E2E（specs/005 P3-11 / FR-005 Phase 2 SC-003）。
//
// 这条用例驱动我们在 P3 建的闭环 UI：approve → execute → 回执(+证据) → verify → completed。
// 它需要一个**已就绪的后端全栈 + 种子数据**，因此默认跳过，仅当显式置位时运行：
//
//   E2E_SEEDED=1                使用外部已 seed case 时必填
//   E2E_AUTO_SEED=1             自动调用后端 seed 脚本生成 pending_approval case
//   E2E_SEED_COMMAND            可选，覆盖默认 seed 命令
//   DEBUG=true                  自动 seed 时必填；后端脚本会拒绝生产态造数
//   E2E_ADMIN_USER / E2E_ADMIN_PASS   有 handover.manage 权限（或 admin）的账号
//   E2E_CASE_ID                 一个处于 `pending_approval` 或 `approved` 态的交接单 id
//   E2E_BASE_URL（可选）        指向已运行的应用；不设则用 playwright.config 的 vite dev
//
// 种子契约：由后端测试夹具/脚本把一个 case 推进到 `pending_approval`（创建→采集→分析→生成
// 审批任务），E2E 负责驱动批准及其后的闭环 UI。把"造数"留给后端、把"点 UI"留给 E2E，
// 是最稳的分工——避免在浏览器里重跑采集/分析这类重依赖步骤。
//
// 选择器锚点（HandoverDetailPage.tsx，无 data-testid，按文案/role/placeholder）：
//   批准=button:has-text("批准") · 执行=button:has-text("执行") · 回执=button:has-text("回执")
//   完成交接=button:has-text("完成交接") · 验收备注=textarea[placeholder="验收备注"]
//   回执说明=input[placeholder="回执说明"] · 结果下拉=execution action 内 <select>
// 这些文案若随设计/ i18n 变动需同步更新。
// ──────────────────────────────────────────────────────────────────────────

const DEFAULT_E2E_ADMIN_USER = "e2e-p3-admin";
const DEFAULT_E2E_ADMIN_PASS = "DuckDock@E2E2026!";

type HandoverStatus = "approved" | "completed" | "executing" | "verifying";

interface HandoverCaseApi {
  status: string;
}

interface ExecutionActionApi {
  status: string;
}

function resolveClosedLoopEnv() {
  let caseId = process.env.E2E_CASE_ID ?? "";
  let adminUser = process.env.E2E_ADMIN_USER ?? "";
  const autoSeed = process.env.E2E_AUTO_SEED === "1";

  if (autoSeed && !caseId) {
    const command =
      process.env.E2E_SEED_COMMAND ??
      "../.venv/bin/python ../backend/scripts/seed_handover_e2e.py --json";
    const raw = execSync(command, {
      cwd: process.cwd(),
      env: {
        ...process.env,
        E2E_ADMIN_USER: adminUser || DEFAULT_E2E_ADMIN_USER,
        E2E_ADMIN_PASS: process.env.E2E_ADMIN_PASS || DEFAULT_E2E_ADMIN_PASS,
      },
      encoding: "utf-8",
      stdio: ["ignore", "pipe", "inherit"],
    });
    const parsed = JSON.parse(raw) as { case_id?: number; admin_username?: string };
    caseId = parsed.case_id ? String(parsed.case_id) : "";
    adminUser = adminUser || parsed.admin_username || DEFAULT_E2E_ADMIN_USER;
  }

  return {
    seeded: process.env.E2E_SEEDED === "1" || autoSeed,
    caseId,
    adminUser: adminUser || DEFAULT_E2E_ADMIN_USER,
    adminPass: process.env.E2E_ADMIN_PASS || DEFAULT_E2E_ADMIN_PASS,
  };
}

const CLOSED_LOOP = resolveClosedLoopEnv();

async function login(page: Page) {
  await page.goto("/login");
  await page.locator('input[type="text"]').first().fill(CLOSED_LOOP.adminUser);
  await page.locator('input[type="password"]').fill(CLOSED_LOOP.adminPass);
  await page.locator('button[type="submit"]').click();
  // 登录成功后 token 落在 localStorage 的 duckdock-auth。
  await expect
    .poll(async () =>
      page.evaluate(() => Boolean(window.localStorage.getItem("duckdock-auth"))),
    )
    .toBe(true);
}

async function apiJson<T>(page: Page, path: string): Promise<T> {
  return page.evaluate(async (apiPath) => {
    const raw = window.localStorage.getItem("duckdock-auth");
    const token = raw ? JSON.parse(raw).state?.accessToken : null;
    if (!token) throw new Error("missing DuckDock auth token");
    const response = await window.fetch(apiPath, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!response.ok) {
      throw new Error(`${response.status} ${await response.text()}`);
    }
    return response.json();
  }, path) as Promise<T>;
}

async function reloadHandover(page: Page) {
  await page.reload();
  await expect(page.getByText(/case #/i)).toBeVisible({ timeout: 20_000 });
}

async function waitForCaseStatus(page: Page, status: HandoverStatus) {
  await expect
    .poll(async () => {
      const handover = await apiJson<HandoverCaseApi>(
        page,
        `/api/v1/handovers/${CLOSED_LOOP.caseId}`,
      );
      return handover.status;
    }, { timeout: 20_000 })
    .toBe(status);
}

async function waitForPendingReceiptCount(page: Page, expected: number) {
  await expect
    .poll(async () => {
      const actions = await apiJson<ExecutionActionApi[]>(
        page,
        `/api/v1/handovers/${CLOSED_LOOP.caseId}/actions`,
      );
      return actions.filter((action) => !["succeeded", "failed"].includes(action.status)).length;
    }, { timeout: 20_000 })
    .toBe(expected);
}

test.describe("manual handover closed loop", () => {
  test.describe.configure({ retries: 0 });

  test.skip(
    !CLOSED_LOOP.seeded || !CLOSED_LOOP.caseId || !CLOSED_LOOP.adminUser || !CLOSED_LOOP.adminPass,
    "requires a seeded backend: set E2E_AUTO_SEED=1, or E2E_SEEDED=1 + E2E_ADMIN_USER/PASS + E2E_CASE_ID. See e2e/README.md.",
  );

  test("approve → execute → receipt → verify → completed", async ({ page }) => {
    test.setTimeout(60_000);
    await login(page);
    await page.goto(`/handovers/${CLOSED_LOOP.caseId}`);
    await expect(page.getByText(/case #/i)).toBeVisible({ timeout: 20_000 });

    // 1) 审批：批准所有待审批任务（若种子已审批完则此区为空，跳过）。
    const approveButtons = page.locator('button:has-text("批准")');
    for (let i = (await approveButtons.count()) - 1; i >= 0; i--) {
      await approveButtons.nth(i).click();
    }
    await waitForCaseStatus(page, "approved");
    await reloadHandover(page);

    // 2) 执行：触发执行动作（status=approved 时可见）。
    const executeBtn = page.locator('button:has-text("执行")').first();
    await expect(executeBtn).toBeEnabled({ timeout: 15_000 });
    await executeBtn.click();
    await waitForCaseStatus(page, "executing");
    await reloadHandover(page);

    // 3) 回执：对每个待回执的执行动作填结果=succeeded + 说明并提交回执。
    //    （若动作要求证据，种子需保证 case 资产/痕迹敏感度允许无证据，或先走 上传证据 控件。）
    const noteInputs = page.locator('input[placeholder="回执说明"]');
    await expect(noteInputs.first()).toBeVisible({ timeout: 15_000 });
    let receiptCount = await noteInputs.count();
    await waitForPendingReceiptCount(page, receiptCount);
    while (receiptCount > 0) {
      await noteInputs.first().fill("E2E 自动回执");
      const receiptBtn = page.locator('button:has-text("回执")').first();
      await expect(receiptBtn).toBeEnabled({ timeout: 15_000 });
      await receiptBtn.click();
      receiptCount -= 1;
      await waitForPendingReceiptCount(page, receiptCount);
      await reloadHandover(page);
    }
    await waitForCaseStatus(page, "verifying");
    await reloadHandover(page);

    // 4) 验收完成：填验收备注 + 点完成交接，期望 case 进入 completed。
    await page.locator('textarea[placeholder="验收备注"]').fill("E2E 验收通过");
    const completeBtn = page.locator('button:has-text("完成交接")');
    await expect(completeBtn).toBeEnabled({ timeout: 15_000 });
    await completeBtn.click();
    await waitForCaseStatus(page, "completed");
    await reloadHandover(page);
    await expect(page.getByText("completed", { exact: true })).toBeVisible({ timeout: 15_000 });
  });
});
