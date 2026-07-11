import type { HTMLAttributes, ReactNode } from "react";

export interface CardProps extends Omit<HTMLAttributes<HTMLElement>, "title"> {
  title?: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  /** Pad the body (16–20px). Leave false for flush, divided rows. */
  padded?: boolean;
}

/**
 * DuckDock Card — the workhorse surface. radius 8, slate-200 border, shadow-sm.
 * With `title` → header (title+desc left, action right) + divider; body flush or padded.
 */
export function Card({
  title,
  description,
  action,
  padded = false,
  className = "",
  children,
  ...rest
}: CardProps) {
  return (
    <section
      className={["overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm", className]
        .filter(Boolean)
        .join(" ")}
      {...rest}
    >
      {title ? (
        <div className="flex items-center justify-between gap-3 border-b border-slate-200 px-5 py-4 sm:px-6">
          <div className="min-w-0">
            <div className="text-base font-semibold tracking-tight text-slate-900 sm:text-lg">
              {title}
            </div>
            {description ? (
              <div className="mt-0.5 text-sm text-slate-500">{description}</div>
            ) : null}
          </div>
          {action ? <div className="shrink-0">{action}</div> : null}
        </div>
      ) : null}
      <div className={padded ? "p-5 sm:p-6" : undefined}>{children}</div>
    </section>
  );
}

export default Card;
