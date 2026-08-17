"""WCAG 2.1 AA contrast for the halligan.dev palette.

Colour choices drift — someone nudges a red to look better and quietly drops a
button below the threshold. Three pairs were already failing when this was
first measured, including white-on-red for the primary call to action at
3.91:1, and a card label that failed in BOTH themes.

Thresholds: 4.5:1 normal text, 3:1 large text and UI boundaries.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CSS = Path(__file__).resolve().parents[1] / "site" / "public" / "style.css"


def _linear(channel: float) -> float:
    c = channel / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _linear(r) + 0.7152 * _linear(g) + 0.0722 * _linear(b)


def contrast(fg: str, bg: str) -> float:
    a, b = luminance(fg), luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


LIGHT, LIGHT_CARD = "#ffffff", "#fafafa"
DARK, DARK_CARD = "#0d1117", "#161b22"
HERO, TERMINAL = "#08090a", "#0b0d10"

# (label, foreground, background, minimum)
PAIRS = [
    ("light body", "#1a1a1a", LIGHT, 4.5),
    ("light muted", "#575d66", LIGHT, 4.5),
    ("light muted on card", "#575d66", LIGHT_CARD, 4.5),
    ("light pass", "#1a7f37", LIGHT, 4.5),
    ("light fail", "#cf222e", LIGHT, 4.5),
    ("light warn", "#9a6700", LIGHT, 4.5),
    ("light link", "#0b5fff", LIGHT, 4.5),
    ("light flake", "#8250df", LIGHT, 4.5),
    ("light card tag", "#cf2f38", LIGHT_CARD, 4.5),
    ("dark body", "#e6edf3", DARK, 4.5),
    ("dark muted", "#a4acb8", DARK, 4.5),
    ("dark muted on card", "#a4acb8", DARK_CARD, 4.5),
    ("dark pass", "#3fb950", DARK, 4.5),
    ("dark fail", "#f85149", DARK, 4.5),
    ("dark warn", "#d29922", DARK, 4.5),
    ("dark link", "#58a6ff", DARK, 4.5),
    ("dark flake", "#a371f7", DARK, 4.5),
    ("dark card tag", "#f0645f", DARK_CARD, 4.5),
    ("hero body", "#f4f5f7", HERO, 4.5),
    ("hero muted", "#9297a1", HERO, 4.5),
    ("hero badge border", "#e5484d", HERO, 3.0),
    ("terminal body", "#d4dae1", TERMINAL, 4.5),
    ("terminal comment", "#7d8590", TERMINAL, 4.5),
    ("terminal green", "#3fb950", TERMINAL, 4.5),
    ("terminal red", "#f85149", TERMINAL, 4.5),
    ("terminal yellow", "#d29922", TERMINAL, 4.5),
    ("terminal blue", "#58a6ff", TERMINAL, 4.5),
    ("terminal magenta", "#a371f7", TERMINAL, 4.5),
    ("primary button label", "#ffffff", "#cf2f38", 4.5),
]


@pytest.mark.parametrize("label,fg,bg,minimum", PAIRS, ids=[p[0] for p in PAIRS])
def test_meets_wcag_aa(label, fg, bg, minimum):
    got = contrast(fg, bg)
    assert got >= minimum, (
        f"{label}: {fg} on {bg} is {got:.2f}:1, below the WCAG AA minimum of "
        f"{minimum}:1. Darken or lighten the foreground rather than lowering "
        f"the bar."
    )


class TestPaletteStaysInSync:
    """The values above must be the ones the stylesheet actually ships."""

    @pytest.fixture(scope="class")
    @classmethod
    def css(cls) -> str:
        return CSS.read_text(encoding="utf-8")

    @pytest.mark.parametrize(
        "token,value",
        [
            ("--brand", "#e5484d"),
            ("--brand-solid", "#cf2f38"),
            ("--brand-text", "#cf2f38"),
        ],
    )
    def test_token_value(self, css, token, value):
        found = re.search(rf"{re.escape(token)}\s*:\s*(#[0-9a-fA-F]{{6}})", css)
        assert found, f"{token} is not defined in style.css"
        assert found.group(1).lower() == value, (
            f"{token} is {found.group(1)} but the contrast test asserts {value}. "
            f"Update both together, or the audit is measuring a colour nobody ships."
        )

    def test_primary_button_uses_the_solid_token(self, css):
        """White on --brand is 3.91:1. The button must use --brand-solid."""
        rule = re.search(r"\.btn\.primary\{([^}]*)\}", css)
        assert rule, ".btn.primary rule not found"
        assert "--brand-solid" in rule.group(1), (
            "the primary button must fill with --brand-solid; --brand fails "
            "white-text contrast at 3.91:1"
        )
