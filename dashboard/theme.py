"""Visual system for the owner console: modes, CSS, HTML helpers, Plotly templates."""

from __future__ import annotations

import re
from html import escape
from pathlib import Path
from typing import Any

import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

from dashboard.palette import MODES, PALETTES, normalize, pal, tokens_css

STATIC = Path(__file__).resolve().parent / "static"

# dark-mode values kept as module constants for callers that only need the default
VOID = PALETTES["dark"]["void"]
PANEL = PALETTES["dark"]["panel"]
LINE = PALETTES["dark"]["line"]
LINE_HI = PALETTES["dark"]["line_hi"]
TEXT = PALETTES["dark"]["text"]
DIM = PALETTES["dark"]["dim"]
FAINT = PALETTES["dark"]["faint"]
SIGNAL = PALETTES["dark"]["signal"]
ALARM = PALETTES["dark"]["alarm"]
MONO = "JetBrains Mono, Consolas, monospace"
SANS = "Instrument Sans, Segoe UI, sans-serif"

FLAGGED = {"block", "escalate"}

# ---------------------------------------------------------------- mode (dark / light)


def init_mode() -> None:
    """Seed the toggle from the URL (?theme=light|dark) the first time a session runs."""
    if "light_mode" not in st.session_state:
        st.session_state["light_mode"] = st.query_params.get("theme") == "light"


def mode() -> str:
    return "light" if st.session_state.get("light_mode") else "dark"


def mode_toggle() -> str:
    """Sidebar switch; persists to session state (widget key) and to the URL so a refresh keeps it."""
    init_mode()
    st.sidebar.toggle("Light theme", key="light_mode")
    current = mode()
    if st.query_params.get("theme") != current:
        st.query_params["theme"] = current
    return current


# ---------------------------------------------------------------- plotly (one template per mode)


def _template(m: str) -> go.layout.Template:
    p = PALETTES[m]
    axis = {
        "gridcolor": p["line"], "gridwidth": 1, "zeroline": False, "showline": True,
        "linecolor": p["line_hi"], "ticks": "outside", "tickcolor": p["line_hi"], "ticklen": 4,
        "automargin": True,
        "tickfont": {"family": MONO, "size": 11, "color": p["dim"]},
        "title": {"font": {"family": SANS, "size": 12, "color": p["dim"]}},
    }
    return go.layout.Template(
        layout={
            "paper_bgcolor": "rgba(0,0,0,0)", "plot_bgcolor": "rgba(0,0,0,0)",
            "font": {"family": SANS, "color": p["text"], "size": 12},
            "colorway": [p["signal"], p["dim"], p["faint"], p["line_hi"]],
            "xaxis": axis, "yaxis": axis, "margin": {"l": 8, "r": 8, "t": 8, "b": 8},
            "hoverlabel": {"bgcolor": p["panel"], "bordercolor": p["line_hi"],
                           "font": {"family": MONO, "size": 12, "color": p["text"]}},
            "legend": {"font": {"family": SANS, "size": 12, "color": p["dim"]}, "bgcolor": "rgba(0,0,0,0)"},
            "bargap": 0.45,
            "transition": {"duration": 450, "easing": "cubic-in-out"},
        }
    )


for _m in MODES:
    pio.templates[f"souk_{_m}"] = _template(_m)
pio.templates.default = "souk_dark"


def rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha:.2f})"


def style_fig(fig: go.Figure, height: int = 260, **layout: Any) -> go.Figure:
    fig.update_layout(template=f"souk_{mode()}", height=height, **layout)
    return fig


def plot(fig: go.Figure, key: str | None = None) -> None:
    st.plotly_chart(fig, width="stretch", theme=None, key=key, config={"displayModeBar": False})


def bar_chart(
    labels: list, values: list, flagged: set | None = None, horizontal: bool = False, height: int = 260
) -> go.Figure:
    p, flagged = pal(mode()), flagged or set()
    colors = [p["alarm"] if lb in flagged else p["signal"] for lb in labels]
    fills = [rgba(c, p["fill_alpha"]) for c in colors]
    if horizontal:
        fig = go.Figure(go.Bar(x=values, y=labels, orientation="h", marker_color=fills))
        fig.update_yaxes(autorange="reversed", showgrid=False)
    else:
        fig = go.Figure(go.Bar(x=labels, y=values, marker_color=fills))
        fig.update_xaxes(showgrid=False)
    fig.update_traces(
        marker_line_color=colors, marker_line_width=1, hovertemplate="%{label}: %{value}<extra></extra>"
    )
    return style_fig(fig, height)


def funnel_chart(labels: list[str], values: list[float], height: int = 260) -> go.Figure:
    """Horizontal step bars with the stage-to-stage ratio annotated."""
    p = pal(mode())
    fig = go.Figure()
    top = max(values) if values and max(values) else 1
    for i, (lb, v) in enumerate(zip(labels, values, strict=True)):
        fig.add_trace(
            go.Bar(
                x=[v], y=[lb], orientation="h",
                marker_color=rgba(p["signal"], max(0.1, p["fill_alpha"] * 0.85 - i * 0.07)),
                marker_line_color=p["signal"], marker_line_width=1, showlegend=False,
                hovertemplate=f"{lb}: %{{x}}<extra></extra>",
            )
        )
        ratio = "" if i == 0 or not values[i - 1] else f"  {v / values[i - 1] * 100:.0f}%"
        fig.add_annotation(
            x=v, y=lb, text=f"{v:g}{ratio}", showarrow=False, xanchor="left", xshift=8,
            font={"family": MONO, "size": 12, "color": p["text"]},
        )
    fig.update_yaxes(autorange="reversed", showgrid=False)
    fig.update_xaxes(range=[0, top * 1.25], showgrid=True)
    return style_fig(fig, height, bargap=0.35)


# ---------------------------------------------------------------- css, motion state, html

def inject_css(m: str | None = None) -> None:
    """Colour tokens for the active mode + stylesheets + the motion engine (script is a constant, so it
    mounts once per browser page)."""
    names = ("base.css", "components.css", "motion.css")
    css = tokens_css(normalize(m or mode())) + "\n" + "\n".join(
        (STATIC / n).read_text(encoding="utf-8") for n in names
    )
    st.html(f"<style>{css}</style>")
    st.html(f"<script>{(STATIC / 'motion.js').read_text(encoding='utf-8')}</script>", unsafe_allow_javascript=True)


def begin_page(choice: str) -> None:
    """Entrance animations run only when the page changed since the last full run, never on reruns."""
    st.session_state["_enter"] = choice != st.session_state.get("_last_page")
    st.session_state["_last_page"] = choice
    st.session_state["_kpi_i"] = 0


def end_page() -> None:
    # fragments (live feed) rerun on their own; they must not see the entrance flag again
    st.session_state["_enter"] = False


def entering() -> bool:
    return bool(st.session_state.get("_enter"))


def _in(delay_ms: int = 0) -> tuple[str, str]:
    return (" sc-in", f' style="--d:{delay_ms}ms"') if entering() else ("", "")


def esc(v: Any) -> str:
    return escape("" if v is None else str(v))


def short_ts(ts: Any) -> str:
    s = str(ts or "")
    return s[11:19] if len(s) >= 19 else s


def pill(text: str, kind: str = "") -> str:
    return f'<span class="sc-pill {kind}">{esc(text)}</span>'


def page_head(title: str, sub: str = "", pills: list[str] | None = None) -> None:
    cls, style = _in(0)
    right = f'<div class="sc-right">{"".join(pills)}</div>' if pills else ""
    subtitle = f'<div class="sc-sub">{esc(sub)}</div>' if sub else ""
    st.html(f'<div class="sc-head{cls}"{style}><div><h1>{esc(title)}</h1>{subtitle}</div>{right}</div>')


def section(title: str) -> None:
    cls, style = _in(70)
    st.html(f'<div class="sc-sec{cls}"{style}>{esc(title)}</div>')


def spark(values: list[float]) -> str:
    if not values:
        return ""
    top = max(values) or 1
    bars = "".join(
        '<i class="z" style="height:2px"></i>' if not v else f'<i style="height:{max(8, v / top * 100):.0f}%"></i>'
        for v in values
    )
    return f'<div class="spark">{bars}</div>'


_NUM = re.compile(r"^-?\d[\d,]*(\.\d+)?$")


def _value_html(value: Any, delay: int) -> str:
    """Numeric values get count-up markup, but only while a page is being entered."""
    text = esc(value)
    s = str(value)
    if not entering() or not _NUM.match(s):
        return text
    dec = len(s.split(".")[1]) if "." in s else 0
    return (f'<span class="sc-count" data-to="{s.replace(",", "")}" data-dec="{dec}" '
            f'data-group="{1 if "," in s else 0}" data-delay="{delay + 120}">{text}</span>')


def kpi(
    label: str, value: Any, sub: str = "", *, unit: str = "", alert: bool = False,
    series: list[float] | None = None, meter: float | None = None, pips: int | None = None,
) -> None:
    i = st.session_state.get("_kpi_i", 0)
    st.session_state["_kpi_i"] = i + 1
    delay = 90 + i * 50
    cls, style = _in(delay)
    extra = ""
    if meter is not None:
        extra = f'<div class="sc-meter"><b style="width:{max(0.0, min(1.0, meter)) * 100:.1f}%"></b></div>'
    if pips is not None:
        extra = '<div class="sc-pips">' + "".join(
            f'<i class="{"on" if n < pips else ""}"></i>' for n in range(5)
        ) + "</div>"
    unit_html = f"<small>{esc(unit)}</small>" if unit else ""
    st.html(
        f'<div class="sc-kpi{" alert" if alert else ""}{cls}"{style}><div class="lb">{esc(label)}</div>'
        f'<div class="vl">{_value_html(value, delay)}{unit_html}</div>{extra}'
        f'<div class="sb">{esc(sub)}</div>{spark(series or [])}</div>'
    )


def decision_cell(decision: Any) -> str:
    d = str(decision or "")
    return f'<span class="dec-{esc(d)}">{esc(d) or "-"}</span>'


def html_table(columns: list[tuple[str, str]], rows: list[dict], *, mono: set[str] | None = None,
               flag_if: Any = None, raw: set[str] | None = None, feed: str | None = None,
               id_key: str | None = None) -> None:
    """columns: (key, header). Cells are escaped unless the key is in `raw` (pre-escaped HTML).
    feed + id_key mark a live table: the motion engine animates rows whose id it has not seen."""
    mono, raw = mono or set(), raw or set()
    head = "".join(f"<th>{esc(h)}</th>" for _k, h in columns)
    body = []
    for r in rows:
        cls = ' class="flag"' if flag_if and flag_if(r) else ""
        rid = f' data-id="{esc(r.get(id_key))}"' if id_key else ""
        cells = "".join(
            f'<td class="{"m" if k in mono else ""}{" r" if k == "reasons" else ""}">'
            f"{r.get(k, '') if k in raw else esc(r.get(k))}</td>"
            for k, _h in columns
        )
        body.append(f"<tr{cls}{rid}>{cells}</tr>")
    live = f' data-feed="{esc(feed)}"' if feed else ""
    wcls, wstyle = _in(60)
    st.html(
        f'<div class="sc-tablewrap{wcls}"{wstyle}{live}><table class="sc-table"><thead><tr>{head}</tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table></div>'
    )


def embed_html(html: str, height: int) -> None:
    """Render an HTML document in an iframe (st.iframe on current Streamlit, components before)."""
    if hasattr(st, "iframe"):
        st.iframe(html, height=height)
    else:  # pragma: no cover - older Streamlit
        import streamlit.components.v1 as components

        components.html(html, height=height)
