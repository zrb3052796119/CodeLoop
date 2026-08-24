from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

import pytest


ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = (
    ROOT / "README.md",
    ROOT / "README.zh-CN.md",
    ROOT / "CONTRIBUTIONS.md",
    ROOT / "docs" / "PORTFOLIO_CASE_STUDY.md",
    ROOT / "docs" / "PORTFOLIO_CASE_STUDY.en.md",
)
MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


@pytest.mark.parametrize("document", DOCUMENTS, ids=lambda path: path.name)
def test_portfolio_relative_links_exist_in_clean_checkout(document: Path) -> None:
    missing: list[str] = []
    for raw_target in MARKDOWN_LINK_RE.findall(document.read_text(encoding="utf-8")):
        target = raw_target.strip().strip("<>")
        if not target or target.startswith(("#", "http://", "https://", "mailto:")):
            continue
        relative = unquote(target.split("#", 1)[0])
        if relative and not (document.parent / relative).resolve().exists():
            missing.append(target)

    assert missing == [], f"{document.relative_to(ROOT)} has missing links: {missing}"
