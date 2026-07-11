FROM python:3.12-slim

WORKDIR /workspace

COPY scripts/duckdock_analysis_worker.py ./scripts/duckdock_analysis_worker.py

RUN addgroup --system appuser \
    && adduser --system --ingroup appuser appuser \
    && mkdir -p /tmp/duckdock-analysis-worker \
    && chown -R appuser:appuser /workspace /tmp/duckdock-analysis-worker

USER appuser

ENTRYPOINT ["python", "/workspace/scripts/duckdock_analysis_worker.py"]
