import { useEffect, useState } from "react";
import { AlertTriangle, Hexagon, Loader2 } from "lucide-react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { authApi, iamApi } from "../api/client";
import { defaultRouteForUser } from "../authRoutes";
import { IconTile } from "../components/ui";
import { useI18n } from "../i18n";
import { useAuthStore } from "../store/auth";

export default function SsoCallbackPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { locale } = useI18n();
  const { setTokens, setUser, setPermissions, logout } = useAuthStore();
  const [error, setError] = useState("");

  useEffect(() => {
    async function run() {
      const token = searchParams.get("token");
      const next = searchParams.get("next") || "/dashboard";
      if (!token) {
        setError(locale === "zh" ? "缺少 SSO 交换令牌。" : "Missing SSO exchange token.");
        return;
      }
      try {
        const { data } = await authApi.exchangeSSO({ token });
        setTokens(data.access_token, data.refresh_token);
        const me = await authApi.me();
        setUser(me.data);
        const permissions = await iamApi.getMyPermissions();
        setPermissions(permissions.data.permission_keys);
        const fallback = defaultRouteForUser(me.data, permissions.data.permission_keys);
        navigate(next.startsWith("/") && next !== "/dashboard" ? next : fallback, { replace: true });
      } catch (err: any) {
        logout();
        setError(
          err.response?.data?.detail ??
            (locale === "zh" ? "SSO 登录完成失败。" : "Failed to complete SSO sign-in.")
        );
      }
    }
    run();
  }, [locale, logout, navigate, searchParams, setPermissions, setTokens, setUser]);

  return (
    <div className="flex min-h-dvh items-center justify-center bg-slate-50 px-4 py-10">
      <div className="w-full max-w-md">
        <div className="flex flex-col items-center text-center">
          <IconTile size="lg" tone="indigo">
            <Hexagon className="h-5 w-5" />
          </IconTile>
          <h1 className="mt-5 text-2xl font-bold tracking-tight text-slate-900">
            {locale === "zh" ? "正在完成单点登录" : "Completing single sign-on"}
          </h1>

          {error ? (
            <div className="soft-rose mt-5 flex w-full items-start gap-3 rounded-lg px-4 py-3 text-left text-sm">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{error}</span>
            </div>
          ) : (
            <p className="mt-3 flex items-center justify-center gap-2 text-sm text-slate-500">
              <Loader2 className="h-4 w-4 animate-spin text-indigo-600" />
              {locale === "zh"
                ? "正在换取访问令牌并载入账号上下文..."
                : "Exchanging access tokens and loading account context..."}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
