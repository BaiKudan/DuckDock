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

const internalRouteBase = "https://duckdock.invalid";

function hasRedirectControlCharacter(value: string) {
  return Array.from(value).some((character) => {
    const codePoint = character.charCodeAt(0);
    return codePoint <= 0x1f || codePoint === 0x7f;
  });
}

function isSafeInternalRoute(value: string) {
  if (
    !value.startsWith("/") ||
    value.startsWith("//") ||
    value.includes("\\") ||
    hasRedirectControlCharacter(value)
  ) {
    return false;
  }
  try {
    return new URL(value, internalRouteBase).origin === internalRouteBase;
  } catch {
    return false;
  }
}

export function safeInternalRoute(value: string | null | undefined, fallback: string) {
  if (!value) {
    return fallback;
  }

  let candidate = value;
  for (let depth = 0; depth < 3; depth += 1) {
    if (!isSafeInternalRoute(candidate)) {
      return fallback;
    }
    try {
      const decoded = decodeURIComponent(candidate);
      if (decoded === candidate) {
        return value;
      }
      candidate = decoded;
    } catch {
      return fallback;
    }
  }
  return fallback;
}

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
