import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { IconTile } from "./IconTile";

export interface MetricCardProps {
  label: string;
  value: ReactNode;
  detail?: ReactNode;
  icon: LucideIcon;
  /** Tint the corner IconTile. `rose` reuses the danger language for risky counts. */
  tone?: "neutral" | "indigo" | "rose";
  loading?: boolean;
  className?: string;
}

/**
 * DuckDock MetricCard — KPI tile. min-h 148, value 30/700, corner IconTile.
 */
export function MetricCard({
  label,
  value,
  detail,
  icon: Icon,
  tone = "neutral",
  loading = false,
  className = "",
}: MetricCardProps) {
  const tileTone = tone === "rose" ? "neutral" : tone;
  const iconColor = tone === "rose" ? "text-rose-500" : undefined;
  return (
    <div
      className={[
        "min-h-[148px] rounded-lg border border-slate-200 bg-white p-5 shadow-sm sm:p-6",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <div className="flex h-full items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="text-sm font-medium text-slate-500">{label}</div>
          <div className="mt-4 text-3xl font-bold tracking-tight text-slate-900">
            {loading ? "—" : value}
          </div>
          {detail ? <div className="mt-3 text-sm leading-5 text-slate-500">{detail}</div> : null}
        </div>
        <IconTile size="lg" tone={tileTone}>
          <Icon className={["h-5 w-5", iconColor].filter(Boolean).join(" ")} />
        </IconTile>
      </div>
    </div>
  );
}

export default MetricCard;
