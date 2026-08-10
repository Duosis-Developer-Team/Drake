"""Register a cluster from inside the running API image.

    python -m drake_api.catalog.register_cluster_cli \
        --cluster-ref duosis-prod-1 \
        --display-name "Duosis Production" \
        --actor <identity-uuid>

Why this exists: the first cluster has to be registered by whoever is doing
the rollout, and that person holds cluster credentials — not necessarily a
Drake browser session, and certainly not one before any UI has ever listed a
cluster. `POST /v1/clusters` is the normal path; this is the same call for
the moment before that path is usable.

It is NOT a database shell. It calls `register_cluster`, which calls the
catalog service — same validation, same idempotency, same audit event, same
transaction. There is no statement argument, no update, no delete. Adding
one would turn an operator tool into an unaudited mutation surface, which is
the thing this file is shaped to avoid.

The actor is explicit and required: an audit row whose actor is "the CLI"
records nothing anybody can follow up on.
"""

import argparse
import asyncio
import uuid

from sqlalchemy import text

from drake_api.catalog.router_clusters import register_cluster
from drake_api.db import get_engine
from drake_api.settings import get_settings


async def _run(cluster_ref: str, display_name: str, site: str, actor: uuid.UUID) -> int:
    settings = get_settings()
    engine = get_engine(settings)
    async with engine.connect() as connection:
        known = (
            await connection.execute(text("SELECT 1 FROM identities WHERE id = :id"), {"id": actor})
        ).first()
    if known is None:
        # Fail closed. An audit event pointing at an identity that does not
        # exist is worse than no tool at all.
        print("refused: the actor identity is not a known Drake identity")
        return 2
    result = await register_cluster(
        engine,
        cluster_ref=cluster_ref,
        display_name=display_name,
        site=site,
        actor_identity_id=actor,
    )
    verb = "registered" if result["created"] else "already registered"
    print(f"{verb}: {result['cluster_ref']} ({result['id']})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Register a Kubernetes cluster with Drake.")
    parser.add_argument("--cluster-ref", required=True)
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--site", default="")
    parser.add_argument("--actor", required=True, help="the operator's Drake identity id")
    args = parser.parse_args()
    try:
        actor = uuid.UUID(args.actor)
    except ValueError:
        print("refused: --actor must be a Drake identity UUID")
        return 2
    try:
        return asyncio.run(_run(args.cluster_ref, args.display_name, args.site, actor))
    except ValueError as error:
        # Bounded, and never the underlying SQL.
        print(f"refused: {error}")
        return 2


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
