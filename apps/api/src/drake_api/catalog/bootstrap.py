"""Local/test catalog fixture bootstrap — NEVER a production seed.

Loads the fictional contract fixtures (project-alpha, project-beta) through
the catalog application service, so all scope/transaction invariants apply.
Fails closed outside local/test; not exposed through any API.

Usage: uv run python -m drake_api.catalog.bootstrap
"""

import asyncio
import sys
import uuid
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from drake_api.catalog.service import CatalogService
from drake_api.settings import get_settings

FIXTURES_DIR = Path(__file__).resolve().parents[5] / "packages" / "contracts" / "fixtures" / "valid"
FIXTURE_FILES = ("project-alpha.yaml", "project-beta.yaml")


async def _load_manifest(service: CatalogService, manifest: dict[str, Any]) -> str:
    connection = service._connection
    project_key = manifest["metadata"]["name"]

    existing = (
        await connection.execute(
            text("SELECT id FROM projects WHERE project_key = :key"), {"key": project_key}
        )
    ).first()
    if existing is not None:
        return f"{project_key}: already present"

    spec = manifest["spec"]
    repository = spec["repository"]
    project = await service.create_project(
        project_key,
        manifest["metadata"].get("displayName", project_key),
        repo_provider=repository["provider"],
        repo_owner=repository["owner"],
        repo_name=repository["name"],
        default_branch=repository.get("defaultBranch", ""),
        criticality=max(
            (env["criticality"] for env in spec["environments"]),
            key=["low", "medium", "high", "critical"].index,
        ),
        tenant_model=spec["tenantModel"]["mode"],
        owners=[(owner["team"], owner.get("role", "primary")) for owner in spec["owners"]],
        source_ref=f"fixture:{project_key}",
        source_revision="fixture-v1",
    )

    cluster_ids: dict[str, uuid.UUID] = {}
    for environment in spec["environments"]:
        cluster_id = None
        if environment["runtime"] == "kubernetes":
            cluster_ref = environment["clusterRef"]
            if cluster_ref not in cluster_ids:
                row = (
                    await connection.execute(
                        text("SELECT id FROM clusters WHERE cluster_ref = :ref"),
                        {"ref": cluster_ref},
                    )
                ).first()
                if row is not None:
                    cluster_ids[cluster_ref] = uuid.UUID(str(row[0]))
                else:
                    created = await service.create_cluster(
                        cluster_ref,
                        cluster_ref.replace("-", " ").title(),
                        source_ref=f"fixture:{project_key}",
                        source_revision="fixture-v1",
                    )
                    cluster_ids[cluster_ref] = created.id
            cluster_id = cluster_ids[cluster_ref]

        environment_entity = await service.create_environment(
            project.id,
            project_key,
            environment["name"],
            runtime=environment["runtime"],
            branch=environment.get("branch", ""),
            criticality=environment["criticality"],
            cluster_id=cluster_id,
            namespace=environment.get("namespace"),
            source_ref=f"fixture:{project_key}",
            source_revision="fixture-v1",
        )

        for service_spec in spec["services"]:
            row = (
                await connection.execute(
                    text(
                        "SELECT id FROM service_definitions "
                        "WHERE project_id = :project_id AND service_key = :key"
                    ),
                    {"project_id": project.id, "key": service_spec["name"]},
                )
            ).first()
            if row is not None:
                service_id = uuid.UUID(str(row[0]))
            else:
                service_id = await service.create_service_definition(
                    project.id,
                    service_spec["name"],
                    component=service_spec["component"],
                    runtime=service_spec["runtime"],
                    metrics_profile=service_spec["metricsProfile"],
                    workload_selector=service_spec.get("workloadSelector") or {},
                    health=service_spec.get("health") or {},
                    source_ref=f"fixture:{project_key}",
                    source_revision="fixture-v1",
                )
            await service.bind_service(
                environment_entity.id,
                service_id,
                project_key=project_key,
                environment_key=environment["name"],
                service_key=service_spec["name"],
            )

    # Placeholder integrations at the project scope: honestly not_configured.
    for integration_type in ("prometheus", "github", "cluster-agent", "backup-reporter"):
        await service.register_integration(integration_type, project.scope_id)

    return f"{project_key}: loaded"


async def bootstrap() -> list[str]:
    settings = get_settings()
    if settings.env not in ("local", "test"):
        raise RuntimeError("catalog fixture bootstrap is local/test only and refuses to run here")

    engine = create_async_engine(settings.database_url)
    results: list[str] = []
    try:
        async with engine.begin() as connection:
            service = CatalogService(connection, source_kind="fixture")
            for filename in FIXTURE_FILES:
                manifest = yaml.safe_load((FIXTURES_DIR / filename).read_text())
                results.append(await _load_manifest(service, manifest))
    finally:
        await engine.dispose()
    return results


def main() -> int:
    for line in asyncio.run(bootstrap()):
        sys.stdout.write(line + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
