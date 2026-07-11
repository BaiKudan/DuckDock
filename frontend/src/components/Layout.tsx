import {
  Boxes,
  BrainCircuit,
  ChevronRight,
  FileClock,
  Gauge,
  Globe2,
  Hexagon,
  KeyRound,
  LogOut,
  Network,
  Puzzle,
  ShieldCheck,
  TerminalSquare,
  UserRound,
  Users2,
} from "lucide-react";
import { useEffect } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { iamApi } from "../api/client";
import { canAccessIam, canAccessManagement, canAccessNamespaceTools } from "../authRoutes";
import { useI18n } from "../i18n";
import { useAuthStore } from "../store/auth";
import LanguageToggle from "./LanguageToggle";

export default function Layout() {
  const { user, accessToken, permissionKeys, permissionsLoaded, setPermissions, logout } = useAuthStore();
  const navigate = useNavigate();
  const location = useLocation();
  const { locale, t } = useI18n();
  const zh = locale === "zh";
  const canUseManagement = canAccessManagement(user, permissionKeys);
  const canUseIam = canAccessIam(user, permissionKeys);
  const canUseNamespaceTools = canAccessNamespaceTools(user, permissionKeys);
  const canManagePeople =
    user?.system_role === "admin" || permissionKeys.some((key) => ["users.manage", "handover.manage"].includes(key));
  const canManageComponents = user?.system_role === "admin";

  useEffect(() => {
    if (!accessToken || permissionsLoaded) {
      return;
    }
    iamApi
      .getMyPermissions()
      .then(({ data }) => setPermissions(data.permission_keys))
      .catch(() => setPermissions([]));
  }, [accessToken, permissionsLoaded, setPermissions]);

  const peopleLabel = zh ? "人员与交接" : "People Handover";
  const myAssetsLabel = zh ? "我的 AI 资产" : "My AI Assets";
  const reporterSetupLabel = zh ? "接入 SOP" : "Setup SOP";
  const controlPlaneLabel = zh ? "Agent 控制平面" : "Agent Control";
  const componentLabel = zh ? "组件管理" : "Components";
  const iamLabel = zh ? "身份与 SSO" : "Identity SSO";

  const nav = [
    { to: "/my-assets", label: myAssetsLabel, icon: UserRound },
    { to: "/reporter-setup", label: reporterSetupLabel, icon: TerminalSquare },
    ...(canUseManagement ? [{ to: "/dashboard", label: t("nav.overview"), icon: Gauge }] : []),
    ...(canUseManagement ? [{ to: "/control-plane", label: controlPlaneLabel, icon: Network }] : []),
    ...(canUseManagement ? [{ to: "/analysis", label: zh ? "分析控制台" : "Analysis", icon: BrainCircuit }] : []),
    { to: "/public", label: t("nav.public"), icon: Globe2 },
    ...(canUseNamespaceTools ? [{ to: "/namespaces", label: t("nav.namespaces"), icon: Boxes }] : []),
    ...(canUseNamespaceTools ? [{ to: "/clinic", label: t("nav.clinic"), icon: ShieldCheck }] : []),
    ...(canUseManagement ? [{ to: "/audit", label: t("nav.audit"), icon: FileClock }] : []),
    ...(canUseIam ? [{ to: "/iam", label: iamLabel, icon: KeyRound }] : []),
    ...(canManageComponents ? [{ to: "/components", label: componentLabel, icon: Puzzle }] : []),
    ...(canManagePeople ? [{ to: "/people", label: peopleLabel, icon: Users2 }] : []),
  ];

  const crumbLabels: Record<string, string> = {
    dashboard: t("layout.workspace"),
    "my-assets": myAssetsLabel,
    "reporter-setup": reporterSetupLabel,
    "control-plane": controlPlaneLabel,
    analysis: zh ? "分析控制台" : "Analysis",
    namespaces: t("nav.namespaces"),
    clinic: t("nav.clinic"),
    audit: t("nav.audit"),
    iam: iamLabel,
    public: t("nav.public"),
    people: peopleLabel,
    components: componentLabel,
  };

  const crumbs = location.pathname
    .split("/")
    .filter(Boolean)
    .map((segment) => crumbLabels[segment] ?? decodeURIComponent(segment));

  function handleLogout() {
    logout();
    navigate("/login");
  }

  return (
    <div className="flex h-dvh overflow-hidden bg-slate-50">
      <aside className="flex w-[268px] shrink-0 flex-col border-r border-slate-200 bg-white">
        <div className="flex h-16 items-center gap-3 border-b border-slate-200 px-5">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-indigo-200 bg-indigo-50 text-indigo-600">
            <Hexagon className="h-4 w-4" />
          </div>
          <div>
            <div className="text-lg font-bold tracking-tight text-slate-900">DuckDock</div>
            <div className="text-xs text-slate-500">{t("layout.productTagline")}</div>
          </div>
        </div>

        <nav className="flex-1 space-y-1 overflow-y-auto px-3 py-5">
          {nav.map((item) => {
            const Icon = item.icon;
            return (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  `flex items-center gap-3 rounded-lg px-3.5 py-2.5 text-sm font-medium transition ${
                    isActive
                      ? "bg-indigo-50 text-indigo-700"
                      : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
                  }`
                }
              >
                <Icon className="h-4 w-4" />
                <span>{item.label}</span>
              </NavLink>
            );
          })}
        </nav>

        <div className="border-t border-slate-200 px-3 py-3">
          <div className="surface-muted flex items-center justify-between px-3 py-3">
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold text-slate-900">{user?.username}</div>
              <div className="mt-1 text-xs capitalize text-slate-500">{user?.system_role}</div>
            </div>
            <button
              onClick={handleLogout}
              className="button-secondary h-9 w-9 shrink-0 px-0 text-slate-500 hover:text-slate-900"
              aria-label={t("layout.signOut")}
            >
              <LogOut className="h-4 w-4" />
            </button>
          </div>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-16 shrink-0 items-center justify-between border-b border-slate-200 bg-white px-6 lg:px-8">
          <div className="flex items-center gap-2 text-sm text-slate-500">
            {(crumbs.length ? crumbs : [t("layout.workspace")]).map((crumb, index) => (
              <div key={`${crumb}-${index}`} className="flex items-center gap-2">
                {index > 0 ? <ChevronRight className="h-4 w-4 text-slate-300" /> : null}
                <span className={index === crumbs.length - 1 ? "font-medium text-slate-700" : ""}>{crumb}</span>
              </div>
            ))}
          </div>

          <div className="flex items-center gap-4">
            <LanguageToggle />
            <div className="flex h-10 w-10 items-center justify-center rounded-full bg-indigo-100 text-sm font-semibold text-indigo-700">
              {(user?.username?.[0] ?? "D").toUpperCase()}
            </div>
          </div>
        </header>

        <main className="flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
