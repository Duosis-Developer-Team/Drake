# Drake cluster agent — pinned, minimal, non-root.
#
# Contract with deploy/agent (the Helm chart):
#   - binary at /usr/local/bin/drake-agent
#   - liveness command: /usr/local/bin/drake-agent healthcheck <loopback:port>
#     (a subcommand of the same binary — no shell, curl, or wget exists in
#     the runtime image at all)
#   - runs as the distroless nonroot user (uid 65532)
#   - static build: works with readOnlyRootFilesystem and drop-ALL caps

FROM golang@sha256:6c5605ab3a9a9fb3c4eafe5b3d63cdbf3881caf113262b67862547b54a9db599 AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY cmd ./cmd
COPY internal ./internal
ARG AGENT_VERSION=0.4.0
RUN CGO_ENABLED=0 go build -trimpath \
    -ldflags "-s -w -X main.agentVersion=${AGENT_VERSION}" \
    -o /out/drake-agent ./cmd/agent

FROM gcr.io/distroless/static-debian12@sha256:f5b485ea962d9bd1186b2f6b3a061191539b905b82ec395de78cbfae51f20e35
COPY --from=build /out/drake-agent /usr/local/bin/drake-agent
USER 65532:65532
ENTRYPOINT ["/usr/local/bin/drake-agent"]
