"""Run an internal agent API listener. Never the public app.

This lives in the application package, not in `scripts/`, because it is
code the production image has to RUN. The API image copies `apps/api` and
`packages` and nothing else, so a runner at the repository root is simply
absent from it — the listener containers exited immediately, the readiness
probe never passed, and `helm upgrade --atomic` rolled the release back.

A deployable entrypoint belongs with the application it starts.

Production runs this TWICE, from one image, with different surfaces:

    --surface enrollment   server-authenticated TLS, no client certificate
                           asked for, serving only POST /enroll
    --surface ingest       CERT_REQUIRED mutual TLS against the Agent CA,
                           serving heartbeat, inventory and renewal

The split is the honest answer to a real asymmetry: an agent enrolling for
the first time has no certificate, and everything after that must present
one. One listener cannot be both, and a listener that merely tolerates a
missing certificate would move the guarantee into application code — which
on this stack cannot even see the peer certificate.

`--surface all` keeps a single listener for local, test and CI.
"""

import argparse
import ssl

import uvicorn

from drake_api.agents.internal_app import SURFACES, create_internal_agent_app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8143)
    parser.add_argument("--tls-cert", required=True)
    parser.add_argument("--tls-key", required=True)
    parser.add_argument("--client-ca", required=True)
    parser.add_argument(
        "--client-cert-optional",
        action="store_true",
        help="CERT_OPTIONAL (local/test: lets enrollment happen pre-certificate)",
    )
    parser.add_argument("--surface", default="all", choices=SURFACES)
    args = parser.parse_args()

    if args.surface == "enrollment":
        # No client certificate exists yet, so asking for one would refuse
        # every first enrollment. This listener is why the ingest listener
        # can demand one without exception.
        cert_reqs = ssl.CERT_NONE
    elif args.client_cert_optional:
        cert_reqs = ssl.CERT_OPTIONAL
    else:
        cert_reqs = ssl.CERT_REQUIRED
    uvicorn.run(
        create_internal_agent_app(surface=args.surface),
        host=args.host,
        port=args.port,
        ssl_certfile=args.tls_cert,
        ssl_keyfile=args.tls_key,
        ssl_ca_certs=args.client_ca,
        ssl_cert_reqs=cert_reqs,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
