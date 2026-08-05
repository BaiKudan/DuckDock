FROM nginx:1.29-alpine3.23@sha256:5616878291a2eed594aee8db4dade5878cf7edcb475e59193904b198d9b830de AS runtime

RUN apk upgrade --no-cache \
    && apk del --no-cache curl \
    && sed -i '/^user /d' /etc/nginx/nginx.conf \
    && rm -f /etc/nginx/conf.d/default.conf \
    && mkdir -p /tmp/nginx/conf.d /tmp/nginx/cache \
    && chown -R nginx:nginx /tmp/nginx

COPY nginx-main.conf /etc/nginx/nginx.conf
COPY duckdock-tls.conf.template /etc/nginx/templates/duckdock-tls.conf.template
COPY entrypoint.sh /usr/local/bin/duckdock-tls-entrypoint

USER nginx
EXPOSE 8443
ENTRYPOINT ["/usr/local/bin/duckdock-tls-entrypoint"]
