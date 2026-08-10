# Rebuild upstream Alertmanager 0.33.1 at its signed release commit while
# overriding dependencies with fixes published after the release image.
ARG GO_IMAGE=mirror.gcr.io/library/golang:1.26.5-alpine3.23@sha256:622e56dbc11a8cfe87cafa2331e9a201877271cbff918af53d3be315f3da88cc
ARG UI_BUILDPLATFORM=linux/amd64

FROM ${GO_IMAGE} AS source
RUN apk add --no-cache git \
    && git clone https://github.com/prometheus/alertmanager.git /src \
    && cd /src \
    && git checkout --detach 2c8da51e03f3dbbed24f9711ca2d76aab4eef9c5 \
    && test "$(git rev-parse HEAD)" = "2c8da51e03f3dbbed24f9711ca2d76aab4eef9c5"

# Elm 0.19.1 has no Linux arm64 compiler. Build only the static UI assets under
# emulation, then compile the Go binaries for the target platform below.
FROM --platform=${UI_BUILDPLATFORM} ${GO_IMAGE} AS ui
RUN apk add --no-cache make nodejs npm
COPY --from=source /src /src
RUN cd /src/ui/app \
    && npm ci \
    && npm run build \
    && test -f dist/index.html.gz

FROM ${GO_IMAGE} AS build
ENV CGO_ENABLED=0
RUN apk add --no-cache ca-certificates tzdata
COPY --from=source /src /src
COPY --from=ui /src/ui/app/dist /src/ui/app/dist
RUN cd /src \
    && go mod edit \
        -require=golang.org/x/crypto@v0.52.0 \
        -require=google.golang.org/grpc@v1.82.1 \
    && go mod tidy \
    && go build -trimpath -tags netgo \
        -ldflags '-s -w -X github.com/prometheus/common/version.Version=0.33.1-duckdock.1 -X github.com/prometheus/common/version.Revision=2c8da51e03f3dbbed24f9711ca2d76aab4eef9c5' \
        -o /out/alertmanager ./cmd/alertmanager \
    && go build -trimpath -tags netgo \
        -ldflags '-s -w -X github.com/prometheus/common/version.Version=0.33.1-duckdock.1 -X github.com/prometheus/common/version.Revision=2c8da51e03f3dbbed24f9711ca2d76aab4eef9c5' \
        -o /out/amtool ./cmd/amtool \
    && /out/alertmanager --version \
    && mkdir /alertmanager \
    && chown 65534:65534 /alertmanager

FROM scratch
LABEL org.opencontainers.image.title="DuckDock patched Alertmanager" \
      org.opencontainers.image.version="0.33.1-duckdock.1" \
      org.opencontainers.image.source="https://github.com/prometheus/alertmanager" \
      org.opencontainers.image.revision="2c8da51e03f3dbbed24f9711ca2d76aab4eef9c5" \
      org.opencontainers.image.licenses="Apache-2.0"
COPY --from=build /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY --from=build /etc/passwd /etc/passwd
COPY --from=build /etc/group /etc/group
COPY --from=build /usr/share/zoneinfo /usr/share/zoneinfo
COPY --from=build /out/alertmanager /bin/alertmanager
COPY --from=build /out/amtool /bin/amtool
COPY --from=build /src/LICENSE /LICENSE
COPY --from=build --chown=65534:65534 /alertmanager /alertmanager
USER 65534:65534
EXPOSE 9093
ENTRYPOINT ["/bin/alertmanager"]
CMD ["--config.file=/etc/alertmanager/alertmanager.yml", "--storage.path=/alertmanager"]
