"""HTML builders for the three.js scenes (agent network, audit integrity ring).

three.js is pinned to r160 and loaded from jsDelivr through an importmap. The scene code lives in
dashboard/static/*.js and is inlined at runtime so the iframe has no other dependency.
"""

from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any

from dashboard.palette import pal, scene_theme, tokens_css

STATIC = Path(__file__).resolve().parent / "static"
THREE_VERSION = "0.160.0"
CDN = f"https://cdn.jsdelivr.net/npm/three@{THREE_VERSION}"

# key, label, (x, y, z) in scene units. Layout: gateway left, agents right, audit chain at the far end.
NODES: list[dict[str, Any]] = [
    {"key": "orchestrator", "label": "Orchestrator", "pos": [0.0, 0.0, 0.0]},
    {"key": "gateway", "label": "Security Gateway", "pos": [-4.6, 0.7, 0.8]},
    {"key": "sales", "label": "Sales", "pos": [2.9, 2.7, 1.1]},
    {"key": "payment", "label": "Payment", "pos": [3.5, -0.2, -2.1]},
    {"key": "insights", "label": "Insights", "pos": [2.3, -2.9, 1.9]},
    {"key": "audit", "label": "Audit Log", "pos": [6.6, 0.9, 0.1]},
    {"key": "analytics", "label": "Analytics", "pos": [6.2, -2.7, -0.9]},
]
# (source, destination): particles flow source -> destination
LINKS: list[tuple[str, str]] = [
    ("gateway", "orchestrator"),
    ("orchestrator", "sales"),
    ("orchestrator", "payment"),
    ("orchestrator", "insights"),
    ("sales", "payment"),
    ("sales", "audit"),
    ("payment", "audit"),
    ("insights", "audit"),
    ("gateway", "audit"),
    ("audit", "analytics"),
]
_ALIASES = [
    ("orchestr", "orchestrator"),
    ("sales", "sales"),
    ("pay", "payment"),
    ("insight", "insights"),
    ("strateg", "insights"),
    ("gateway", "gateway"),
    ("security", "gateway"),
    ("filter", "gateway"),
    ("fraud", "gateway"),
    ("classif", "gateway"),
    ("analytic", "analytics"),
    ("kpi", "analytics"),
    ("audit", "audit"),
]
FLAGGED = {"block", "escalate"}
RECENT_WINDOW = 30  # newest events considered "recent" for node pulses


# Real actor names written by the backend: owner, orchestrator, sales, payment, insights. The gateway logs
# its decisions under the *requesting* actor (e.g. "sales"), so gateway_*/permission_denied events are
# attributed by event type; "owner" actions (approvals, Q&A, drafts) flow through the orchestrator.
_GATEWAY_EVENTS = ("gateway_", "permission_denied")


def canonical_actor(actor: Any, event_type: Any = None) -> str | None:
    et = str(event_type or "").lower()
    if et.startswith(_GATEWAY_EVENTS):
        return "gateway"
    a = str(actor or "").lower()
    if a == "owner":
        return "orchestrator"
    for needle, key in _ALIASES:
        if needle in a:
            return key
    return None


def scene_data(events: list[dict], kpis: dict | None = None) -> dict[str, Any]:
    """Reduce audit events (newest first) and KPIs to the JSON the scene reads."""
    activity: dict[str, int] = {}
    flagged: dict[str, int] = {}
    recent: dict[str, int] = {}
    for i, e in enumerate(events):
        key = canonical_actor(e.get("actor"), e.get("event_type"))
        if not key:
            continue
        activity[key] = activity.get(key, 0) + 1
        if e.get("decision") in FLAGGED:
            flagged[key] = flagged.get(key, 0) + 1
            if i < RECENT_WINDOW:
                recent[key] = recent.get(key, 0) + 1
    k = kpis or {}
    return {
        "nodes": NODES,
        "links": [list(link) for link in LINKS],
        "activity": activity,
        "flagged": flagged,
        "recent_flagged": recent,
        "totals": {
            "events": len(events),
            "flagged": sum(flagged.values()),
            "interactions": k.get("interactions_count", 0),
            "paid": k.get("paid_prospects", 0),
            "revenue": k.get("simulated_revenue", 0),
        },
    }


def _json_for_script(data: Any) -> str:
    return json.dumps(data).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def _read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8").replace("</script", "<\\/script")


def _importmap() -> str:
    imap = {"imports": {"three": f"{CDN}/build/three.module.js", "three/addons/": f"{CDN}/examples/jsm/"}}
    return f'<script type="importmap">{json.dumps(imap)}</script>'


_BASE_CSS = """
:root{--sans:"Instrument Sans","Segoe UI",system-ui,sans-serif;--mono:"JetBrains Mono",ui-monospace,Consolas,monospace}
*{box-sizing:border-box}html,body{margin:0;height:100%;background:var(--scene-bg);color:var(--text);
font-family:var(--sans);overflow:hidden}
#stage{position:absolute;inset:0}#stage canvas{display:block;width:100%;height:100%}
.labels{position:absolute;inset:0;pointer-events:none;overflow:hidden}
.lbl{font:500 11px/1.25 var(--sans);color:var(--text);background:color-mix(in srgb,var(--panel) 86%,transparent);
border:1px solid var(--line-hi);padding:3px 7px;border-radius:2px;white-space:nowrap;
transition:border-color .18s ease-out,background-color .18s ease-out,opacity .6s ease-out}
.lbl.hot{border-color:var(--signal);background:var(--panel)}.lbl.bad.hot{border-color:var(--alarm)}
body:not(.ready) .lbl{opacity:0}
.lbl b{display:block;font-weight:600}.lbl span{font:400 10px var(--mono);color:var(--dim)}
.lbl.bad{border-color:var(--alarm)}.lbl.bad span{color:var(--alarm-ink)}
#fallback{display:none;position:absolute;inset:0}.nogl #fallback{display:block}.nogl #stage{display:none}
#fallback svg{width:100%;height:100%}
#fallback-note{position:absolute;left:18px;bottom:14px;font:400 11px var(--mono);color:var(--dim)}
"""

_HUD_CSS = """
.hud{position:absolute;pointer-events:none;font:400 11px/1.5 var(--mono);color:var(--dim)}
.hud b{color:var(--text);font-weight:500}.hud .bad{color:var(--alarm-ink)}
.tl{left:18px;top:14px}.tl h2{margin:0 0 2px;font:600 13px var(--sans);color:var(--text)}
.bl{left:18px;bottom:14px;display:flex;gap:22px}.bl div b{display:block;font-size:19px;letter-spacing:-.02em}
.br{right:18px;bottom:14px;text-align:right}
.br i{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:6px;background:var(--signal)}
.br i.a{background:var(--alarm)}
.c{position:absolute;width:14px;height:14px;border:0 solid var(--line-hi);pointer-events:none}
.c1{left:8px;top:8px;border-left-width:1px;border-top-width:1px}.c2{right:8px;top:8px;border-right-width:1px;border-top-width:1px}
.c3{left:8px;bottom:8px;border-left-width:1px;border-bottom-width:1px}.c4{right:8px;bottom:8px;border-right-width:1px;border-bottom-width:1px}
.nogl .bl{bottom:34px}
"""


def _page(css: str, body: str, data: dict, script: str, mode: str = "dark") -> str:
    data["theme"] = scene_theme(mode)
    root = tokens_css(mode) + f":root{{--scene-bg:{data['theme']['bg']}}}"
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<style>{root}{_BASE_CSS}{css}</style>{_importmap()}</head><body>"
        f"{body}<script id='scene-data' type='application/json'>{_json_for_script(data)}</script>"
        f"<script type='module'>{script}</script></body></html>"
    )


def _svg_fallback(data: dict) -> str:
    """Static 2D version of the network, shown when WebGL or the CDN is unavailable."""
    nodes = {n["key"]: n for n in data["nodes"]}
    cx = sum(n["pos"][0] for n in nodes.values()) / len(nodes)
    th, pl = data["theme"], data["palette"]

    def xy(key: str) -> tuple[float, float]:
        x, y, z = nodes[key]["pos"]
        return 400 + (x - cx) * 58 + z * 9, 215 - y * 52 - z * 5

    parts = ['<svg viewBox="0 0 800 430" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Agent network">']
    for a, b in data["links"]:
        (x1, y1), (x2, y2) = xy(a), xy(b)
        hot = data["recent_flagged"].get(a) or data["recent_flagged"].get(b)
        stroke = th["alarm"] if hot else th["link"]
        mx, my = (x1 + x2) / 2 + (y2 - y1) * 0.1, (y1 + y2) / 2 - (x2 - x1) * 0.1
        parts.append(
            f'<path d="M{x1:.1f},{y1:.1f} Q{mx:.1f},{my:.1f} {x2:.1f},{y2:.1f}" fill="none" '
            f'stroke="{stroke}" stroke-opacity=".6" stroke-width="1" stroke-dasharray="3 5"/>'
        )
    for key, n in nodes.items():
        x, y = xy(key)
        count = data["activity"].get(key, 0)
        hot = bool(data["recent_flagged"].get(key))
        color = th["alarm"] if hot else th["accent"]
        r = 9 if key == "orchestrator" else 6
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r + 7}" fill="none" stroke="{color}" stroke-opacity=".35"/>'
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{color}"/>'
            f'<text x="{x:.1f}" y="{y + r + 22:.1f}" text-anchor="middle" fill="{pl["text"]}" '
            f'font-family="Instrument Sans, Segoe UI, sans-serif" font-size="12" font-weight="600">{escape(n["label"])}</text>'
            f'<text x="{x:.1f}" y="{y + r + 36:.1f}" text-anchor="middle" fill="{pl["dim"]}" '
            f'font-family="JetBrains Mono, Consolas, monospace" font-size="10">{count} ev</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _fmt_money(v: Any) -> str:
    try:
        return f"{float(v):,.2f}"
    except (TypeError, ValueError):
        return "0.00"


def agent_network_html(events: list[dict], kpis: dict | None = None, mode: str = "dark") -> str:
    data = scene_data(events, kpis)
    data["theme"] = scene_theme(mode)
    data["palette"] = pal(mode)
    t = data["totals"]
    flagged_cls = "bad" if t["flagged"] else ""
    body = (
        "<div id='stage'></div>"
        f"<div id='fallback'>{_svg_fallback(data)}<div id='fallback-note'></div></div>"
        "<div class='c c1'></div><div class='c c2'></div><div class='c c3'></div><div class='c c4'></div>"
        "<div class='hud tl'><h2>Agent network</h2>"
        f"particle rate follows audit events per actor, last {t['events']}</div>"
        "<div class='hud bl'>"
        f"<div>events<b>{t['events']}</b></div>"
        f"<div>flagged<b class='{flagged_cls}'>{t['flagged']}</b></div>"
        f"<div>interactions<b>{escape(str(t['interactions']))}</b></div>"
        f"<div>paid<b>{escape(str(t['paid']))}</b></div>"
        f"<div>revenue TND<b>{_fmt_money(t['revenue'])}</b></div></div>"
        "<div class='hud br'><div><i></i>normal traffic</div><div><i class='a'></i>block or escalate</div></div>"
    )
    return _page(_HUD_CSS, body, data, _read("agent_network.js"), mode)


def integrity_ring_html(verify: dict | None, mode: str = "dark") -> str:
    verify = verify or {}
    ok = bool(verify.get("ok"))
    checked = int(verify.get("checked") or 0)
    bad_id = verify.get("first_bad_id")
    # the ring has 40 segments; map the bad link's position in the chain onto them
    bad_index = 0 if ok or not checked else 18
    data = {"ok": ok, "bad_index": bad_index}
    p = pal(mode)
    state = "chain verified" if ok else "chain broken"
    detail = f"{checked} events checked" if ok else f"first bad link {escape(str(bad_id))}"
    body = (
        "<div id='stage'></div>"
        f"<div id='fallback'><div id='fallback-note' style='left:50%;top:50%;bottom:auto;transform:translate(-50%,-50%);"
        f"color:{p['signal_ink'] if ok else p['alarm_ink']}'>{state}</div></div>"
        f"<div class='hud tl' style='left:0;right:0;text-align:center;top:auto;bottom:12px'>"
        f"<b class='{'' if ok else 'bad'}' style='font-size:12px'>{state}</b><br>{detail}</div>"
    )
    return _page(_HUD_CSS, body, data, _read("integrity_ring.js"), mode)
