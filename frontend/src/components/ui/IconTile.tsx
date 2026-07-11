import type { ReactNode } from "react";

type TileSize = "sm" | "md" | "lg";
type TileTone = "neutral" | "indigo";

const SIZE: Record<TileSize, string> = {
  sm: "h-9 w-9 rounded-lg",
  md: "h-10 w-10 rounded-lg",
  lg: "h-11 w-11 rounded-lg",
};

const TONE: Record<TileTone, string> = {
  neutral: "border-slate-200 bg-slate-50 text-slate-500",
  indigo: "border-indigo-200 bg-indigo-50 text-indigo-600",
};

export interface IconTileProps {
  size?: TileSize;
  tone?: TileTone;
  className?: string;
  children: ReactNode;
}

/**
 * DuckDock IconTile — rounded slate/indigo square framing one icon.
 * Used in metric cards, list rows, the sidebar brand mark.
 */
export function IconTile({ size = "md", tone = "neutral", className = "", children }: IconTileProps) {
  return (
    <span
      className={[
        "inline-flex shrink-0 items-center justify-center border",
        SIZE[size],
        TONE[tone],
        className,
      ]
        .filter(Boolean)
        .join(" ")}
    >
      {children}
    </span>
  );
}

export default IconTile;
