import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { AiAssistBadge } from "../components/AiAssistBadge";
import { useI18nStore } from "../store/i18n";

describe("AiAssistBadge", () => {
  beforeEach(() => {
    useI18nStore.setState({ locale: "en" });
  });

  it("shows the LLM badge when AI assist really ran", () => {
    render(<AiAssistBadge aiAssist={{ mode: "llm", degraded: false, reason: null }} />);

    expect(screen.getByLabelText("LLM assist enabled")).toBeInTheDocument();
    expect(screen.getByText("AI")).toBeInTheDocument();
  });

  it("shows a baseline badge with the degradation reason", () => {
    render(
      <AiAssistBadge
        aiAssist={{ mode: "baseline", degraded: true, reason: "DUCKDOCK_LLM_API_KEY is not configured" }}
      />
    );

    expect(screen.getByText("Baseline")).toBeInTheDocument();
    expect(
      screen.getByLabelText("Heuristic baseline: DUCKDOCK_LLM_API_KEY is not configured")
    ).toBeInTheDocument();
  });
});
