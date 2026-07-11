import { create } from "zustand";
import { persist } from "zustand/middleware";

interface User {
  id: number;
  username: string;
  email: string;
  system_role: "admin" | "user";
  full_name?: string | null;
  auth_source?: "local" | "oidc" | "ldap";
  external_subject?: string | null;
  last_login_at?: string | null;
}

interface AuthState {
  accessToken: string | null;
  refreshToken: string | null;
  user: User | null;
  permissionKeys: string[];
  permissionsLoaded: boolean;
  setTokens: (access: string, refresh: string) => void;
  setUser: (user: User) => void;
  setPermissions: (permissionKeys: string[]) => void;
  logout: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      accessToken: null,
      refreshToken: null,
      user: null,
      permissionKeys: [],
      permissionsLoaded: false,
      setTokens: (access, refresh) =>
        set({ accessToken: access, refreshToken: refresh }),
      setUser: (user) => set({ user }),
      setPermissions: (permissionKeys) => set({ permissionKeys, permissionsLoaded: true }),
      logout: () =>
        set({
          accessToken: null,
          refreshToken: null,
          user: null,
          permissionKeys: [],
          permissionsLoaded: false,
        }),
    }),
    { name: "duckdock-auth" }
  )
);
