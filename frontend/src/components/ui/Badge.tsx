import type { HTMLAttributes, ReactNode } from "react";

export type Tone = "indigo" | "emerald" | "amber" | "rose" | "violet" | "orange" | "neutral";

const TONE: Record<Tone, string> = {
  indigo: "border-indigo-200 bg-indigo-50 text-indigo-700",
  emerald: "border-emerald-200 bg-emerald-50 text-emerald-700",
  amber: "border-amber-200 bg-amber-50 text-amber-700",
  rose: "border-rose-200 bg-rose-50 text-rose-700",
  violet: "border-violet-200 bg-violet-50 text-violet-700",
  orange: "border-orange-200 bg-orange-50 text-orange-700",
  neutral: "border-slate-200 bg-slate-50 text-slate-600",
};

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: Tone;
  icon?: ReactNode;
}

/**
 * DuckDock Badge / StatusPill — soft-chip recipe (50 fill + 200 border + 700 text).
 * Pass `icon` to make it a status pill.
 */
export function Badge({ tone = "neutral", icon, className = "", children, ...rest }: BadgeProps) {
  return (
    <span
      className={[
        "inline-flex items-center rounded-md border px-2.5 py-1 text-xs font-semibold",
        icon ? "gap-1.5" : "",
        TONE[tone],
        className,
      ]
        .filter(Boolean)
        .join(" ")}
      {...rest}
    >
      {icon ? <span className="inline-flex shrink-0">{icon}</span> : null}
      {children}
    </span>
  );
}

export default Badge;
