/**
 * 前端测试地基首例(specs/002 FR-005 Phase 1 · T053)。
 * 覆盖 auth store 的核心不变量:令牌写入、权限装载、logout 全清空、persist 落盘。
 */
import { beforeEach, describe, expect, it } from "vitest";

import { useAuthStore } from "../store/auth";

describe("useAuthStore", () => {
  beforeEach(() => {
    useAuthStore.getState().logout();
    localStorage.clear();
  });

  it("setTokens 写入双令牌", () => {
    useAuthStore.getState().setTokens("access-1", "refresh-1");
    expect(useAuthStore.getState().accessToken).toBe("access-1");
    expect(useAuthStore.getState().refreshToken).toBe("refresh-1");
  });

  it("setPermissions 装载权限并标记 loaded", () => {
    useAuthStore.getState().setPermissions(["runtime.read", "handover.manage"]);
    const state = useAuthStore.getState();
    expect(state.permissionKeys).toEqual(["runtime.read", "handover.manage"]);
    expect(state.permissionsLoaded).toBe(true);
  });

  it("logout 清空令牌、用户与权限(权限收口后防残留越权态)", () => {
    const store = useAuthStore.getState();
    store.setTokens("a", "r");
    store.setUser({ id: 1, username: "u", email: "u@x.com", system_role: "admin" });
    store.setPermissions(["asset.read"]);

    useAuthStore.getState().logout();

    const state = useAuthStore.getState();
    expect(state.accessToken).toBeNull();
    expect(state.refreshToken).toBeNull();
    expect(state.user).toBeNull();
    expect(state.permissionKeys).toEqual([]);
    expect(state.permissionsLoaded).toBe(false);
  });

  it("persist 以 duckdock-auth 键落盘", () => {
    useAuthStore.getState().setTokens("persisted-token", "r2");
    const raw = localStorage.getItem("duckdock-auth");
    expect(raw).not.toBeNull();
    expect(JSON.parse(raw as string).state.accessToken).toBe("persisted-token");
  });
});
