import { forwardRef } from "react";
import type { InputHTMLAttributes, ReactNode } from "react";

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  /** Leading icon (lucide). Adds left padding so text clears it. */
  icon?: ReactNode;
  wrapperClassName?: string;
}

/**
 * DuckDock Input — radius 6, slate-200 border, signature indigo focus ring
 * (indigo-300 border + 4px indigo-100 ring). Leading icon pads left to 36px.
 */
export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { icon, className = "", wrapperClassName = "", ...rest },
  ref,
) {
  const field = (
    <input
      ref={ref}
      className={[
        "w-full rounded-md border border-slate-200 bg-white py-2 text-sm text-slate-900 shadow-sm outline-none",
        "transition-colors placeholder:text-slate-400 focus:border-indigo-300 focus:ring-4 focus:ring-indigo-100",
        "disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-400",
        icon ? "pl-9 pr-3" : "px-3",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
      {...rest}
    />
  );
  if (!icon) return field;
  return (
    <div className={["relative", wrapperClassName].filter(Boolean).join(" ")}>
      <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400">
        {icon}
      </span>
      {field}
    </div>
  );
});

export default Input;
