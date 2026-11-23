# Tracesis Collector (ADR-0037 §5): ocb build of builder-config.yaml.
#
# Same base images, pinned the same way, as the cluster agent.
FROM golang@sha256:957952ae74dc010608717144399a59a2c6e82ea03d63be1c7ff6577c5a135f93 AS build
WORKDIR /build
ARG OCB_VERSION=v0.162.0
RUN CGO_ENABLED=0 go install go.opentelemetry.io/collector/cmd/builder@${OCB_VERSION}
COPY builder-config.yaml ./
RUN CGO_ENABLED=0 builder --config=builder-config.yaml --skip-strict-versioning=false \
    --ldflags="-s -w"
# The licence travels with the binary: the Collector's own copy, from the
# module the build just compiled.
RUN mkdir -p /out/licenses \
 && cp "$(go env GOMODCACHE)/go.opentelemetry.io/collector/receiver/otlpreceiver@v0.162.0/LICENSE" \
       /out/licenses/LICENSE-opentelemetry-collector
COPY NOTICE /out/licenses/NOTICE

FROM gcr.io/distroless/static-debian12@sha256:f5b485ea962d9bd1186b2f6b3a061191539b905b82ec395de78cbfae51f20e35
COPY --from=build /build/out/tracesis-collector /tracesis-collector
COPY --from=build /out/licenses /licenses
USER 65532:65532
EXPOSE 4317 4318 13133
ENTRYPOINT ["/tracesis-collector"]
