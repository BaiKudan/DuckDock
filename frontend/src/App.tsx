import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import AnalysisControlPage from "./pages/AnalysisControlPage";
import AgentRuntimeOverviewPage from "./pages/AgentRuntimeOverviewPage";
import AuditPage from "./pages/AuditPage";
import ClinicPage from "./pages/ClinicPage";
import ClinicReportPage from "./pages/ClinicReportPage";
import ComponentManagementPage from "./pages/ComponentManagementPage";
import ControlPlanePage from "./pages/ControlPlanePage";
import DashboardPage from "./pages/DashboardPage";
import HandoverDetailPage from "./pages/HandoverDetailPage";
import IamPage from "./pages/IamPage";
import LoginPage from "./pages/LoginPage";
import NamespaceDetailPage from "./pages/NamespaceDetailPage";
import NamespacesPage from "./pages/NamespacesPage";
import PeopleHandoverPage from "./pages/PeopleHandoverPage";
import PersonalWorkspacePage from "./pages/PersonalWorkspacePage";
import PublishSkillPage from "./pages/PublishSkillPage";
import PublicRegistryPage from "./pages/PublicRegistryPage";
import RegisterPage from "./pages/RegisterPage";
import ReporterSetupPage from "./pages/ReporterSetupPage";
import SkillDetailPage from "./pages/SkillDetailPage";
import SsoCallbackPage from "./pages/SsoCallbackPage";
import { canAccessIam, canAccessManagement, defaultRouteForUser } from "./authRoutes";
import { useAuthStore } from "./store/auth";

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
    </BrowserRouter>
  );
}
