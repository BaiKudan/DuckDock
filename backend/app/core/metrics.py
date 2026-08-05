from __future__ import annotations

import math
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass


_LATENCY_BUCKETS = (0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0)
_MAX_RECENT_SAMPLES = 4096


@dataclass(frozen=True, slots=True)
class HttpMetricSummary:
    route: str
    method: str
    request_count: int
    error_count: int
    p95_ms: float | None


class HttpMetricsRegistry:
    """Small, dependency-free process metrics registry.

    Route templates are captured after FastAPI routing, so public IDs never
    become Prometheus labels. Recent samples are bounded and contain only
    numeric durations/status classes.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: dict[tuple[str, str, str], int] = defaultdict(int)
        self._duration_sum: dict[tuple[str, str], float] = defaultdict(float)
        self._samples: dict[tuple[str, str], deque[tuple[float, float, str]]] = defaultdict(
            lambda: deque(maxlen=_MAX_RECENT_SAMPLES)
        )

    def observe(self, *, method: str, route: str, status_code: int, duration: float) -> None:
        key = (method.upper(), route)
        status_class = f"{max(0, status_code) // 100}xx"
        bounded_duration = max(0.0, min(float(duration), 300.0))
        with self._lock:
            self._counts[(key[0], key[1], status_class)] += 1
            self._duration_sum[key] += bounded_duration
            self._samples[key].append((time.time(), bounded_duration, status_class))

    def summaries(self, *, window_seconds: int | None = None) -> list[HttpMetricSummary]:
        cutoff = time.time() - window_seconds if window_seconds is not None else None
        with self._lock:
            keys = sorted(self._samples)
            output: list[HttpMetricSummary] = []
            for method, route in keys:
                recent = [
                    (duration, status_class)
                    for observed_at, duration, status_class in self._samples[(method, route)]
                    if cutoff is None or observed_at >= cutoff
                ]
                samples = sorted(duration for duration, _ in recent)
                request_count = len(recent)
                error_count = sum(status_class == "5xx" for _, status_class in recent)
                p95_ms: float | None = None
                if samples:
                    index = max(0, math.ceil(len(samples) * 0.95) - 1)
                    p95_ms = round(samples[index] * 1000.0, 3)
                output.append(
                    HttpMetricSummary(
                        route=route,
                        method=method,
                        request_count=request_count,
                        error_count=error_count,
                        p95_ms=p95_ms,
                    )
                )
            return output

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()
            self._duration_sum.clear()
            self._samples.clear()

    def render_prometheus(self) -> str:
        lines = [
            "# HELP duckdock_http_requests_total HTTP requests handled by DuckDock.",
            "# TYPE duckdock_http_requests_total counter",
        ]
        with self._lock:
            for (method, route, status_class), count in sorted(self._counts.items()):
                labels = _labels(method=method, route=route, status_class=status_class)
                lines.append(f"duckdock_http_requests_total{{{labels}}} {count}")
            lines.extend(
                [
                    "# HELP duckdock_http_request_duration_seconds Request latency by route template.",
                    "# TYPE duckdock_http_request_duration_seconds histogram",
                ]
            )
            for (method, route), samples in sorted(self._samples.items()):
                ordered = [duration for _, duration, _ in samples]
                base_labels = _labels(method=method, route=route)
                for bound in _LATENCY_BUCKETS:
                    count = sum(value <= bound for value in ordered)
                    lines.append(
                        "duckdock_http_request_duration_seconds_bucket"
                        f'{{{base_labels},le="{bound:g}"}} {count}'
                    )
                lines.append(
                    "duckdock_http_request_duration_seconds_bucket"
                    f'{{{base_labels},le="+Inf"}} {len(ordered)}'
                )
                lines.append(
                    "duckdock_http_request_duration_seconds_sum"
                    f"{{{base_labels}}} {self._duration_sum[(method, route)]:.9f}"
                )
                lines.append(
                    "duckdock_http_request_duration_seconds_count"
                    f"{{{base_labels}}} {len(ordered)}"
                )
        lines.extend(
            [
                "# HELP duckdock_process_up DuckDock process availability marker.",
                "# TYPE duckdock_process_up gauge",
                "duckdock_process_up 1",
                "",
            ]
        )
        return "\n".join(lines)


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _labels(**values: str) -> str:
    return ",".join(f'{key}="{_escape_label(value)}"' for key, value in values.items())


http_metrics = HttpMetricsRegistry()
