"""Every telemetry read stays under the reader profile's row ceiling.

The read-only ClickHouse user carries `max_result_rows` with a hard `<max>`
constraint, and the adapter sends each read's own row bound as that
setting. A bound above the ceiling is not trimmed: ClickHouse refuses the
whole request (SETTING_CONSTRAINT_VIOLATION). The cross-service signal read
shipped with 2000 against a ceiling of 1000 and failed on every call in
production while the fake store in the integration tests answered happily.
This test reads the ceiling from the deployed configuration itself.
"""

import re
from pathlib import Path

import pytest
from drake_api.observability import logs, signals, traces

ROOT = Path(__file__).resolve().parents[3]
CONFIGS = [
    ROOT / "deploy/otel/templates/clickhouse-config.yaml",
    ROOT / "deploy/local/clickhouse/users.d/drake.xml",
]


def _ceiling(path: Path) -> int:
    text = path.read_text(encoding="utf-8")
    match = re.search(r"<max_result_rows>\s*<max>(\d+)</max>\s*</max_result_rows>", text)
    assert match, f"{path}: reader profile has no max_result_rows constraint"
    return int(match.group(1))


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.parent.name)
def test_every_read_bound_fits_the_reader_ceiling(path: Path) -> None:
    ceiling = _ceiling(path)
    bounds = {
        "signals.MAX_GROUPS": signals.MAX_GROUPS,
        "logs.MAX_LIMIT": logs.MAX_LIMIT,
        "traces.MAX_LIMIT": traces.MAX_LIMIT,
        "traces.MAX_ROUTES": traces.MAX_ROUTES,
        "traces.MAX_SPANS": traces.MAX_SPANS,
    }
    too_big = {name: value for name, value in bounds.items() if value > ceiling}
    assert not too_big, f"above the reader's max_result_rows ({ceiling}): {too_big}"
