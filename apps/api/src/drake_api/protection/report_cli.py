"""Report one finished backup to Tracesis. Standard library only.

Run it at the end of the job that took the backup, wherever that job runs:

    python3 report_cli.py \\
        --url https://tracesis.example/ --connector logislot-backup \\
        --secret-file /run/secrets/tracesis-protection \\
        --policy logislot-prod-db --artifact /backups/logislot-20261121.dump \\
        --started 2026-11-21T02:00:00Z --status succeeded \\
        [--site onedrive-primary --offsite]

It sends, signed with the reporter's own secret, what the screen judges a
policy on: the run (started, finished, how it ended), the artifact (its size
and SHA-256, computed here from the file), a checksum integrity check, and
optionally the copy at an offsite site. It reads the dump to hash it and
sends nothing of its content: no path, no host, no credential — the
artifact is named by its file name only.

Exit 0 when every event was accepted, 1 otherwise; the reason is printed as
a bounded code, never a server message.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ENDPOINT = "/v1/protection/ingest/events"


def sign(secret: bytes, timestamp: str, body: bytes) -> str:
    digest = hmac.new(secret, f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"v1={digest}"


def sha256_of(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def events(
    args: argparse.Namespace, checksum: str, size: int, now: datetime
) -> list[dict[str, Any]]:
    run_id = args.run_id or f"{args.policy}-{args.started.strftime('%Y%m%dT%H%M%SZ')}"
    artifact_key = Path(args.artifact).name
    at = now.isoformat()

    def envelope(kind: str, data: dict[str, Any]) -> dict[str, Any]:
        return {"id": f"evt-{uuid.uuid4().hex}", "type": kind, "time": at, "data": data}

    out = [
        envelope(
            "drake.backup.run.completed.v1",
            {
                "policy_key": args.policy,
                "run_id": run_id,
                "status": args.status,
                "started_at": args.started.isoformat(),
                "completed_at": at,
                **({"error_code": args.error_code} if args.error_code else {}),
            },
        )
    ]
    if args.status == "succeeded":
        out += [
            envelope(
                "drake.backup.artifact.observed.v1",
                {
                    "policy_key": args.policy,
                    "run_id": run_id,
                    "artifact_key": artifact_key,
                    "size_bytes": size,
                    "checksum_algorithm": "sha256",
                    "checksum": checksum,
                    "encrypted": bool(args.encrypted),
                    "created_at": at,
                    "presence": "present",
                },
            ),
            envelope(
                "drake.backup.integrity.completed.v1",
                {
                    "artifact_key": artifact_key,
                    "check_key": f"sha256-{run_id}",
                    "method": "checksum",
                    "result": "passed",
                    "checked_at": at,
                },
            ),
        ]
        if args.site:
            out.append(
                envelope(
                    "drake.backup.copy.observed.v1",
                    {
                        "artifact_key": artifact_key,
                        "site_key": args.site,
                        "state": "present",
                        "is_offsite": bool(args.offsite),
                        "size_bytes": size,
                    },
                )
            )
    return out


def send(url: str, connector: str, secret: bytes, event: dict[str, Any], timeout: float) -> str:
    body = json.dumps(event, separators=(",", ":")).encode()
    timestamp = str(int(time.time()))
    request = urllib.request.Request(  # noqa: S310 - the operator's own Tracesis URL
        url.rstrip("/") + ENDPOINT,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Drake-Protection-Connector": connector,
            "X-Drake-Protection-Timestamp": timestamp,
            "X-Drake-Protection-Signature": sign(secret, timestamp, body),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return "accepted" if 200 <= response.status < 300 else f"http_{response.status}"
    except urllib.error.HTTPError as error:
        return f"http_{error.code}"
    except (urllib.error.URLError, TimeoutError, OSError):
        return "unreachable"


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report one finished backup to Tracesis.")
    parser.add_argument("--url", required=True, help="Tracesis base URL")
    parser.add_argument("--connector", required=True, help="reporter key from the contract")
    parser.add_argument("--secret-file", required=True, help="file holding the signing secret")
    parser.add_argument("--policy", required=True, help="policy key from the contract")
    parser.add_argument("--artifact", required=True, help="the backup file just written")
    parser.add_argument("--started", required=True, type=_time, help="when the backup began (ISO)")
    parser.add_argument(
        "--status", default="succeeded", choices=["succeeded", "failed", "cancelled"]
    )
    parser.add_argument("--error-code", default="", help="a short code when it failed")
    parser.add_argument("--run-id", default="", help="defaults to <policy>-<started>")
    parser.add_argument("--site", default="", help="site key the artifact was copied to")
    parser.add_argument("--offsite", action="store_true", help="that site is offsite")
    parser.add_argument("--encrypted", action="store_true")
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args(argv)

    secret = Path(args.secret_file).read_bytes().strip()
    if not secret:
        print("secret_empty", file=sys.stderr)
        return 1
    if args.status == "succeeded":
        checksum, size = sha256_of(Path(args.artifact))
    else:
        checksum, size = "", 0
    ok = True
    for event in events(args, checksum, size, datetime.now(UTC)):
        outcome = send(args.url, args.connector, secret, event, args.timeout)
        print(f"{event['type']}: {outcome}")
        ok = ok and outcome == "accepted"
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
