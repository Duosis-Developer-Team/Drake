"""Telemetry registry loader (fail-closed).

Loads the versioned metric / query-template / dashboard-template registries
from ``packages/contracts/registry`` (repository-controlled, reviewed like
code), re-validates the invariants the contracts package enforces in CI —
duplicates, cross-references, forbidden labels, placeholder allowlist,
deterministic ordering — and refuses to serve telemetry if anything is off.
The canonical content hash participates in every cache key.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

REGISTRY_DIR = Path(__file__).resolve().parents[5] / "packages" / "contracts" / "registry"

# Mirror of the contracts-package denylist (single reviewed source in TS;
# re-enforced here so a bypassed CI cannot smuggle labels into the runtime).
FORBIDDEN_LABELS = frozenset(
    {
        "email",
        "user_id",
        "full_name",
        "request_id",
        "trace_id",
        "span_id",
        "raw_path",
        "route",
        "path",
        "url",
        "query",
        "query_string",
        "sql",
        "ip",
        "client_ip",
        "tenant_name",
        "display_name",
        "error_message",
        "filename",
        "artifact_id",
        "git_sha",
        "commit_sha",
    }
)

_KEY_SHAPE = re.compile(r"^[a-z0-9]([a-z0-9_.-]{0,62}[a-z0-9])?$")
_LABEL_SHAPE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_PLACEHOLDER = re.compile(r"\{\{([a-z_]+)\}\}")
_ALLOWED_PLACEHOLDERS = frozenset({"matchers", "window"})
# Every value a template may match on. All of them come from resolved
# server-side state — a binding row or a catalog row — never from a caller,
# which is what keeps a template from becoming a free-text query.
_MATCHER_SOURCES = frozenset(
    {
        "project_key",
        "environment_key",
        "service_key",
        "cluster_ref",
        "namespace",
        "workload_name",
        "workload_kind",
    }
)

# Global hard ceilings — templates may narrow, never widen.
GLOBAL_MAX_RANGE_SECONDS = 30 * 24 * 3600
GLOBAL_MAX_TIMEOUT_SECONDS = 10.0
GLOBAL_MAX_SERIES = 200
GLOBAL_MAX_POINTS = 20_000


class RegistryError(RuntimeError):
    """The registry is malformed; telemetry must not be served."""


@dataclass(frozen=True)
class MetricDefinition:
    key: str
    version: int
    metric_type: str
    unit: str
    source_type: str
    allowed_input_labels: tuple[str, ...]
    allowed_output_labels: tuple[str, ...]
    profiles: tuple[str, ...]


@dataclass(frozen=True)
class QueryTemplate:
    key: str
    version: int
    metric_key: str
    metric_version: int
    provider_type: str
    expression: str
    scope_types: tuple[str, ...]
    permission: str
    matchers: tuple[tuple[str, str], ...]  # (label, source)
    parameters: dict[str, Any] = field(compare=False)
    max_range_seconds: int
    min_step_seconds: int
    max_series: int
    max_points: int
    timeout_seconds: float
    fresh_ttl_seconds: int
    stale_ttl_seconds: int
    output_unit: str
    output_labels: tuple[str, ...]
    empty_semantics: str


@dataclass(frozen=True)
class DashboardTemplate:
    key: str
    version: int
    title: str
    profiles: tuple[str, ...]
    scope_types: tuple[str, ...]
    summary: str
    raw: dict[str, Any] = field(compare=False)


@dataclass(frozen=True)
class TelemetryRegistry:
    metrics: dict[str, MetricDefinition]
    templates: dict[str, QueryTemplate]
    dashboards: dict[str, DashboardTemplate]
    content_hash: str


def _canonical(value: Any) -> Any:
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    if isinstance(value, dict):
        return {key: _canonical(value[key]) for key in sorted(value)}
    return value


def _content_hash(documents: dict[str, Any]) -> str:
    # Matches packages/contracts/src/registry.ts registryContentHash():
    # sha256 over sorted-key JSON of the combined registry.
    canonical = json.dumps(_canonical(documents), separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RegistryError(message)


def _load_json(base: Path, name: str) -> dict[str, Any]:
    path = base / name
    _require(path.is_file(), f"registry file missing: {name}")
    try:
        document = json.loads(path.read_text())
    except json.JSONDecodeError as error:
        raise RegistryError(f"registry file unparseable: {name}") from error
    if not isinstance(document, dict):
        raise RegistryError(f"registry root must be an object: {name}")
    return document


def _labels_ok(labels: list[str], where: str) -> tuple[str, ...]:
    for label in labels:
        _require(bool(_LABEL_SHAPE.fullmatch(label)), f"{where}: malformed label {label!r}")
        _require(label not in FORBIDDEN_LABELS, f"{where}: forbidden label {label!r}")
    return tuple(labels)


def load_registry(registry_dir: Path | None = None) -> TelemetryRegistry:
    base = registry_dir or REGISTRY_DIR
    catalog_doc = _load_json(base, "metric-catalog.json")
    templates_doc = _load_json(base, "query-templates.json")
    dashboards_doc = _load_json(base, "dashboard-templates.json")

    metrics: dict[str, MetricDefinition] = {}
    ordered_metric_ids: list[str] = []
    for entry in catalog_doc.get("metrics", []):
        key, version = str(entry.get("key", "")), entry.get("version")
        where = f"metric {key!r}"
        _require(bool(_KEY_SHAPE.fullmatch(key)), f"{where}: malformed key")
        _require(isinstance(version, int) and version >= 1, f"{where}: bad version")
        metric_id = f"{key}@{version}"
        _require(metric_id not in metrics, f"{where}: duplicate key/version")
        ordered_metric_ids.append(metric_id)
        metrics[metric_id] = MetricDefinition(
            key=key,
            version=version,
            metric_type=str(entry.get("type", "")),
            unit=str(entry.get("unit", "")),
            source_type=str(entry.get("sourceType", "")),
            allowed_input_labels=_labels_ok(entry.get("allowedInputLabels", []), where),
            allowed_output_labels=_labels_ok(entry.get("allowedOutputLabels", []), where),
            profiles=tuple(entry.get("profiles", [])),
        )
    _require(ordered_metric_ids == sorted(ordered_metric_ids), "metric catalog is not sorted")

    templates: dict[str, QueryTemplate] = {}
    ordered_template_ids: list[str] = []
    for entry in templates_doc.get("templates", []):
        key, version = str(entry.get("key", "")), entry.get("version")
        where = f"template {key!r}"
        _require(bool(_KEY_SHAPE.fullmatch(key)), f"{where}: malformed key")
        _require(isinstance(version, int) and version >= 1, f"{where}: bad version")
        template_id = f"{key}@{version}"
        _require(template_id not in templates, f"{where}: duplicate key/version")
        ordered_template_ids.append(template_id)

        metric = metrics.get(f"{entry.get('metricKey')}@{entry.get('metricVersion')}")
        _require(metric is not None, f"{where}: unknown metric reference")
        assert metric is not None
        _require(
            metric.source_type != "snapshot",
            f"{where}: snapshot metrics cannot back provider query templates",
        )

        expression = str(entry.get("expression", ""))
        for match in _PLACEHOLDER.finditer(expression):
            _require(
                match.group(1) in _ALLOWED_PLACEHOLDERS,
                f"{where}: unknown placeholder {match.group(1)!r}",
            )
        _require("{{matchers}}" in expression, f"{where}: expression missing scope matchers")
        for label in FORBIDDEN_LABELS:
            _require(
                re.search(rf"[({{,\s]{label}\s*=", expression) is None,
                f"{where}: forbidden label {label!r} in expression",
            )

        matchers: list[tuple[str, str]] = []
        for matcher in entry.get("matchers", []):
            label, source = str(matcher.get("label", "")), str(matcher.get("source", ""))
            _require(bool(_LABEL_SHAPE.fullmatch(label)), f"{where}: malformed matcher label")
            _require(label in metric.allowed_input_labels, f"{where}: matcher label not allowed")
            _require(source in _MATCHER_SOURCES, f"{where}: unknown matcher source {source!r}")
            matchers.append((label, source))
        _require(0 < len(matchers) <= 8, f"{where}: matcher count out of bounds")

        budgets = entry.get("budgets", {})
        cache = entry.get("cache", {})
        output = entry.get("output", {})
        semantics = entry.get("semantics", {})
        output_labels = _labels_ok(list(output.get("labels", [])), where)
        for label in output_labels:
            _require(
                label in metric.allowed_output_labels,
                f"{where}: output label {label!r} not allowed by metric",
            )

        template = QueryTemplate(
            key=key,
            version=version,
            metric_key=metric.key,
            metric_version=metric.version,
            provider_type=str(entry.get("providerType", "")),
            expression=expression,
            scope_types=tuple(entry.get("scopeTypes", [])),
            permission=str(entry.get("permission", "")),
            matchers=tuple(matchers),
            parameters=dict(entry.get("parameters", {})),
            max_range_seconds=int(budgets.get("maxRangeSeconds", 0)),
            min_step_seconds=int(budgets.get("minStepSeconds", 0)),
            max_series=int(budgets.get("maxSeries", 0)),
            max_points=int(budgets.get("maxPoints", 0)),
            timeout_seconds=float(budgets.get("timeoutSeconds", 0)),
            fresh_ttl_seconds=int(cache.get("freshTtlSeconds", 0)),
            stale_ttl_seconds=int(cache.get("staleTtlSeconds", 0)),
            output_unit=str(output.get("unit", "")),
            output_labels=output_labels,
            empty_semantics=str(semantics.get("empty", "")),
        )
        _require(template.provider_type == "prometheus", f"{where}: unsupported provider")
        _require(template.permission == "telemetry.query", f"{where}: unexpected permission")
        _require(
            0 < template.max_range_seconds <= GLOBAL_MAX_RANGE_SECONDS,
            f"{where}: range budget outside the global ceiling",
        )
        _require(
            0 < template.timeout_seconds <= GLOBAL_MAX_TIMEOUT_SECONDS,
            f"{where}: timeout outside the global ceiling",
        )
        _require(
            0 < template.max_series <= GLOBAL_MAX_SERIES,
            f"{where}: series budget outside the global ceiling",
        )
        _require(
            0 < template.max_points <= GLOBAL_MAX_POINTS,
            f"{where}: point budget outside the global ceiling",
        )
        _require(template.min_step_seconds >= 1, f"{where}: min step must be positive")
        _require(
            template.fresh_ttl_seconds >= 1 and template.stale_ttl_seconds >= 1,
            f"{where}: cache TTLs must be positive",
        )
        _require(
            # `workload` scopes a query to one bound workload; its source
            # values come from a binding row, not from the caller.
            set(template.scope_types) <= {"environment", "service", "cluster", "workload"},
            f"{where}: unsupported scope type",
        )
        templates[template_id] = template
    _require(ordered_template_ids == sorted(ordered_template_ids), "query templates are not sorted")

    dashboards: dict[str, DashboardTemplate] = {}
    ordered_dashboard_ids: list[str] = []
    template_keys = {template.key for template in templates.values()}
    for entry in dashboards_doc.get("dashboards", []):
        key, version = str(entry.get("key", "")), entry.get("version")
        where = f"dashboard {key!r}"
        _require(bool(_KEY_SHAPE.fullmatch(key)), f"{where}: malformed key")
        _require(isinstance(version, int) and version >= 1, f"{where}: bad version")
        dashboard_id = f"{key}@{version}"
        _require(dashboard_id not in dashboards, f"{where}: duplicate key/version")
        ordered_dashboard_ids.append(dashboard_id)
        profiles = tuple(entry.get("profiles", []))
        for section in entry.get("sections", []):
            for widget in section.get("widgets", []):
                _require(
                    widget.get("queryTemplateKey") in template_keys,
                    f"{where}: unknown query template {widget.get('queryTemplateKey')!r}",
                )
                required_profile = widget.get("requiredProfile")
                _require(
                    required_profile is None or required_profile in profiles,
                    f"{where}: widget requiredProfile outside dashboard profiles",
                )
        dashboards[dashboard_id] = DashboardTemplate(
            key=key,
            version=version,
            title=str(entry.get("title", "")),
            profiles=profiles,
            scope_types=tuple(entry.get("scopeTypes", [])),
            summary=str(entry.get("summary", "")),
            raw=entry,
        )
    _require(ordered_dashboard_ids == sorted(ordered_dashboard_ids), "dashboards are not sorted")

    return TelemetryRegistry(
        metrics=metrics,
        templates=templates,
        dashboards=dashboards,
        content_hash=_content_hash(
            {
                "metricCatalog": catalog_doc,
                "queryTemplates": templates_doc,
                "dashboardTemplates": dashboards_doc,
            }
        ),
    )


@lru_cache
def get_registry() -> TelemetryRegistry:
    return load_registry()


def find_template(registry: TelemetryRegistry, key: str) -> QueryTemplate | None:
    """Latest version of a template by key (deterministic: highest version)."""
    candidates = [t for t in registry.templates.values() if t.key == key]
    if not candidates:
        return None
    return max(candidates, key=lambda t: t.version)


def find_dashboard(registry: TelemetryRegistry, key: str) -> DashboardTemplate | None:
    candidates = [d for d in registry.dashboards.values() if d.key == key]
    if not candidates:
        return None
    return max(candidates, key=lambda d: d.version)
