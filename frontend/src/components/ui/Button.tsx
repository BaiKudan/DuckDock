import type { ButtonHTMLAttributes, ReactNode } from "react";

type Variant = "primary" | "secondary" | "ghost" | "link";
type Size = "sm" | "md" | "lg";

const VARIANT: Record<Variant, string> = {
  primary: "bg-indigo-600 text-white font-semibold shadow-sm hover:bg-indigo-700",
  secondary:
    "border border-slate-200 bg-white text-slate-700 font-medium shadow-sm hover:bg-slate-50",
  ghost: "bg-transparent text-slate-700 font-medium hover:bg-slate-50",
  link: "bg-transparent text-indigo-600 font-medium hover:text-indigo-700",
};

const DANGER: Record<Variant, string> = {
  primary: "bg-rose-600 text-white font-semibold shadow-sm hover:bg-rose-700",
  secondary:
    "border border-rose-200 bg-white text-rose-600 font-medium shadow-sm hover:bg-rose-50",
  ghost: "bg-transparent text-rose-600 font-medium hover:bg-rose-50",
  link: "bg-transparent text-rose-600 font-medium hover:text-rose-700",
};

const SIZE: Record<Size, string> = {
  sm: "px-3 py-1.5 text-sm",
  md: "px-4 py-2 text-sm",
  lg: "px-5 py-2.5 text-base",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  icon?: ReactNode;
  iconRight?: ReactNode;
  danger?: boolean;
}

/**
 * DuckDock Button — primary action control. One primary per view.
 * primary=indigo-600 (hover 700), secondary=white+slate-200, ghost, link=indigo.
 */
export function Button({
  variant = "primary",
  size = "md",
  icon,
  iconRight,
  danger = false,
  className = "",
  type = "button",
  children,
  ...rest
}: ButtonProps) {
  const tone = danger ? DANGER[variant] : VARIANT[variant];
  const sizing = variant === "link" ? "" : SIZE[size];
  return (
    <button
      type={type}
      className={[
        "inline-flex items-center justify-center gap-2 rounded-md transition-colors",
        "disabled:cursor-not-allowed disabled:opacity-50",
        tone,
        sizing,
        className,
      ]
        .filter(Boolean)
        .join(" ")}
      {...rest}
    >
      {icon ? <span className="inline-flex shrink-0">{icon}</span> : null}
      {children}
      {iconRight ? <span className="inline-flex shrink-0">{iconRight}</span> : null}
    </button>
  );
}

export default Button;
