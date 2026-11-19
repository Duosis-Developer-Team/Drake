"""Dashboard layouts: only catalog widgets, each once, never all hidden."""

import pytest
from drake_api.dashboards.model import (
    WIDGETS,
    LayoutError,
    default_layout,
    default_title,
    validate_layout,
    validate_note,
    validate_title,
)


def test_the_default_shows_every_widget_in_catalog_order() -> None:
    layout = default_layout()
    assert [w["key"] for w in layout] == list(WIDGETS)
    assert not any(w["hidden"] for w in layout)
    assert validate_layout(layout) == layout


def test_order_and_sizes_are_kept_and_missing_widgets_come_back_hidden() -> None:
    out = validate_layout(
        [
            {"key": "findings", "size": "l", "hidden": False},
            {"key": "overview", "size": "m", "hidden": False},
        ]
    )
    assert [w["key"] for w in out[:2]] == ["findings", "overview"]
    assert out[1]["size"] == "m"
    assert {w["key"] for w in out} == set(WIDGETS)
    assert all(w["hidden"] for w in out[2:])


@pytest.mark.parametrize(
    "layout",
    [
        "overview",
        [{"key": "promql", "size": "l"}],
        [{"key": "overview", "size": "xl"}],
        [{"key": "overview", "size": "l"}, {"key": "overview", "size": "s"}],
        [{"key": "overview", "size": "l", "query": "up"}],
        [{"key": "overview", "size": "l", "hidden": "no"}],
        [{"key": k, "size": "s", "hidden": True} for k in WIDGETS],
        [{"key": "overview", "size": "l", "hidden": True}],
        [{"key": k, "size": "s"} for k in WIDGETS] + [{"key": "overview", "size": "s"}],
    ],
)
def test_refused_layouts(layout) -> None:
    with pytest.raises(LayoutError):
        validate_layout(layout)


def test_titles_and_notes() -> None:
    assert validate_title("  Hermes test  ") == "Hermes test"
    with pytest.raises(LayoutError):
        validate_title("   ")
    with pytest.raises(LayoutError):
        validate_title("x" * 121)
    assert validate_note("") is None and validate_note(None) is None
    with pytest.raises(LayoutError):
        validate_note("x" * 201)
    assert default_title("Hermes", "test") == "Hermes · test"
