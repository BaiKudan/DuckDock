import { useEffect, useMemo, useState } from "react";
import { CheckCircle2, Hexagon, LockKeyhole, ShieldCheck, UserRound } from "lucide-react";
import { Link, useNavigate } from "../router";
import { authApi, iamApi, type PublicSSOProvider } from "../api/client";
import { defaultRouteForUser } from "../authRoutes";
import LanguageToggle from "../components/LanguageToggle";
import { Button, IconTile, Input } from "../components/ui";
import { useI18n } from "../i18n";
import { useAuthStore } from "../store/auth";

export default function LoginPage() {
  const navigate = useNavigate();
  const { setTokens, setUser, setPermissions } = useAuthStore();
  const { locale, t } = useI18n();
  const [form, setForm] = useState({ username: "", password: "" });
  const [providers, setProviders] = useState<PublicSSOProvider[]>([]);
  const [selectedLdapProviderId, setSelectedLdapProviderId] = useState<number | "">("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const oidcProviders = useMemo(
    () => providers.filter((provider) => provider.provider_type === "oidc"),
    [providers]
  );
  const ldapProviders = useMemo(
    () => providers.filter((provider) => provider.provider_type === "ldap"),
    [providers]
  );

  useEffect(() => {
    authApi
      .listSSOProviders()
      .then(({ data }) => {
        setProviders(data);
        const firstLdap = data.find((provider) => provider.provider_type === "ldap");
        if (firstLdap) {
          setSelectedLdapProviderId(firstLdap.id);
        }
      })
      .catch(() => setProviders([]));
  }, []);

  async function finishLogin(accessToken: string, refreshToken: string) {
    setTokens(accessToken, refreshToken);
    const me = await authApi.me();
    setUser(me.data);
    const permissions = await iamApi.getMyPermissions();
    setPermissions(permissions.data.permission_keys);
    navigate(defaultRouteForUser(me.data, permissions.data.permission_keys), { replace: true });
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    setLoading(true);
    try {
      const { data } = await authApi.login(form);
      await finishLogin(data.access_token, data.refresh_token);
    } catch (err: any) {
      setError(err.response?.data?.detail ?? t("auth.login.failed"));
    } finally {
      setLoading(false);
    }
  }

  async function handleLdapLogin() {
    if (!selectedLdapProviderId) {
      setError(locale === "zh" ? "请选择 LDAP 提供方。" : "Select an LDAP provider.");
      return;
    }
    setError("");
    setLoading(true);
    try {
      const { data } = await authApi.ldapLogin({
        provider_id: Number(selectedLdapProviderId),
        username: form.username,
        password: form.password,
      });
      await finishLogin(data.access_token, data.refresh_token);
    } catch (err: any) {
      setError(err.response?.data?.detail ?? (locale === "zh" ? "LDAP 登录失败" : "LDAP sign-in failed"));
    } finally {
      setLoading(false);
    }
  }

  function handleOidcLogin(provider: PublicSSOProvider) {
    const next = encodeURIComponent("/dashboard");
    const loginUrl = provider.login_url?.includes("?")
      ? `${provider.login_url}&next=${next}`
      : `${provider.login_url}?next=${next}`;
    window.location.assign(loginUrl);
  }

  return (
    <div className="min-h-dvh bg-slate-50">
      <div className="mx-auto flex min-h-dvh w-full max-w-6xl flex-col px-4 py-5 sm:px-6 lg:px-8">
        <header className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-indigo-200 bg-indigo-50 text-indigo-600">
              <Hexagon className="h-4 w-4" />
            </div>
            <div>
              <div className="text-lg font-bold tracking-tight text-slate-900">DuckDock</div>
              <div className="text-xs text-slate-500">{t("layout.productTagline")}</div>
            </div>
          </div>
          <LanguageToggle />
        </header>

        <main className="grid flex-1 items-center gap-10 py-10 lg:grid-cols-[1fr_430px]">
          <section className="hidden max-w-2xl lg:block">
            <div className="section-kicker">{t("layout.workspace")}</div>
            <h1 className="mt-4 text-4xl font-bold tracking-tight text-slate-950">
              企业 AI Agent 资产与交接控制平面
            </h1>
            <p className="mt-4 max-w-xl text-sm leading-6 text-slate-500">
              管理员进入企业控制台，员工进入个人 AI 资产工作台。DuckDock 会按账号权限自动分流，不把普通用户带进管理后台。
            </p>

            <div className="mt-8 grid gap-3">
              {[
                "统一管理 OpenClaw、WorkBuddy、ArkClaw、JVS 与定制运行时。",
                "用 Reporter 周期性归档工作历程、资产和交接证据。",
                "员工只确认自己的资产和上下文，管理员处理治理、风险和交接。",
              ].map((item) => (
                <div key={item} className="flex items-center gap-3 rounded-lg border border-slate-200 bg-white px-4 py-3 shadow-sm">
                  <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" />
                  <span className="text-sm text-slate-600">{item}</span>
                </div>
              ))}
            </div>
          </section>

          <section className="mx-auto w-full max-w-[430px]">
            <form onSubmit={handleSubmit} className="surface-card space-y-5 p-6 sm:p-8">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <div className="section-kicker">{t("auth.login.kicker")}</div>
                  <h2 className="mt-3 text-2xl font-bold tracking-tight text-slate-900">{t("auth.login.title")}</h2>
                  <p className="mt-2 text-sm text-slate-500">{t("auth.product")}</p>
                </div>
                <IconTile size="lg" tone="indigo">
                  <ShieldCheck className="h-5 w-5" />
                </IconTile>
              </div>

              {error ? <div className="soft-rose rounded-md px-4 py-3 text-sm">{error}</div> : null}

              <div className="space-y-1.5">
                <label className="text-xs font-medium uppercase tracking-[0.18em] text-slate-500">{t("field.username")}</label>
                <Input
                  type="text"
                  required
                  autoFocus
                  icon={<UserRound className="h-4 w-4" />}
                  value={form.username}
                  onChange={(event) => setForm({ ...form, username: event.target.value })}
                  placeholder={t("auth.login.usernamePlaceholder")}
                />
              </div>

              <div className="space-y-1.5">
                <label className="text-xs font-medium uppercase tracking-[0.18em] text-slate-500">{t("field.password")}</label>
                <Input
                  type="password"
                  required
                  icon={<LockKeyhole className="h-4 w-4" />}
                  value={form.password}
                  onChange={(event) => setForm({ ...form, password: event.target.value })}
                  placeholder={t("auth.login.passwordPlaceholder")}
                />
              </div>

              <Button type="submit" disabled={loading} className="w-full">
                {loading ? t("auth.login.loading") : t("auth.login.submit")}
              </Button>

              {ldapProviders.length ? (
                <div className="space-y-3 rounded-lg border border-slate-200 bg-slate-50 p-4">
                  <div className="text-xs font-medium uppercase tracking-[0.18em] text-slate-500">
                    {locale === "zh" ? "LDAP 登录" : "LDAP sign-in"}
                  </div>
                  <select
                    value={selectedLdapProviderId}
                    onChange={(event) => setSelectedLdapProviderId(event.target.value ? Number(event.target.value) : "")}
                    className="input-control"
                  >
                    <option value="">{locale === "zh" ? "选择 LDAP 提供方" : "Select LDAP provider"}</option>
                    {ldapProviders.map((provider) => (
                      <option key={provider.id} value={provider.id}>
                        {provider.name}
                      </option>
                    ))}
                  </select>
                  <Button
                    variant="secondary"
                    type="button"
                    disabled={loading || !form.username.trim() || !form.password.trim()}
                    onClick={handleLdapLogin}
                    className="w-full"
                  >
                    {loading ? t("auth.login.loading") : locale === "zh" ? "使用 LDAP 登录" : "Sign in with LDAP"}
                  </Button>
                </div>
              ) : null}

              {oidcProviders.length ? (
                <div className="space-y-3 rounded-lg border border-slate-200 bg-slate-50 p-4">
                  <div className="text-xs font-medium uppercase tracking-[0.18em] text-slate-500">
                    {locale === "zh" ? "单点登录" : "Single sign-on"}
                  </div>
                  <div className="space-y-2">
                    {oidcProviders.map((provider) => (
                      <Button
                        key={provider.id}
                        variant="secondary"
                        type="button"
                        onClick={() => handleOidcLogin(provider)}
                        className="w-full"
                      >
                        {locale === "zh" ? `使用 ${provider.name} 登录` : `Continue with ${provider.name}`}
                      </Button>
                    ))}
                  </div>
                </div>
              ) : null}

              <p className="text-center text-sm text-slate-500">
                {t("auth.noAccount")}{" "}
                <Link to="/register" className="font-medium text-indigo-600 transition hover:text-indigo-500">
                  {t("auth.register.link")}
                </Link>
              </p>
            </form>
          </section>
        </main>
      </div>
    </div>
  );
}
