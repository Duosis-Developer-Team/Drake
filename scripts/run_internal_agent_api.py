"""Run the internal agent API listener (local/test/CI).

Serves ONLY the internal agent app over TLS:
- server certificate for the listener,
- CERT_REQUIRED client verification against the Drake Agent CA
  (real mTLS handshake; enrollment being pre-certificate uses the same
  listener in local/test where CERT_OPTIONAL is configured explicitly).

Production runs this behind the dedicated internal gateway per ADR-0016;
this script never serves the public app.
"""

import argparse
import ssl

import uvicorn
from drake_api.agents.internal_app import create_internal_agent_app


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
    args = parser.parse_args()

    cert_reqs = ssl.CERT_OPTIONAL if args.client_cert_optional else ssl.CERT_REQUIRED
    uvicorn.run(
        create_internal_agent_app(),
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
