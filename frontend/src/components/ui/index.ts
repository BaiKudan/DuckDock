// DuckDock design-system primitives. Screens compose these.
export { Button } from "./Button";
export type { ButtonProps } from "./Button";
export { Badge } from "./Badge";
export type { BadgeProps, Tone } from "./Badge";
export { MonoPill } from "./MonoPill";
export { Input } from "./Input";
export type { InputProps } from "./Input";
export { Card } from "./Card";
export type { CardProps } from "./Card";
export { IconTile } from "./IconTile";
export type { IconTileProps } from "./IconTile";
export { MetricCard } from "./MetricCard";
export type { MetricCardProps } from "./MetricCard";

// Layout helpers live in PageShell; re-export so screens have one import site.
export { PageHeader, EmptyState } from "../PageShell";
