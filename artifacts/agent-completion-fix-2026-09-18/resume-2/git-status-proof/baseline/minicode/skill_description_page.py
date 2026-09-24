"""Pure, bounded description-page rendering shared by discovery and prefetch."""

from __future__ import annotations

from dataclasses import dataclass

_DESCRIPTION_TRUNCATED = " [description truncated; call load_skill for full content]"


@dataclass(frozen=True, slots=True)
class DescriptionPage:
    ok: bool
    output: str
    rendered: int = 0
    truncated: bool = False


def shorten_text(text: str, limit: int, *, suffix: str = "…") -> str:
    return text if len(text) <= limit else text[: limit - len(suffix)] + suffix


@dataclass(frozen=True, slots=True)
class DescriptionRow:
    name: str
    prefix: str
    description: str
    section: str = ""


def render_description_page(
    rows: list[DescriptionRow],
    *,
    offset: int,
    limit: int,
    budget: int,
    tool_name: str,
    query: str | None = None,
    leading_lines: tuple[str, ...] = (),
) -> DescriptionPage:
    """Consume a stable sequence prefix and budget its complete rendered text.

    A too-large first description is shortened, with an explicit loader hint,
    so even the smallest page advances. Names are never shortened. Section
    grouping is presentation only; offsets count the underlying sequence.
    """

    def render(
        selected: list[tuple[DescriptionRow, str]], *, compact: bool = False
    ) -> str:
        next_offset = offset + len(selected)
        header = list(leading_lines)
        if query is not None:
            header.append(f"QUERY: {shorten_text(query, 80)}")
        header.extend(
            [
                f"OFFSET: {shorten_text(str(offset), 32)}",
                "TRUNCATED: "
                + (
                    f"yes - call {tool_name} again with offset {next_offset}"
                    if next_offset < len(rows)
                    else "no"
                ),
                "",
            ]
        )
        sections: dict[str, list[str]] = {}
        for row, description in selected:
            prefix = f"- {row.name}: " if compact else row.prefix
            sections.setdefault("" if compact else row.section, []).append(
                prefix + description
            )
        for section, lines in sections.items():
            if section:
                header.append(section)
            header.extend(lines)
        if not selected:
            header.append("(no skills on this page)")
        return "\n".join(header)

    selected: list[tuple[DescriptionRow, str]] = []
    for row in rows[offset : offset + limit]:
        candidate = [*selected, (row, row.description)]
        if len(render(candidate)) <= budget:
            selected = candidate
            continue
        if selected:
            break
        # Prefer the normal layout; drop redundant directory text only when
        # needed to fit a maximal valid qualified name in the minimum budget.
        for compact in (False, True):
            available = budget - len(render([(row, "")], compact=compact))
            if available >= len(_DESCRIPTION_TRUNCATED):
                description = shorten_text(
                    row.description, available, suffix=_DESCRIPTION_TRUNCATED
                )
                return DescriptionPage(
                    ok=True,
                    output=render([(row, description)], compact=compact),
                    rendered=1,
                    truncated=offset + 1 < len(rows),
                )
        # Legacy caller catalogs can contain invalid, unbounded identities.
        # Do not print a shortened name that load_skill could resolve wrongly.
        return DescriptionPage(
            ok=False,
            output="Skill discovery entry name cannot fit the page budget.",
        )
    return DescriptionPage(
        ok=True,
        output=render(selected),
        rendered=len(selected),
        truncated=offset + len(selected) < len(rows),
    )
