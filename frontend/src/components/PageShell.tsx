import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { Inbox } from "lucide-react";

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="page-header">
      <div className="min-w-0">
        {eyebrow ? <div className="section-kicker">{eyebrow}</div> : null}
        <h1 className="section-title mt-3">{title}</h1>
        {description ? <div className="section-subtitle max-w-3xl leading-6">{description}</div> : null}
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap gap-3">{actions}</div> : null}
    </div>
  );
}

export function SectionCard({
  title,
  description,
  action,
  children,
  className = "",
}: {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`surface-card overflow-hidden ${className}`}>
      <div className="card-header">
        <div className="min-w-0">
          <div className="text-base font-semibold tracking-tight text-slate-900 sm:text-lg">{title}</div>
          {description ? <div className="mt-1 text-sm leading-5 text-slate-500">{description}</div> : null}
        </div>
        {action ? <div className="shrink-0">{action}</div> : null}
      </div>
      {children}
    </section>
  );
}

export function MetricCard({
  label,
  value,
  detail,
  icon: Icon,
  loading,
}: {
  label: string;
  value: string;
  detail: ReactNode;
  icon: LucideIcon;
  loading?: boolean;
}) {
  return (
    <div className="metric-card">
      <div className="flex h-full items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="text-sm font-medium text-slate-500">{label}</div>
          <div className="mt-4 text-3xl font-bold tracking-tight text-slate-900">{loading ? "-" : value}</div>
          <div className="mt-3 text-sm leading-5 text-slate-500">{detail}</div>
        </div>
        <div className="icon-tile h-11 w-11">
          <Icon className="h-5 w-5" />
        </div>
      </div>
    </div>
  );
}

export function EmptyState({ text, icon: Icon = Inbox }: { text: string; icon?: LucideIcon }) {
  return (
    <div className="empty-state">
      <Icon className="mb-3 h-8 w-8 text-slate-300" />
      <div>{text}</div>
    </div>
  );
}
