"""Backwards-compatible entrypoint for the internal agent listener.

The implementation moved into the application package
(`drake_api.agents.run_internal_listener`) because the production image
contains `apps/api`, not the repository root — a runner living here was not
in the image at all, and the listener containers could not start.

This file stays because local development, the chart smoke and two test
suites invoke it by path. It holds no logic of its own: two copies of a TLS
bootstrap are two places for it to drift.
"""

from drake_api.agents.run_internal_listener import main

if __name__ == "__main__":
    main()
