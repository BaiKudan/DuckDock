#!/bin/sh
set -eu

case "${DUCKDOCK_PUBLIC_HOST:-}" in
  ""|*/*|*:*|*[!A-Za-z0-9.-]*)
    echo "DUCKDOCK_PUBLIC_HOST must be a DNS hostname" >&2
    exit 1
    ;;
esac
case "${DUCKDOCK_MINIO_PUBLIC_HOST:-}" in
  ""|*/*|*:*|*[!A-Za-z0-9.-]*)
    echo "DUCKDOCK_MINIO_PUBLIC_HOST must be a DNS hostname" >&2
    exit 1
    ;;
esac
if [ "$DUCKDOCK_PUBLIC_HOST" = "$DUCKDOCK_MINIO_PUBLIC_HOST" ]; then
  echo "Application and object-store TLS hostnames must be different" >&2
  exit 1
fi

envsubst '${DUCKDOCK_PUBLIC_HOST} ${DUCKDOCK_MINIO_PUBLIC_HOST}' \
  < /etc/nginx/templates/duckdock-tls.conf.template \
  > /tmp/nginx/conf.d/duckdock-tls.conf

exec nginx -g 'daemon off;'
