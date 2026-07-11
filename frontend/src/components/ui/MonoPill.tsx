import type { HTMLAttributes } from "react";

/**
 * DuckDock MonoPill — slate-100 fill, mono 12px. Identifiers are always mono:
 * versions, SHAs, slugs, IDs, robot tokens, runtime types.
 */
export function MonoPill({ className = "", children, ...rest }: HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={[
        "inline-flex items-center rounded-md bg-slate-100 px-2 py-1 font-mono text-xs text-slate-600",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
      {...rest}
    >
      {children}
    </span>
  );
}

export default MonoPill;
