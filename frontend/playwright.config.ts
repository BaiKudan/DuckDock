import { defineConfig, devices } from "@playwright/test";

// DuckDock 闭环 E2E 配置（specs/005 P3-11）。
//
// 两类用例：
//  - e2e/auth.smoke.spec.ts —— 登录守卫与登录表单冒烟。
//  - e2e/handover-closeloop.spec.ts —— 需后端全栈 + 种子数据，受 E2E_SEEDED 门控，
//    CI 通过 E2E_AUTO_SEED=1 自动造数并阻塞运行。详见 e2e/README.md。
//
// @playwright/test 不在 package.json/lock 里；
// 本地/CI 临时安装：npm i -D @playwright/test@1.61.1 --no-save --package-lock=false。
const PORT = 5174;
const EXTERNAL_BASE = process.env.E2E_BASE_URL;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["github"]] : "list",
  use: {
    baseURL: EXTERNAL_BASE ?? `http://localhost:${PORT}`,
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  // When pointed at an already-running app (E2E_BASE_URL), don't manage a server.
  webServer: EXTERNAL_BASE
    ? undefined
    : {
        command: "npm run dev",
        url: `http://localhost:${PORT}`,
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
      },
});
