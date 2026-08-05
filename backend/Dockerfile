FROM python:3.12.13-alpine3.23@sha256:601d3d3797e90e2534782e69c85fafb7971b43f24c7b1b079b7e48dd435e458d AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Production is MySQL-only. Remove the unused sqlite extension because the
# upstream Python base currently carries unfixed sqlite advisories.
RUN apk upgrade --no-cache \
    && apk del --no-network .python-rundeps \
    && apk add --no-cache --virtual .duckdock-python-rundeps \
        so:libbz2.so.1 so:libcrypto.so.3 so:libffi.so.8 \
        so:libgdbm.so.6 so:libgdbm_compat.so.4 so:liblzma.so.5 \
        so:libncursesw.so.6 so:libnsl.so.3 so:libpanelw.so.6 \
        so:libreadline.so.8 so:libssl.so.3 so:libtirpc.so.3 \
        so:libuuid.so.1 so:libz.so.1 \
    && rm -rf /usr/local/lib/python3.12/sqlite3 \
        /usr/local/lib/python3.12/lib-dynload/_sqlite3*.so

COPY requirements.lock .
RUN pip install --disable-pip-version-check --no-cache-dir \
        --require-hashes -r requirements.lock

COPY . .
RUN addgroup -S -g 10001 appuser \
    && adduser -S -D -H -u 10001 -G appuser appuser \
    && mkdir -p /repos \
    && chown -R appuser:appuser /app /repos

FROM scratch

ARG DUCKDOCK_VERSION=2.0.0-rc.1
LABEL org.opencontainers.image.title="DuckDock backend" \
      org.opencontainers.image.version="${DUCKDOCK_VERSION}" \
      org.opencontainers.image.source="https://github.com/BaiKudan/DuckDock"

COPY --from=runtime / /

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
EXPOSE 8801
USER 10001:10001
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8801"]
