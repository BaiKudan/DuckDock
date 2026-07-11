import { test, expect } from "@playwright/test";

// 前端-only 冒烟：不依赖后端，验证鉴权门控与登录表单渲染。
// 这是 CI e2e job 的默认绿线（闭环用例受 E2E_SEEDED 门控、默认跳过）。
// 选择器刻意用 type/role 而非中文文案，避免 i18n 语言切换导致脆弱。

test("guarded route bounces an unauthenticated visitor to the login form", async ({ page }) => {
  await page.goto("/control-plane");
  // 无 token → 路由守卫把访客送回登录；以"登录密码框出现"作为到达登录态的稳健断言。
  await expect(page.locator('input[type="password"]')).toBeVisible({ timeout: 15_000 });
  await expect(page).not.toHaveURL(/\/control-plane$/);
});

test("login page renders the credential form", async ({ page }) => {
  await page.goto("/login");
  await expect(page.locator('input[type="text"]').first()).toBeVisible();
  await expect(page.locator('input[type="password"]')).toBeVisible();
  await expect(page.locator('button[type="submit"]')).toBeVisible();
});
