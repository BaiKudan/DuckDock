FROM python:3.12-slim

WORKDIR /app

# git: gitpython / skill version storage.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN addgroup --system appuser \
    && adduser --system --ingroup appuser appuser \
    && mkdir -p /repos \
    && chown -R appuser:appuser /app /repos

EXPOSE 8801
USER appuser
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8801"]
