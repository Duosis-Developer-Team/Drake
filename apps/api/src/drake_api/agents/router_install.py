"""The install file and command for one cluster (ADR-0037 §1).

`POST /v1/clusters/{id}/agent-install` is what the Connect a cluster screen
calls. It mints a one-time enrollment token and returns everything the
customer runs:

- `values_yaml`: the cluster's own values for the tracesis-agent chart —
  its id and name (written into all three sub-charts, which the chart
  checks agree), the two public listener URLs, the listener CA (public
  material) and the token;
- `command`: the one `helm upgrade --install` line that applies it.

The token is the only secret in the file. It is shown exactly once, here,
expires in ten minutes and is spent by the agent's first start.

Only clusters whose telemetry stays in the cluster (`telemetry_location =
'cluster'`) get an install file: generating one for a cluster read from the
central store would install a second, unused store beside it.
"""

import uuid
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text

from drake_api.agents.router_tokens import TOKEN_TTL_SECONDS_DEFAULT, issue_enrollment_token
from drake_api.auth.dependencies import AuthContext, require_auth, require_csrf
from drake_api.catalog.authz import visible_scope_ids
from drake_api.db import get_engine
from drake_api.settings import Settings

router = APIRouter(prefix="/v1", tags=["agent-install"])

RELEASE = "tracesis-agent"
NAMESPACE = "tracesis"


class _Dumper(yaml.SafeDumper):
    """Multi-line strings (the CA) as literal blocks, so the file reads like
    the PEM it carries."""


def _str(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _str)


def install_configured(settings: Settings) -> bool:
    return bool(
        settings.agent_public_enrollment_url
        and settings.agent_public_ingest_url
        and settings.agent_server_ca_file
        and settings.agent_chart_ref
        and settings.agent_chart_version
    )


def install_values(
    settings: Settings, *, cluster_id: str, cluster_ref: str, server_ca: str, token: str
) -> dict[str, Any]:
    return {
        "cluster": {"id": cluster_id, "name": cluster_ref},
        "tracesis": {"serverCA": server_ca.strip() + "\n"},
        "enrollment": {"token": token},
        "clusterAgent": {
            "clusterId": cluster_id,
            "clusterName": cluster_ref,
            "apiBaseUrl": settings.agent_public_ingest_url,
            "enrollmentBaseUrl": settings.agent_public_enrollment_url,
        },
        "telemetry": {"clusterName": cluster_ref},
    }


def install_command(settings: Settings, file_name: str) -> str:
    return (
        f"helm upgrade --install {RELEASE} {settings.agent_chart_ref} "
        f"--version {settings.agent_chart_version} "
        f"--namespace {NAMESPACE} --create-namespace -f {file_name}"
    )


@router.get("/agent-install")
async def agent_install_status(
    request: Request, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    """Whether this Tracesis can connect clusters other than its own.

    Asked before anything is created, so the Connect screen can say "not
    yet" instead of registering a cluster it then cannot install.
    """
    settings: Settings = request.app.state.settings
    return {
        "configured": install_configured(settings),
        "chart_ref": settings.agent_chart_ref,
        "chart_version": settings.agent_chart_version or None,
    }


@router.post("/clusters/{cluster_id}/agent-install", status_code=201)
async def create_agent_install(
    request: Request,
    cluster_id: uuid.UUID,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        # The same authority as an enrollment token, because this mints one.
        manage_scopes = await visible_scope_ids(connection, auth.principal, "integration.manage")
        row = (
            await connection.execute(
                text(
                    "SELECT scope_id, cluster_ref, telemetry_location FROM clusters "
                    "WHERE id = :id AND lifecycle = 'active'"
                ),
                {"id": cluster_id},
            )
        ).first()
    if row is None or row[0] not in manage_scopes:
        raise HTTPException(status_code=404, detail="not found")
    if row[2] != "cluster":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "cluster_reads_central_store",
                "message": "This cluster is read from Tracesis's own store; it needs no install.",
            },
        )
    if not install_configured(settings):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "agent_install_not_configured",
                "message": "This Tracesis has no public agent listener configured yet.",
            },
        )
    try:
        server_ca = Path(settings.agent_server_ca_file).read_text(encoding="utf-8")
    except OSError as error:
        # The path is configuration and is not echoed.
        raise HTTPException(
            status_code=503, detail={"code": "agent_server_ca_unreadable"}
        ) from error
    if "BEGIN CERTIFICATE" not in server_ca or "PRIVATE KEY" in server_ca:
        # A CA bundle only. A file that holds a key is the wrong file, and it
        # must never reach a response.
        raise HTTPException(status_code=503, detail={"code": "agent_server_ca_unreadable"})

    token, expires_at = await issue_enrollment_token(engine, cluster_id, auth.principal.identity_id)
    cluster_ref = str(row[1])
    file_name = f"tracesis-agent-{cluster_ref}.yaml"
    values = install_values(
        settings,
        cluster_id=str(cluster_id),
        cluster_ref=cluster_ref,
        server_ca=server_ca,
        token=token,
    )
    header = (
        f"# tracesis-agent for cluster {cluster_ref}, generated by Tracesis.\n"
        f"# The enrollment token below is single-use and expires at "
        f"{expires_at.isoformat()}.\n"
        f"# Install with:\n#   {install_command(settings, file_name)}\n"
    )
    return {
        "cluster_id": str(cluster_id),
        "cluster_ref": cluster_ref,
        "file_name": file_name,
        "values_yaml": header + yaml.dump(values, Dumper=_Dumper, sort_keys=False, width=1000),
        "command": install_command(settings, file_name),
        "chart_ref": settings.agent_chart_ref,
        "chart_version": settings.agent_chart_version,
        "namespace": NAMESPACE,
        "expires_at": expires_at.isoformat(),
        "ttl_seconds": TOKEN_TTL_SECONDS_DEFAULT,
    }
