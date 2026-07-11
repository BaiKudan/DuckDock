import type { UserProfile } from "./api/client";

const managementPermissionKeys = new Set([
  "runtime.read",
  "runtime.manage",
  "asset.read",
  "asset.manage",
  "worktrace.read",
  "handover.read",
  "handover.manage",
  "evidence.read",
  "audit.read",
  "iam.manage",
  "org.read",
  "org.manage",
  "sso.manage",
  "users.manage",
]);

export function canAccessManagement(user: UserProfile | null | undefined, permissionKeys: string[]) {
  return user?.system_role === "admin" || permissionKeys.some((key) => managementPermissionKeys.has(key));
}

export function canAccessNamespaceTools(user: UserProfile | null | undefined, permissionKeys: string[]) {
  return canAccessManagement(user, permissionKeys) || permissionKeys.some((key) => key.startsWith("namespace."));
}

export function canAccessIam(user: UserProfile | null | undefined, permissionKeys: string[]) {
  return user?.system_role === "admin" || permissionKeys.some((key) => ["iam.manage", "sso.manage"].includes(key));
}

export function defaultRouteForUser(user: UserProfile | null | undefined, permissionKeys: string[]) {
  return canAccessManagement(user, permissionKeys) ? "/dashboard" : "/my-assets";
}
