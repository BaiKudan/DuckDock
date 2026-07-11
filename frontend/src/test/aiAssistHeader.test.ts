import { describe, expect, it } from "vitest";

import { parseAiAssistHeader } from "../utils/aiAssist";

describe("parseAiAssistHeader", () => {
  it("parses a valid X-AI-Assist header", () => {
    expect(parseAiAssistHeader('{"mode":"llm","degraded":false,"reason":null}')).toEqual({
      mode: "llm",
      degraded: false,
      reason: null,
    });
  });

  it("ignores malformed optional telemetry", () => {
    expect(parseAiAssistHeader("{nope")).toBeNull();
    expect(parseAiAssistHeader('{"mode":"llm"}')).toBeNull();
    expect(parseAiAssistHeader(undefined)).toBeNull();
  });
});
