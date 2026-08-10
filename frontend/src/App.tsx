import { lazy, Suspense } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "./router";
import Layout from "./components/Layout";
import { canAccessIam, canAccessManagement, defaultRouteForUser } from "./authRoutes";
import { useAuthStore } from "./store/auth";

const AnalysisControlPage = lazy(() => import("./pages/AnalysisControlPage"));
const AgentRuntimeOverviewPage = lazy(() => import("./pages/AgentRuntimeOverviewPage"));
const AuditPage = lazy(() => import("./pages/AuditPage"));
const ClinicPage = lazy(() => import("./pages/ClinicPage"));
const ClinicReportPage = lazy(() => import("./pages/ClinicReportPage"));
const ComponentManagementPage = lazy(() => import("./pages/ComponentManagementPage"));
const ControlPlanePage = lazy(() => import("./pages/ControlPlanePage"));
const DashboardPage = lazy(() => import("./pages/DashboardPage"));
const EvalHubPage = lazy(() => import("./pages/EvalHubPage"));
const FleetPage = lazy(() => import("./pages/FleetPage"));
const HandoverDetailPage = lazy(() => import("./pages/HandoverDetailPage"));
const IamPage = lazy(() => import("./pages/IamPage"));
const LoginPage = lazy(() => import("./pages/LoginPage"));
const NamespaceDetailPage = lazy(() => import("./pages/NamespaceDetailPage"));
const NamespacesPage = lazy(() => import("./pages/NamespacesPage"));
const OperationsPage = lazy(() => import("./pages/OperationsPage"));
const PeopleHandoverPage = lazy(() => import("./pages/PeopleHandoverPage"));
const PackageRegistryPage = lazy(() => import("./pages/PackageRegistryPage"));
const PersonalWorkspacePage = lazy(() => import("./pages/PersonalWorkspacePage"));
const PublishSkillPage = lazy(() => import("./pages/PublishSkillPage"));
const PublicRegistryPage = lazy(() => import("./pages/PublicRegistryPage"));
const RegisterPage = lazy(() => import("./pages/RegisterPage"));
const ReleaseControlPage = lazy(() => import("./pages/ReleaseControlPage"));
const ReporterSetupPage = lazy(() => import("./pages/ReporterSetupPage"));
const SkillDetailPage = lazy(() => import("./pages/SkillDetailPage"));
const SsoCallbackPage = lazy(() => import("./pages/SsoCallbackPage"));

function RequireAuth({ children }: { children: React.ReactNode }) {
  const token = useAuthStore((state) => state.accessToken);
  return token ? <>{children}</> : <Navigate to="/login" replace />;
}

function DefaultLanding() {
  const user = useAuthStore((state) => state.user);
  const permissionKeys = useAuthStore((state) => state.permissionKeys);
  const permissionsLoaded = useAuthStore((state) => state.permissionsLoaded);
  if (!permissionsLoaded) {
    return <div className="app-page text-sm text-slate-500">Loading permissions...</div>;
  }
  return <Navigate to={defaultRouteForUser(user, permissionKeys)} replace />;
}

function RequireManagementAccess({ children }: { children: React.ReactNode }) {
  const user = useAuthStore((state) => state.user);
  const permissionKeys = useAuthStore((state) => state.permissionKeys);
  const permissionsLoaded = useAuthStore((state) => state.permissionsLoaded);
  if (!permissionsLoaded) {
    return <div className="app-page text-sm text-slate-500">Loading permissions...</div>;
  }
  return canAccessManagement(user, permissionKeys) ? <>{children}</> : <Navigate to="/my-assets" replace />;
}

function RequirePeopleAccess({ children }: { children: React.ReactNode }) {
  const user = useAuthStore((state) => state.user);
  const permissionKeys = useAuthStore((state) => state.permissionKeys);
  const permissionsLoaded = useAuthStore((state) => state.permissionsLoaded);
  const canAccessPeople = user?.system_role === "admin" || permissionKeys.some((key) => ["users.manage", "handover.manage"].includes(key));

  if (!permissionsLoaded) {
    return <div className="app-page text-sm text-slate-500">Loading permissions...</div>;
  }
  return canAccessPeople ? <>{children}</> : <Navigate to={defaultRouteForUser(user, permissionKeys)} replace />;
}

function RequireIamAccess({ children }: { children: React.ReactNode }) {
  const user = useAuthStore((state) => state.user);
  const permissionKeys = useAuthStore((state) => state.permissionKeys);
  const permissionsLoaded = useAuthStore((state) => state.permissionsLoaded);
  if (!permissionsLoaded) {
    return <div className="app-page text-sm text-slate-500">Loading permissions...</div>;
  }
  return canAccessIam(user, permissionKeys) ? <>{children}</> : <Navigate to={defaultRouteForUser(user, permissionKeys)} replace />;
}

function RequireAdminAccess({ children }: { children: React.ReactNode }) {
  const user = useAuthStore((state) => state.user);
  const permissionKeys = useAuthStore((state) => state.permissionKeys);
  const permissionsLoaded = useAuthStore((state) => state.permissionsLoaded);
  if (!permissionsLoaded) {
    return <div className="app-page text-sm text-slate-500">Loading permissions...</div>;
  }
  return user?.system_role === "admin" ? <>{children}</> : <Navigate to={defaultRouteForUser(user, permissionKeys)} replace />;
}

export default function App() {
  return (
    <BrowserRouter>
      <Suspense fallback={<div className="app-page text-sm text-slate-500">Loading DuckDock...</div>}>
        <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/login/sso/callback" element={<SsoCallbackPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route
          element={
            <RequireAuth>
              <Layout />
            </RequireAuth>
          }
        >
          <Route index element={<DefaultLanding />} />
          <Route
            path="/dashboard"
            element={
              <RequireManagementAccess>
                <DashboardPage />
              </RequireManagementAccess>
            }
          />
          <Route path="/my-assets" element={<PersonalWorkspacePage />} />
          <Route path="/reporter-setup" element={<ReporterSetupPage />} />
          <Route
            path="/control-plane"
            element={
              <RequireManagementAccess>
                <ControlPlanePage />
              </RequireManagementAccess>
            }
          />
          <Route
            path="/fleet"
            element={
              <RequireManagementAccess>
                <FleetPage />
              </RequireManagementAccess>
            }
          />
          <Route
            path="/eval-hub"
            element={
              <RequireManagementAccess>
                <EvalHubPage />
              </RequireManagementAccess>
            }
          />
          <Route
            path="/packages"
            element={
              <RequireManagementAccess>
                <PackageRegistryPage />
              </RequireManagementAccess>
            }
          />
          <Route
            path="/release-control"
            element={
              <RequireManagementAccess>
                <ReleaseControlPage />
              </RequireManagementAccess>
            }
          />
          <Route
            path="/operations"
            element={
              <RequireAdminAccess>
                <OperationsPage />
              </RequireAdminAccess>
            }
          />
          <Route
            path="/control-plane/runtimes/:runtimeId/overview"
            element={
              <RequireManagementAccess>
                <AgentRuntimeOverviewPage />
              </RequireManagementAccess>
            }
          />
          <Route
            path="/handovers/:caseId"
            element={
              <RequireManagementAccess>
                <HandoverDetailPage />
              </RequireManagementAccess>
            }
          />
          <Route
            path="/analysis"
            element={
              <RequireManagementAccess>
                <AnalysisControlPage />
              </RequireManagementAccess>
            }
          />
          <Route path="/public" element={<PublicRegistryPage />} />
          <Route path="/namespaces" element={<NamespacesPage />} />
          <Route path="/namespaces/:ns" element={<NamespaceDetailPage />} />
          <Route path="/namespaces/:ns/:skill" element={<SkillDetailPage />} />
          <Route path="/namespaces/:ns/:skill/publish" element={<PublishSkillPage />} />
          <Route path="/clinic" element={<ClinicPage />} />
          <Route path="/clinic/:evalId" element={<ClinicReportPage />} />
          <Route
            path="/audit"
            element={
              <RequireManagementAccess>
                <AuditPage />
              </RequireManagementAccess>
            }
          />
          <Route
            path="/components"
            element={
              <RequireAdminAccess>
                <ComponentManagementPage />
              </RequireAdminAccess>
            }
          />
          <Route
            path="/people"
            element={
              <RequirePeopleAccess>
                <PeopleHandoverPage />
              </RequirePeopleAccess>
            }
          />
          <Route
            path="/iam"
            element={
              <RequireIamAccess>
                <IamPage />
              </RequireIamAccess>
            }
          />
        </Route>
        </Routes>
      </Suspense>
    </BrowserRouter>
  );
}
