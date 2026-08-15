# Drake cluster agent — pinned, minimal, non-root.
#
# Contract with deploy/agent (the Helm chart):
#   - binary at /usr/local/bin/drake-agent
#   - liveness command: /usr/local/bin/drake-agent healthcheck <loopback:port>
#     (a subcommand of the same binary — no shell, curl, or wget exists in
#     the runtime image at all)
#   - runs as the distroless nonroot user (uid 65532)
#   - static build: works with readOnlyRootFilesystem and drop-ALL caps

# golang:1.26.6. The official images set GOTOOLCHAIN=local, so this digest —
# not the `go` directive in go.mod — decides which toolchain compiles the
# agent. Bump the two together or the build stops resolving.
FROM golang@sha256:640a234f4bea3e399c056b7b8f9c667c4939befae8db2f14e9785e16eccd4205 AS build
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
