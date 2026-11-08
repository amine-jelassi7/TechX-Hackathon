"""Colour tokens for the dark and light themes (single source for CSS, Plotly and the three.js scenes).

Dark: the original night-shift panel. Light: a separate design, not an inverted dark. Cool paper
background, white panels, the same accent hue (205 degrees) darkened until it clears WCAG AA, and the same
alarm hue. Alarm is kept as a fill colour and has a darker `alarm_ink` for text on light surfaces.
Contrast (WCAG 2.x), light: text 16.4:1, dim 6.6:1, signal 5.1:1, alarm_ink 5.5:1, all on the page bg.
"""

from __future__ import annotations

from typing import Any

MODES = ("dark", "light")

PALETTES: dict[str, dict[str, Any]] = {
    "dark": {
        "void": "#06080B", "panel": "#0B0F14", "panel_hi": "#111820",
        "line": "#1B242E", "line_hi": "#2A3744",
        "text": "#DCE3EA", "dim": "#7F8E9D", "faint": "#4B5866",
        "signal": "#46B4FF", "signal_ink": "#46B4FF", "alarm": "#FF5A36", "alarm_ink": "#FF5A36",
        "on_signal": "#06080B", "fill_alpha": 0.5, "scheme": "dark",
    },
    "light": {
        "void": "#F3F5F8", "panel": "#FFFFFF", "panel_hi": "#E9EEF3",
        "line": "#D8DFE6", "line_hi": "#B7C3CF",
        "text": "#0E1822", "dim": "#4A5968", "faint": "#6F7E8C",
        "signal": "#0B6BB8", "signal_ink": "#0B6BB8", "alarm": "#FF5A36", "alarm_ink": "#B83216",
        "on_signal": "#FFFFFF", "fill_alpha": 0.22, "scheme": "light",
    },
}

# three.js scene parameters per mode (read by agent_network.js and integrity_ring.js)
SCENES: dict[str, dict[str, Any]] = {
    "dark": {
        "bg": "#06080B", "accent": "#46B4FF", "alarm": "#FF5A36", "link": "#2F6F9C", "link_opacity": 0.5,
        "link_hot": "#6FC6FF", "ring": "#2A3744", "ring_opacity": 0.55, "star": "#4B5866", "star_opacity": 0.8,
        "core_boost": 1.35, "shell_opacity": 0.4, "fog": 0.026, "additive": True,
        "bloom": {"strength": 0.38, "radius": 0.4, "threshold": 0.45},
    },
    "light": {
        "bg": "#FFFFFF", "accent": "#0B6BB8", "alarm": "#F0481F", "link": "#7FA6C7", "link_opacity": 0.8,
        "link_hot": "#0B6BB8", "ring": "#C5D0DA", "ring_opacity": 0.9, "star": "#9AA8B5", "star_opacity": 0.7,
        "core_boost": 1.0, "shell_opacity": 0.6, "fog": 0.018, "additive": False,
        "bloom": None,
    },
}


def normalize(mode: str | None) -> str:
    return mode if mode in MODES else "dark"


def pal(mode: str | None) -> dict[str, Any]:
    return PALETTES[normalize(mode)]


def _rgb(hex_color: str) -> str:
    h = hex_color.lstrip("#")
    return ",".join(str(int(h[i : i + 2], 16)) for i in (0, 2, 4))


def tokens_css(mode: str | None) -> str:
    """The :root block for a mode; every stylesheet reads these variables."""
    p = pal(mode)
    names = ("void", "panel", "panel_hi", "line", "line_hi", "text", "dim", "faint", "signal", "signal_ink",
             "alarm", "alarm_ink", "on_signal")
    body = ";".join(f"--{n.replace('_', '-')}:{p[n]}" for n in names)
    body += f";--signal-rgb:{_rgb(p['signal'])};--alarm-rgb:{_rgb(p['alarm'])};color-scheme:{p['scheme']}"
    return f":root{{{body}}}"


def scene_theme(mode: str | None) -> dict[str, Any]:
    return {"mode": normalize(mode), **SCENES[normalize(mode)]}
