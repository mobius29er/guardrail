"""Code blocks must stay inside the viewport on a phone.

The terminal blocks already set `overflow-x: auto`, so a long command is
supposed to scroll inside its own box. It did not. `.pair` declared
`grid-template-columns: 1fr 1fr`, and a bare `1fr` means `minmax(auto, 1fr)` —
the track refuses to shrink below its widest content. The `<pre>` never got the
chance to scroll, so the grid grew instead and took the whole page sideways
with it. The mobile breakpoint collapsed to a single `1fr`, which has the same
floor, so narrowing the screen did not help.

`overflow-x: auto` on the scroller is therefore only half the fix. Every
ancestor between it and the page has to be allowed to shrink, which for a grid
or flex child means an explicit `minmax(0, …)` or `min-width: 0` — their
default `min-width: auto` is the whole problem.

These tests pin both halves.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PUBLIC = Path(__file__).resolve().parents[1] / "site" / "public"
SHARED = PUBLIC / "style.css"
PAGES = ["index.html", "start.html"]

# Containers that wrap a <pre>. Each must be able to shrink.
TERMINALS = [".termlight", ".term"]


def _rules(css: str, selector: str) -> list[str]:
    """Every declaration block for a selector, across all sheets.

    Plural on purpose. `.term` is defined twice — once for the hero terminal in
    index.html and once for the step blocks in start.html — and the first
    version of this helper returned only the first match, which is exactly how
    the hero block kept overflowing after the fix had supposedly landed.
    """
    return re.findall(rf"(?:^|[}}\n,]){re.escape(selector)}\s*\{{([^}}]*)\}}", css)


def _all_css() -> str:
    """Shared sheet plus every page's inline block, concatenated."""
    parts = [SHARED.read_text(encoding="utf-8")]
    for page in PAGES:
        parts += re.findall(
            r"<style>(.*?)</style>", (PUBLIC / page).read_text(encoding="utf-8"), re.S
        )
    return "\n".join(parts)


@pytest.mark.parametrize("selector", TERMINALS)
def test_terminal_can_shrink(selector):
    blocks = _rules(_all_css(), selector)
    assert blocks, f"{selector} is not defined in any stylesheet"
    bad = [b for b in blocks if "max-width:100%" not in b.replace(" ", "")]
    assert not bad, (
        f"{len(bad)} of {len(blocks)} {selector} rules are missing "
        f"max-width:100%. Without it the box is free to grow past the viewport "
        f"and the page scrolls sideways instead of the code block scrolling "
        f"inside itself. Every definition needs it, not just the first."
    )


@pytest.mark.parametrize("selector", TERMINALS)
def test_terminal_scrolls_internally(selector):
    blocks = _rules(_all_css(), f"{selector} pre")
    assert blocks, f"{selector} pre is not defined"
    bad = [b for b in blocks if "overflow-x:auto" not in b.replace(" ", "")]
    assert not bad, (
        f"{selector} pre must set overflow-x:auto so a long command scrolls "
        f"within the block. Do not wrap the text instead — a shell command "
        f"broken across lines is worse than one the reader has to scroll."
    )


def test_side_by_side_pair_shrinks_on_small_screens():
    """`1fr` is `minmax(auto,1fr)`; the track will not go below its content."""
    css = _all_css()
    tracks = [
        m.group(1)
        for decl in re.findall(r"\.pair\s*\{([^}]*)\}", css)
        for m in [re.search(r"grid-template-columns\s*:([^;}]*)", decl)]
        if m
    ]
    assert tracks, ".pair defines no grid-template-columns"
    bad = [t for t in tracks if "fr" in t and "minmax(0" not in t.replace(" ", "")]
    assert not bad, (
        f"{bad} cannot shrink below the widest cell, so one long line pushes "
        f"the whole page off screen — including at the mobile breakpoint, "
        f"which collapses to a single track with the same floor. Use "
        f"minmax(0,1fr)."
    )
