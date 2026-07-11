import { AlertTriangle, Sparkles } from "lucide-react";

import type { AiAssist } from "../api/client";
import { useI18n } from "../i18n";
import { Badge, type Tone } from "./ui";

export function AiAssistBadge({
  aiAssist,
  className = "",
}: {
  aiAssist?: AiAssist | null;
  className?: string;
}) {
  const { t } = useI18n();
  if (!aiAssist) return null;

  const degraded = aiAssist.degraded || aiAssist.mode !== "llm";
  const tone: Tone = degraded ? "amber" : "emerald";
  const Icon = degraded ? AlertTriangle : Sparkles;
  const baseTitle = degraded ? t("aiAssist.baselineTitle") : t("aiAssist.llmTitle");
  const title = aiAssist.reason ? `${baseTitle}: ${aiAssist.reason}` : baseTitle;

  return (
    <Badge
      tone={tone}
      className={className}
      title={title}
      aria-label={title}
      icon={<Icon className="h-3.5 w-3.5" />}
    >
      {degraded ? t("aiAssist.baseline") : t("aiAssist.llm")}
    </Badge>
  );
}
