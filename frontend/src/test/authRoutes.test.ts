/**
 * 权限路由门控测试(specs/002 FR-005 · specs/003 FR-002 · 宪法原则 III)。
 * 覆盖「无权限隐藏/重定向」的纯函数:canAccessManagement / canAccessNamespaceTools / canAccessIam / defaultRouteForUser。
 */
import { describe, expect, it } from "vitest";

import { canAccessIam, canAccessManagement, canAccessNamespaceTools, defaultRouteForUser } from "../authRoutes";
import type { UserProfile } from "../api/client";

const adminUser = { system_role: "admin" } as unknown as UserProfile;
const plainUser = { system_role: "user" } as unknown as UserProfile;

describe("canAccessManagement", () => {
  it("系统 admin 一律放行(不依赖权限键)", () => {
    expect(canAccessManagement(adminUser, [])).toBe(true);
  });

  it("持任一管理类权限键即放行", () => {
    expect(canAccessManagement(plainUser, ["runtime.read"])).toBe(true);
    expect(canAccessManagement(plainUser, ["handover.manage"])).toBe(true);
    expect(canAccessManagement(plainUser, ["iam.manage"])).toBe(true);
    expect(canAccessManagement(plainUser, ["sso.manage"])).toBe(true);
    expect(canAccessManagement(plainUser, ["org.read"])).toBe(true);
  });

  it("无管理权限键的普通用户拒绝", () => {
    expect(canAccessManagement(plainUser, [])).toBe(false);
    expect(canAccessManagement(plainUser, ["namespace.read"])).toBe(false);
  });

  it("user 为 null 且无权限键时拒绝", () => {
    expect(canAccessManagement(null, [])).toBe(false);
    expect(canAccessManagement(undefined, ["asset.read"])).toBe(true); // 权限键仍生效
  });
});

describe("canAccessNamespaceTools", () => {
  it("有管理权限即可访问命名空间工具", () => {
    expect(canAccessNamespaceTools(plainUser, ["asset.manage"])).toBe(true);
  });

  it("仅有 namespace.* 权限键也可访问(即便非管理类)", () => {
    expect(canAccessNamespaceTools(plainUser, ["namespace.publish"])).toBe(true);
  });

  it("两者皆无则拒绝", () => {
    expect(canAccessNamespaceTools(plainUser, [])).toBe(false);
  });
});

describe("canAccessIam", () => {
  it("系统 admin 一律放行", () => {
    expect(canAccessIam(adminUser, [])).toBe(true);
  });

  it("仅 IAM 或 SSO 管理权限可进入身份管理页", () => {
    expect(canAccessIam(plainUser, ["iam.manage"])).toBe(true);
    expect(canAccessIam(plainUser, ["sso.manage"])).toBe(true);
    expect(canAccessIam(plainUser, ["users.manage"])).toBe(false);
    expect(canAccessIam(plainUser, ["org.manage"])).toBe(false);
  });
});

describe("defaultRouteForUser", () => {
  it("管理用户落地 /dashboard,普通用户落地 /my-assets", () => {
    expect(defaultRouteForUser(adminUser, [])).toBe("/dashboard");
    expect(defaultRouteForUser(plainUser, ["runtime.read"])).toBe("/dashboard");
    expect(defaultRouteForUser(plainUser, [])).toBe("/my-assets");
  });
});
