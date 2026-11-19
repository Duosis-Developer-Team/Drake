"""What a dashboard layout is, and which layouts are allowed. Pure.

A layout is an ordered list of widgets from a fixed catalog, each with a
width and whether it is hidden. A widget carries no query: what it draws is
decided by its key (ADR-0033), so a layout can never become a way to read
something the fixed reads do not.
"""

from typing import Any

# The catalog, in the order of the default dashboard (the approved v18).
WIDGETS: tuple[str, ...] = (
    "overview",
    "services",
    "traffic_graph",
    "findings",
    "latency_heatmap",
    "endpoints",
    "capacity",
    "databases",
    "logs",
)
SIZES: tuple[str, ...] = ("s", "m", "l")
DEFAULT_SIZES: dict[str, str] = {
    "overview": "l",
    "services": "l",
    "traffic_graph": "l",
    "findings": "l",
    "latency_heatmap": "m",
    "endpoints": "m",
    "capacity": "s",
    "databases": "s",
    "logs": "s",
}
MAX_TITLE = 120
MAX_NOTE = 200


class LayoutError(ValueError):
    pass


def default_layout() -> list[dict[str, Any]]:
    return [{"key": key, "size": DEFAULT_SIZES[key], "hidden": False} for key in WIDGETS]


def default_title(project_name: str, environment_key: str) -> str:
    return f"{project_name} · {environment_key}"[:MAX_TITLE]


def validate_layout(raw: Any) -> list[dict[str, Any]]:
    """The layout as stored: every catalog widget exactly once, in the
    caller's order. A widget the caller left out is appended hidden, so a
    layout written before a widget existed still names it."""
    if not isinstance(raw, list) or len(raw) > len(WIDGETS):
        raise LayoutError("layout must be a list of catalog widgets")
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict) or set(item) - {"key", "size", "hidden"}:
            raise LayoutError("a widget has a key, a size and whether it is hidden")
        key, size, hidden = item.get("key"), item.get("size"), item.get("hidden", False)
        if key not in WIDGETS:
            raise LayoutError(f"unknown widget: {key!r}")
        if key in seen:
            raise LayoutError(f"widget twice: {key}")
        if size not in SIZES:
            raise LayoutError(f"unknown size: {size!r}")
        if not isinstance(hidden, bool):
            raise LayoutError("hidden is true or false")
        seen.add(key)
        out.append({"key": key, "size": size, "hidden": hidden})
    for key in WIDGETS:
        if key not in seen:
            out.append({"key": key, "size": DEFAULT_SIZES[key], "hidden": True})
    if all(w["hidden"] for w in out):
        raise LayoutError("a dashboard shows at least one widget")
    return out


def validate_title(raw: Any) -> str:
    if not isinstance(raw, str) or not raw.strip() or len(raw.strip()) > MAX_TITLE:
        raise LayoutError(f"title is 1 to {MAX_TITLE} characters")
    return raw.strip()


def validate_note(raw: Any) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str) or len(raw) > MAX_NOTE:
        raise LayoutError(f"note is at most {MAX_NOTE} characters")
    return raw.strip() or None
