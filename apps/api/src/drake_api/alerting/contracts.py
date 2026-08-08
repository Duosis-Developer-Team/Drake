"""Loading the reviewed alerting contracts, fail-closed.

Burn-rate profiles, latency thresholds and silence reason codes are
repository-controlled and reviewed like code, exactly as the telemetry
registry and the protection connector contract are. The reason is the same
each time: these are the numbers a verdict is measured against, and a value
that can be supplied at runtime is a value nobody reviewed.

There is no request field anywhere in this sprint that selects a threshold,
edits a profile, or names a window. A frontend cannot decide what "fast
enough" means.
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

CONTRACT_PATH = (
    Path(__file__).resolve().parents[5]
    / "packages"
    / "contracts"
    / "alerting"
    / "slo-profiles.v1.json"
)

ROUTE_FIXTURE_PATH = (
    Path(__file__).resolve().parents[5]
    / "packages"
    / "contracts"
    / "alerting"
    / "alertmanager-route.v1.yaml"
)

RULES_FIXTURE_PATH = (
    Path(__file__).resolve().parents[5]
    / "packages"
    / "contracts"
    / "alerting"
    / "prometheus-rules.v1.yaml"
)

# Used when a definition names no profile. A missing threshold must resolve
# to something reviewed rather than to zero, which would mark every request
# as too slow.
FALLBACK_LATENCY_SECONDS = 1.0


@lru_cache
def load_contract(path: str | None = None) -> dict[str, Any]:
    source = Path(path) if path else CONTRACT_PATH
    document: dict[str, Any] = json.loads(source.read_text())
    if int(document.get("schemaVersion", 0)) != 1:
        raise ValueError("unsupported alerting contract version")
    if not document.get("burnProfiles"):
        raise ValueError("alerting contract declares no burn profile")
    return document


def latency_threshold_seconds(key: str | None, path: str | None = None) -> float:
    """The p95 ceiling for a threshold profile key."""
    document = load_contract(path)
    wanted = key or document.get("defaults", {}).get("latencyThresholdKey")
    for entry in document.get("latencyThresholds", []):
        if entry.get("key") == wanted:
            return float(entry.get("p95Seconds", FALLBACK_LATENCY_SECONDS))
    return FALLBACK_LATENCY_SECONDS


def latency_threshold_keys(path: str | None = None) -> list[str]:
    return [str(entry["key"]) for entry in load_contract(path).get("latencyThresholds", [])]


def silence_reason_codes(path: str | None = None) -> dict[str, str]:
    """The reasons a silence may state. A short, reviewed vocabulary.

    Free text would be a place for someone to write a customer name, an
    incident number from another system, or a URL. The optional note beside
    it is bounded and never rendered as a link.
    """
    return {
        str(entry["key"]): str(entry.get("label", entry["key"]))
        for entry in load_contract(path).get("reasonCodes", [])
    }


def indicator_measurement(indicator: str, path: str | None = None) -> str:
    """How this indicator is actually measured, in one sentence.

    Shown on the SLO screen. A compliance number whose measurement method is
    invisible invites a reader to assume it means something stronger than it
    does.
    """
    for entry in load_contract(path).get("indicators", []):
        if entry.get("indicator") == indicator:
            return str(entry.get("measurement", ""))
    return ""


def indicator_template(indicator: str, path: str | None = None) -> str:
    for entry in load_contract(path).get("indicators", []):
        if entry.get("indicator") == indicator:
            return str(entry.get("sliTemplateKey", ""))
    return ""


def default_burn_profile(path: str | None = None) -> str:
    return str(load_contract(path).get("defaults", {}).get("burnProfileKey", "standard.30d.v1"))
