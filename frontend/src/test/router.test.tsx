import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import {
  BrowserRouter,
  Link,
  Navigate,
  Outlet,
  Route,
  Routes,
  resolveInternalTarget,
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
} from "../router";

function RouteProbe() {
  const { runtimeId } = useParams<{ runtimeId: string }>();
  const location = useLocation();
  const [search] = useSearchParams();
  return (
    <div>
      <span>{runtimeId}</span>
      <span>{search.get("tab")}</span>
      <span>{String((location.state as { source?: string } | null)?.source ?? "none")}</span>
    </div>
  );
}

function LayoutProbe() {
  return (
    <section>
      <h1>layout</h1>
      <Outlet />
    </section>
  );
}

function StatefulNavigation() {
  const navigate = useNavigate();
  return (
    <button onClick={() => navigate("/runtimes/runtime%201?tab=evidence", { state: { source: "fleet" } })}>
      open runtime
    </button>
  );
}

describe("DuckDock SPA router", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/");
  });

  it("matches nested routes, decodes params and preserves query/state", async () => {
    render(
      <BrowserRouter>
        <Routes>
          <Route element={<LayoutProbe />}>
            <Route index element={<StatefulNavigation />} />
            <Route path="/runtimes/:runtimeId" element={<RouteProbe />} />
          </Route>
        </Routes>
      </BrowserRouter>,
    );

    fireEvent.click(screen.getByRole("button", { name: "open runtime" }));

    await waitFor(() => expect(window.location.pathname).toBe("/runtimes/runtime%201"));
    expect(screen.getByText("runtime 1")).toBeInTheDocument();
    expect(screen.getByText("evidence")).toBeInTheDocument();
    expect(screen.getByText("fleet")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "layout" })).toBeInTheDocument();
  });

  it("intercepts same-origin links and resolves fallback redirects", async () => {
    render(
      <BrowserRouter>
        <Link to="/missing">missing</Link>
        <Routes>
          <Route path="/missing" element={<Navigate to="/safe" replace />} />
          <Route path="/safe" element={<div>safe destination</div>} />
        </Routes>
      </BrowserRouter>,
    );

    fireEvent.click(screen.getByRole("link", { name: "missing" }));
    await waitFor(() => expect(window.location.pathname).toBe("/safe"));
    expect(screen.getByText("safe destination")).toBeInTheDocument();
  });

  it("rejects cross-origin Link targets before navigation", () => {
    expect(() => resolveInternalTarget("https://attacker.example/steal")).toThrow(
      "DuckDock navigation only accepts same-origin targets",
    );
    expect(window.location.pathname).toBe("/");
  });
});
