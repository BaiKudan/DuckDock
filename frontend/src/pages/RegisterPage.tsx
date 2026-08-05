import { useState } from "react";
import { AtSign, CheckCircle2, Hexagon, LockKeyhole, UserPlus, UserRound } from "lucide-react";
import { Link, useNavigate } from "../router";
import { authApi } from "../api/client";
import LanguageToggle from "../components/LanguageToggle";
import { Button, IconTile, Input } from "../components/ui";
import { useI18n } from "../i18n";

export default function RegisterPage() {
  const navigate = useNavigate();
  const { t } = useI18n();
  const [form, setForm] = useState({ username: "", email: "", password: "" });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    setLoading(true);
    try {
      await authApi.register(form);
      navigate("/login");
    } catch (err: any) {
      const detail = err.response?.data?.detail;
      setError(Array.isArray(detail) ? detail[0]?.msg : detail ?? t("auth.register.failed"));
    } finally {
      setLoading(false);
    }
  }

  const renderField = (key: keyof typeof form, label: string, type = "text", placeholder = "") => (
    <div className="space-y-1.5">
      <label className="text-xs font-medium uppercase tracking-[0.18em] text-slate-500">{label}</label>
      <Input
        type={type}
        required
        icon={
          key === "username" ? (
            <UserRound className="h-4 w-4" />
          ) : key === "email" ? (
            <AtSign className="h-4 w-4" />
          ) : (
            <LockKeyhole className="h-4 w-4" />
          )
        }
        value={form[key]}
        onChange={(event) => setForm({ ...form, [key]: event.target.value })}
        placeholder={placeholder}
      />
    </div>
  );

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
            <h1 className="mt-4 text-4xl font-bold tracking-tight text-slate-950">{t("auth.register.hero.title")}</h1>
            <p className="mt-4 max-w-xl text-sm leading-6 text-slate-500">{t("auth.register.hero.subtitle")}</p>

            <div className="mt-8 grid gap-3">
              {["auth.hero.feature1", "auth.hero.feature2", "auth.hero.feature3"].map((item) => (
                <div key={item} className="flex items-center gap-3 rounded-lg border border-slate-200 bg-white px-4 py-3 shadow-sm">
                  <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" />
                  <span className="text-sm text-slate-600">{t(item)}</span>
                </div>
              ))}
            </div>
          </section>

          <section className="mx-auto w-full max-w-[430px]">
            <form onSubmit={handleSubmit} className="surface-card space-y-5 p-6 sm:p-8">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <div className="section-kicker">{t("auth.register.kicker")}</div>
                  <h2 className="mt-3 text-2xl font-bold tracking-tight text-slate-900">{t("auth.register.title")}</h2>
                  <p className="mt-2 text-sm text-slate-500">{t("auth.product")}</p>
                </div>
                <IconTile size="lg" tone="indigo">
                  <UserPlus className="h-5 w-5" />
                </IconTile>
              </div>

              {error ? <div className="soft-rose rounded-md px-4 py-3 text-sm">{error}</div> : null}

              {renderField("username", t("field.username"), "text", t("auth.register.usernamePlaceholder"))}
              {renderField("email", t("field.email"), "email", t("auth.register.emailPlaceholder"))}
              {renderField("password", t("field.password"), "password", t("auth.register.passwordPlaceholder"))}

              <Button type="submit" disabled={loading} className="w-full">
                {loading ? t("auth.register.loading") : t("auth.register.submit")}
              </Button>

              <p className="text-center text-sm text-slate-500">
                {t("auth.haveAccount")}{" "}
                <Link to="/login" className="font-medium text-indigo-600 transition hover:text-indigo-500">
                  {t("auth.login.link")}
                </Link>
              </p>
            </form>
          </section>
        </main>
      </div>
    </div>
  );
}
