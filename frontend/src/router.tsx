import {
  Children,
  createContext,
  isValidElement,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type AnchorHTMLAttributes,
  type MouseEvent,
  type ReactElement,
  type ReactNode,
} from "react";

// This router intentionally exposes components and hooks as one replaceable API.
/* eslint-disable react-refresh/only-export-components */

const HISTORY_STATE_KEY = "__duckdock_router_state";

export type RouterLocation = {
  pathname: string;
  search: string;
  hash: string;
  state: unknown;
  key: string;
};

export type NavigateOptions = {
  replace?: boolean;
  state?: unknown;
};

export type NavigateFunction = (
  target: string | number,
  options?: NavigateOptions,
) => void;

type RouterContextValue = {
  location: RouterLocation;
  navigate: NavigateFunction;
};

type RouteContextValue = {
  params: Record<string, string>;
  outlet: ReactNode;
};

const RouterContext = createContext<RouterContextValue | null>(null);
const RouteContext = createContext<RouteContextValue>({ params: {}, outlet: null });

function readLocation(): RouterLocation {
  const state = window.history.state?.[HISTORY_STATE_KEY] ?? null;
  return {
    pathname: window.location.pathname || "/",
    search: window.location.search,
    hash: window.location.hash,
    state,
    key: `${window.history.length}:${window.location.href}`,
  };
}

export function resolveInternalTarget(target: string): string {
  const resolved = new URL(target, window.location.origin);
  if (resolved.origin !== window.location.origin) {
    throw new Error("DuckDock navigation only accepts same-origin targets");
  }
  return `${resolved.pathname}${resolved.search}${resolved.hash}`;
}

export function BrowserRouter({ children }: { children: ReactNode }) {
  const [location, setLocation] = useState<RouterLocation>(() => readLocation());

  useEffect(() => {
    const handlePopState = () => setLocation(readLocation());
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  const navigate = useCallback<NavigateFunction>((target, options = {}) => {
    if (typeof target === "number") {
      window.history.go(target);
      return;
    }

    const resolved = resolveInternalTarget(target);
    const state = { [HISTORY_STATE_KEY]: options.state ?? null };
    if (options.replace) {
      window.history.replaceState(state, "", resolved);
    } else {
      window.history.pushState(state, "", resolved);
    }
    setLocation(readLocation());
  }, []);

  const value = useMemo(() => ({ location, navigate }), [location, navigate]);
  return <RouterContext.Provider value={value}>{children}</RouterContext.Provider>;
}

function useRouter(): RouterContextValue {
  const value = useContext(RouterContext);
  if (value === null) {
    throw new Error("DuckDock router APIs must be used inside BrowserRouter");
  }
  return value;
}

export function useNavigate(): NavigateFunction {
  return useRouter().navigate;
}

export function useLocation(): RouterLocation {
  return useRouter().location;
}

export function useParams<T extends Record<string, string | undefined> = Record<string, string>>() {
  return useContext(RouteContext).params as T;
}

export function useSearchParams(): [
  URLSearchParams,
  (next: URLSearchParams | Record<string, string> | ((current: URLSearchParams) => URLSearchParams), options?: NavigateOptions) => void,
] {
  const { location, navigate } = useRouter();
  const params = useMemo(() => new URLSearchParams(location.search), [location.search]);
  const setParams = useCallback(
    (
      next: URLSearchParams | Record<string, string> | ((current: URLSearchParams) => URLSearchParams),
      options?: NavigateOptions,
    ) => {
      const resolved =
        typeof next === "function"
          ? next(new URLSearchParams(location.search))
          : next instanceof URLSearchParams
            ? next
            : new URLSearchParams(next);
      const query = resolved.toString();
      navigate(`${location.pathname}${query ? `?${query}` : ""}${location.hash}`, options);
    },
    [location.hash, location.pathname, location.search, navigate],
  );
  return [params, setParams];
}

export type RouteProps = {
  path?: string;
  index?: boolean;
  element?: ReactNode;
  children?: ReactNode;
};

export function Route(_props: RouteProps) {
  return null;
}

type RouteMatch = {
  element: ReactNode;
  params: Record<string, string>;
};

function splitPath(pathname: string): string[] {
  return pathname.replace(/^\/+|\/+$/g, "").split("/").filter(Boolean);
}

function matchPath(
  pattern: string | undefined,
  index: boolean | undefined,
  pathname: string,
): Record<string, string> | null {
  if (index) {
    return pathname === "/" || pathname === "" ? {} : null;
  }
  if (pattern === "*") {
    return {};
  }
  if (!pattern) {
    return null;
  }

  const expected = splitPath(pattern);
  const actual = splitPath(pathname);
  if (expected.length !== actual.length) {
    return null;
  }

  const params: Record<string, string> = {};
  for (let index = 0; index < expected.length; index += 1) {
    const segment = expected[index];
    const value = actual[index];
    if (segment.startsWith(":")) {
      try {
        params[segment.slice(1)] = decodeURIComponent(value);
      } catch {
        return null;
      }
    } else if (segment !== value) {
      return null;
    }
  }
  return params;
}

function routeChildren(element: ReactElement<RouteProps>): ReactNode {
  return element.props.children;
}

function findMatch(children: ReactNode, pathname: string): RouteMatch | null {
  for (const candidate of Children.toArray(children)) {
    if (!isValidElement<RouteProps>(candidate) || candidate.type !== Route) {
      continue;
    }
    const props = candidate.props;
    if (!props.path && !props.index && routeChildren(candidate)) {
      const nested = findMatch(routeChildren(candidate), pathname);
      if (nested !== null) {
        return {
          params: nested.params,
          element: (
            <RouteContext.Provider value={{ params: nested.params, outlet: nested.element }}>
              {props.element}
            </RouteContext.Provider>
          ),
        };
      }
      continue;
    }

    const params = matchPath(props.path, props.index, pathname);
    if (params !== null) {
      return {
        params,
        element: (
          <RouteContext.Provider value={{ params, outlet: null }}>
            {props.element}
          </RouteContext.Provider>
        ),
      };
    }
  }
  return null;
}

export function Routes({ children }: { children: ReactNode }) {
  const { location } = useRouter();
  return <>{findMatch(children, location.pathname)?.element ?? null}</>;
}

export function Outlet() {
  return <>{useContext(RouteContext).outlet}</>;
}

export function Navigate({ to, replace = false, state }: { to: string; replace?: boolean; state?: unknown }) {
  const navigate = useNavigate();
  const stateRef = useRef(state);
  stateRef.current = state;
  useLayoutEffect(() => {
    navigate(to, { replace, state: stateRef.current });
  }, [navigate, replace, to]);
  return null;
}

type LinkProps = Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "href"> & {
  to: string;
};

function shouldHandleLink(event: MouseEvent<HTMLAnchorElement>) {
  return (
    !event.defaultPrevented &&
    event.button === 0 &&
    !event.metaKey &&
    !event.ctrlKey &&
    !event.shiftKey &&
    !event.altKey &&
    event.currentTarget.target !== "_blank" &&
    !event.currentTarget.hasAttribute("download")
  );
}

export function Link({ to, onClick, ...props }: LinkProps) {
  const navigate = useNavigate();
  const href = resolveInternalTarget(to);
  return (
    <a
      {...props}
      href={href}
      onClick={(event) => {
        onClick?.(event);
        if (!shouldHandleLink(event)) {
          return;
        }
        event.preventDefault();
        navigate(href);
      }}
    />
  );
}

type NavLinkProps = Omit<LinkProps, "className"> & {
  end?: boolean;
  className?: string | ((state: { isActive: boolean }) => string);
};

export function NavLink({ to, end = false, className, ...props }: NavLinkProps) {
  const { pathname } = useLocation();
  const targetPath = new URL(resolveInternalTarget(to), window.location.origin).pathname;
  const isActive = pathname === targetPath || (!end && pathname.startsWith(`${targetPath}/`));
  const resolvedClassName = typeof className === "function" ? className({ isActive }) : className;
  return <Link {...props} to={to} className={resolvedClassName} aria-current={isActive ? "page" : undefined} />;
}
