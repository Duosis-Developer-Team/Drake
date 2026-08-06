"""Drake's own bounded Query Broker metrics (no PII, no unbounded labels).

Hand-rolled counters/histograms with a Prometheus text exposition — labels
are drawn exclusively from registry-controlled values (template keys) and
small fixed enums (outcome, cache state, provider type). Never labelled by
principal, project name, tenant, scope ref, PromQL, URL, correlation ID,
or raw error text. The endpoint must never be exposed on public ingress.
"""

import threading
from collections import defaultdict

_DURATION_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
_POINT_BUCKETS = (10, 100, 500, 1000, 5000, 20000)


class BrokerMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queries: dict[tuple[str, str, str, str], int] = defaultdict(int)
        self._rejections: dict[str, int] = defaultdict(int)
        self._duration_buckets: dict[float, int] = defaultdict(int)
        self._duration_sum = 0.0
        self._duration_count = 0
        self._point_buckets: dict[int, int] = defaultdict(int)
        self._point_count = 0

    def record_query(
        self,
        *,
        template_key: str,
        provider_type: str,
        outcome: str,
        cache_state: str,
        duration_seconds: float,
        returned_points: int,
    ) -> None:
        with self._lock:
            self._queries[(template_key, provider_type, outcome, cache_state)] += 1
            for bound in _DURATION_BUCKETS:
                if duration_seconds <= bound:
                    self._duration_buckets[bound] += 1
            self._duration_sum += duration_seconds
            self._duration_count += 1
            for bound in _POINT_BUCKETS:
                if returned_points <= bound:
                    self._point_buckets[bound] += 1
            self._point_count += 1

    def record_rejection(self, reason: str) -> None:
        """reason ∈ {concurrency, budget_unavailable, range_budget, timeout,
        provider_contract, provider_unavailable} — fixed enum, never free text."""
        with self._lock:
            self._rejections[reason] += 1

    def render(self) -> str:
        with self._lock:
            lines = [
                "# TYPE drake_telemetry_queries_total counter",
            ]
            for (template, provider, outcome, cache_state), count in sorted(self._queries.items()):
                lines.append(
                    "drake_telemetry_queries_total{"
                    f'template_key="{template}",provider_type="{provider}",'
                    f'outcome="{outcome}",cache_state="{cache_state}"'
                    f"}} {count}"
                )
            lines.append("# TYPE drake_telemetry_rejections_total counter")
            for reason, count in sorted(self._rejections.items()):
                lines.append(f'drake_telemetry_rejections_total{{reason="{reason}"}} {count}')
            lines.append("# TYPE drake_telemetry_query_duration_seconds histogram")
            for bound in _DURATION_BUCKETS:
                lines.append(
                    f'drake_telemetry_query_duration_seconds_bucket{{le="{bound}"}} '
                    f"{self._duration_buckets[bound]}"
                )
            lines.append(
                f'drake_telemetry_query_duration_seconds_bucket{{le="+Inf"}} {self._duration_count}'
            )
            lines.append(f"drake_telemetry_query_duration_seconds_sum {self._duration_sum:.6f}")
            lines.append(f"drake_telemetry_query_duration_seconds_count {self._duration_count}")
            lines.append("# TYPE drake_telemetry_returned_points histogram")
            for bound in _POINT_BUCKETS:
                lines.append(
                    f'drake_telemetry_returned_points_bucket{{le="{bound}"}} '
                    f"{self._point_buckets[bound]}"
                )
            lines.append(f'drake_telemetry_returned_points_bucket{{le="+Inf"}} {self._point_count}')
            return "\n".join(lines) + "\n"
