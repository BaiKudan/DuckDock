import type { AiAssist } from "../api/client";

export function parseAiAssistHeader(header: unknown): AiAssist | null {
  if (typeof header !== "string" || !header.trim()) {
    return null;
  }
  try {
    const parsed = JSON.parse(header) as Partial<AiAssist>;
    if (
      (parsed.mode === "llm" || parsed.mode === "baseline") &&
      typeof parsed.degraded === "boolean" &&
      (typeof parsed.reason === "string" || parsed.reason === null)
    ) {
      return {
        mode: parsed.mode,
        degraded: parsed.degraded,
        reason: parsed.reason,
      };
    }
  } catch {
    return null;
  }
  return null;
}
