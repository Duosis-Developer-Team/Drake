"""Provider response normalization into the typed safe envelope.

The provider payload is never proxied. Series labels must sit inside the
template's output allowlist — an unexpected label means the template is
under-aggregated and the response is refused (fail-closed contract error).
Non-finite values are never coerced to 0: they become null points with a
``partial`` flag and a bounded warning code. Ordering is deterministic.
"""

import math
from typing import Any

from drake_api.telemetry.provider import ProviderContractError, RangeQueryResult
from drake_api.telemetry.registry import QueryTemplate


def normalize_matrix(
    template: QueryTemplate, raw: RangeQueryResult
) -> tuple[list[dict[str, Any]], bool, list[str]]:
    """→ (sorted safe series, partial flag, bounded warning codes)."""
    if len(raw.result) > template.max_series:
        raise ProviderContractError("series_budget_exceeded")

    allowed = set(template.output_labels)
    partial = False
    warnings: set[str] = set()
    series_out: list[dict[str, Any]] = []
    total_points = 0

    for entry in raw.result:
        labels: dict[str, str] = {}
        for name, value in entry["metric"].items():
            if name.startswith("__"):
                continue
            if name not in allowed:
                # Under-aggregated template or provider misbehavior: refuse
                # rather than leak an unvetted label to the browser.
                raise ProviderContractError("unexpected_series_label")
            labels[str(name)] = str(value)

        points: list[list[Any]] = []
        for pair in entry["values"]:
            if not isinstance(pair, list) or len(pair) != 2:
                raise ProviderContractError("provider_malformed_response")
            timestamp, raw_value = pair
            try:
                ts = int(float(timestamp))
                value = float(raw_value)
            except (TypeError, ValueError) as error:
                raise ProviderContractError("provider_malformed_response") from error
            if math.isnan(value) or math.isinf(value):
                points.append([ts, None])
                partial = True
                warnings.add("non_finite_values")
            else:
                points.append([ts, value])
        total_points += len(points)
        if total_points > template.max_points:
            raise ProviderContractError("point_budget_exceeded")
        points.sort(key=lambda p: p[0])
        series_out.append({"labels": labels, "points": points})

    series_out.sort(key=lambda s: sorted(s["labels"].items()))
    return series_out, partial, sorted(warnings)
