#!/usr/bin/env python3
"""Verify equivalent safe metadata for OpenInference and OTel GenAI inputs."""

from __future__ import annotations

import re
import sys


TRACE_ID = re.compile(r"Trace ID\s+:\s+([0-9a-f]{32})")
ATTRIBUTE = re.compile(
    r"->\s+([A-Za-z0-9_.]+):\s+(?:Str|Int)\((.*)\)\s*$"
)
OPENINFERENCE_TRACE_ID = "10112233445566778899aabbccddeeff"
OTEL_GENAI_TRACE_ID = "20112233445566778899aabbccddeeff"
CANONICAL_KEYS = (
    "gen_ai.request.model",
    "gen_ai.provider.name",
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "gen_ai.operation.name",
    "gen_ai.conversation.id",
)


def parsed_span_attributes(log_text: str) -> dict[str, dict[str, str]]:
    spans: dict[str, dict[str, str]] = {}
    current_trace_id: str | None = None
    for line in log_text.splitlines():
        trace_match = TRACE_ID.search(line)
        if trace_match is not None:
            current_trace_id = trace_match.group(1)
            spans.setdefault(current_trace_id, {})
            continue
        attribute_match = ATTRIBUTE.search(line)
        if attribute_match is not None and current_trace_id is not None:
            key, value = attribute_match.groups()
            spans[current_trace_id][key] = value
    return spans


def main() -> int:
    spans = parsed_span_attributes(sys.stdin.read())
    try:
        openinference = spans[OPENINFERENCE_TRACE_ID]
        otel_genai = spans[OTEL_GENAI_TRACE_ID]
        openinference_summary = {
            key: openinference[key] for key in CANONICAL_KEYS
        }
        otel_genai_summary = {
            key: otel_genai[key] for key in CANONICAL_KEYS
        }
    except KeyError as exc:
        print(
            f"Collector conformance output is missing {exc.args[0]}",
            file=sys.stderr,
        )
        return 1

    if openinference_summary != otel_genai_summary:
        print(
            "OpenInference and OTel GenAI normalized summaries differ: "
            f"{openinference_summary!r} != {otel_genai_summary!r}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
