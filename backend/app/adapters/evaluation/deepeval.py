from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from typing import Any

from app.services.evaluation_ports import (
    EphemeralEvaluationCase,
    MetricSpec,
    MetricSummary,
)


class DeepEvalUnavailableError(RuntimeError):
    pass


class UnsupportedDeepEvalMetricError(ValueError):
    pass


class DeepEvalAdapter:
    """Optional, ephemeral DeepEval execution adapter.

    DeepEval is imported only when this adapter runs. This preserves DuckDock's
    optional-dependency boundary and prevents its automatic dotenv loader from
    reading the repository's credentials. Returned values are metric summaries
    only; reasons and case content are deliberately discarded.
    """

    _SUPPORTED_METRICS = {
        "answer_relevancy": "AnswerRelevancyMetric",
        "faithfulness": "FaithfulnessMetric",
    }

    def __init__(
        self,
        *,
        metric_factory: Callable[[MetricSpec], Any] | None = None,
        case_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._metric_factory = metric_factory
        self._case_factory = case_factory

    def _factories(
        self,
    ) -> tuple[Callable[[MetricSpec], Any], Callable[..., Any]]:
        if self._metric_factory is not None and self._case_factory is not None:
            return self._metric_factory, self._case_factory
        os.environ.setdefault("DEEPEVAL_DISABLE_DOTENV", "1")
        try:
            from deepeval import metrics as deepeval_metrics
            from deepeval.test_case import LLMTestCase
        except ImportError as exc:
            raise DeepEvalUnavailableError(
                "DeepEval is not installed in the evaluation worker"
            ) from exc

        def metric_factory(spec: MetricSpec):
            class_name = self._SUPPORTED_METRICS.get(spec.name)
            if class_name is None:
                raise UnsupportedDeepEvalMetricError(
                    f"Unsupported DeepEval metric: {spec.name}"
                )
            metric_class = getattr(deepeval_metrics, class_name)
            return metric_class(threshold=spec.threshold)

        return metric_factory, LLMTestCase

    def evaluate(
        self,
        *,
        case: EphemeralEvaluationCase,
        metrics: Sequence[MetricSpec],
    ) -> list[MetricSummary]:
        if not metrics:
            raise ValueError("At least one metric is required")
        metric_factory, case_factory = self._factories()
        test_case = case_factory(
            input=case.input_text,
            actual_output=case.actual_output,
            expected_output=case.expected_output,
            context=list(case.context) or None,
            retrieval_context=list(case.retrieval_context) or None,
        )
        summaries: list[MetricSummary] = []
        for spec in metrics:
            if not 0 <= spec.threshold <= 1:
                raise ValueError("Metric threshold must be in [0, 1]")
            metric = metric_factory(spec)
            metric.measure(test_case)
            score = float(metric.score)
            summaries.append(
                MetricSummary(
                    name=spec.name,
                    score=score,
                    threshold=spec.threshold,
                    passed=score >= spec.threshold,
                )
            )
        return summaries
