"""Safe PromQL compilation.

The only dynamic content that ever enters a query is the scope matcher
values — derived from authoritative catalog rows and escaped by ONE central
function. Users cannot supply metric names, label names, operators,
regexes, or fragments; unknown parameters are rejected upstream by the
request model and again here. Same input → same query, deterministically.
"""

from dataclasses import dataclass

from drake_api.telemetry.registry import QueryTemplate

_MAX_MATCHERS = 8


class CompileError(ValueError):
    """The query cannot be compiled safely."""


def escape_label_value(value: str) -> str:
    """Central exact-match encoder for PromQL label values."""
    if "\n" in value or "\r" in value:
        raise CompileError("label values cannot contain newlines")
    return value.replace("\\", "\\\\").replace('"', '\\"')


@dataclass(frozen=True)
class CompiledQuery:
    query: str
    window_seconds: int
    matcher_set: tuple[str, ...]  # sorted label="value" pairs (cache key input)


def compile_query(
    template: QueryTemplate,
    source_values: dict[str, str],
    parameters: dict[str, str],
    effective_step_seconds: int,
) -> CompiledQuery:
    # v1 templates take no request parameters; anything supplied is refused.
    for name in parameters:
        if name not in template.parameters:
            raise CompileError(f"unknown parameter {name!r}")

    missing = [source for _label, source in template.matchers if source not in source_values]
    if missing:
        raise CompileError("scope does not provide the template's matcher sources")
    if len(template.matchers) > _MAX_MATCHERS:
        raise CompileError("too many matchers")

    pairs = [
        f'{label}="{escape_label_value(source_values[source])}"'
        for label, source in template.matchers
    ]
    matchers = ",".join(pairs)

    # rate()/increase() need at least a few scrape intervals; tie the window
    # to the effective step so zoomed-out queries stay smooth, bounded below.
    window_seconds = max(120, 4 * effective_step_seconds)
    window_seconds = min(window_seconds, 3600)

    query = template.expression.replace("{{matchers}}", matchers).replace(
        "{{window}}", f"{window_seconds}s"
    )
    if "{{" in query:
        raise CompileError("unresolved placeholder in expression")
    return CompiledQuery(
        query=query,
        window_seconds=window_seconds,
        matcher_set=tuple(sorted(pairs)),
    )
