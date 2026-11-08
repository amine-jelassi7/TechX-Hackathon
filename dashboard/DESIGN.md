# SoukSaaS owner console: design notes

## Run it without the real backend
```
uv run uvicorn tests.fixtures.mock_owner_api:app --port 8001
$env:API_URL="http://127.0.0.1:8001"; uv run streamlit run dashboard/app.py --server.port 8599
```
(bash: `API_URL=http://127.0.0.1:8001 uv run streamlit run dashboard/app.py --server.port 8599`)

## Direction: night-shift instrument panel
The owner is a merchant leaving autonomous agents running overnight, so the console reads like
an instrument cluster, not a SaaS admin. Near-black, hairline grid, tabular numerals, and a single
object that earns attention: a slowly turning 3D map of the agent team with traffic flowing along
its links. Everything else stays quiet so that red means something.

## Research notes
- Collect UI (analytics-chart, dashboard) is client-rendered, so the fetch returned no gallery
  content; patterns below come from the secondary dashboard sources found by search and the
  three.js r160 `webgl_postprocessing_unreal_bloom` source (EffectComposer, RenderPass,
  UnrealBloomPass, OutputPass; bloom is applied last and tuned low: strength well under 1).
- Dense-dashboard conventions kept: monospace tabular numerals for metrics, IDs, timestamps;
  sans for labels and prose; thin 1px borders instead of shadows; inline sparklines; sans weight
  raised one step on dark backgrounds.

## Tokens
| token | hex | use |
|---|---|---|
| void | `#06080B` | app background |
| panel | `#0B0F14` | cards, sidebar |
| panel-hi | `#111820` | hover, inputs |
| line | `#1B242E` | 1px borders, grid |
| line-hi | `#2A3744` | focused/active borders |
| text | `#DCE3EA` | primary text |
| dim | `#7F8E9D` | labels, axes |
| faint | `#4B5866` | disabled, ticks |
| signal (accent) | `#46B4FF` | the one accent: data, active nav, OK state |
| alarm (alert) | `#FF5A36` | block, escalate, tampered, errors only |

Chart series use signal at stepped opacity plus neutral greys; alarm is reserved for flagged data.

## Type
Instrument Sans (UI, 400/500/600) and JetBrains Mono (numerals, ids, timestamps, 400/500).
Labels are sentence case, 12px, dim; numerals 30px mono with tabular figures.

## Motion
One orchestrated moment: the network scene (auto-rotate 0.04 rad/s, particles, pulses on flagged
actors). Everything else is static; hover only changes border colour. `prefers-reduced-motion`
stops the rotation and particles.

## Deliberately avoided
Purple/pink gradients, glassmorphism, emoji, glow on every element (bloom only in the scene),
all-caps tracked eyebrows, identical rounded card walls (radius 2px on cards, 999px only on
status pills), numbered markers on non-sequences, default Plotly colours.

## Light theme (a separate design, not an inverted dark)
Sidebar toggle "Light theme", default dark, stored in `st.session_state["light_mode"]` and in `?theme=light|dark`
(a refresh keeps it). Tokens live in `dashboard/palette.py`; CSS, Plotly (`souk_dark`, `souk_light`) and both
three.js scenes read from it. Cool paper background, white panels, same accent hue (205 degrees) darkened for
contrast, same alarm hue. Alarm stays a fill colour; text uses a darker `alarm ink`.
| token | dark | light | contrast of text tokens on bg (light) |
|---|---|---|---|
| void (page) | `#06080B` | `#F3F5F8` | |
| panel / panel-hi | `#0B0F14` / `#111820` | `#FFFFFF` / `#E9EEF3` | |
| line / line-hi | `#1B242E` / `#2A3744` | `#D8DFE6` / `#B7C3CF` | |
| text | `#DCE3EA` | `#0E1822` | 16.4:1 |
| dim | `#7F8E9D` | `#4A5968` | 6.6:1 |
| signal (accent) | `#46B4FF` | `#0B6BB8` | 5.1:1 (white on it 5.5:1) |
| alarm fill / alarm ink | `#FF5A36` / same | `#FF5A36` / `#B83216` | ink 5.5:1 |
`tests/unit/test_dashboard_api.py` asserts every text token is at least 4.5:1 on every surface, both modes.
Scenes: dark uses additive particles and bloom (0.38/0.4/0.45); light uses normal blending, no bloom, a white
scene background and darker links, because glow does not read on a light surface.
`.streamlit/config.toml` keeps one dark `[theme]`. Streamlit 1.67 does support `[theme.light]` and
`[theme.dark]`, but those follow the OS and cannot be driven by our toggle, so they would fight it. The
light mode instead re-skins Streamlit's widgets with the CSS tokens (base.css, last section).

## Motion (added)
- Entrance (header, section rules, KPI cards staggered 50 ms, tables): CSS `sc-in`, 280 ms, 8 px rise. The class is
  only in the markup on the run where the page changed (`theme.begin_page` / `end_page`), so reruns, button
  clicks, theme toggles and the 5 s feed fragment never replay it.
- KPI numerals count up from 0 (900 ms) only in that same entry render (`.sc-count`, `static/motion.js`).
- Hover/focus on cards, buttons, nav, table rows, inputs: 150-200 ms ease-out.
- Live feed: rows whose audit id was not on screen at the previous refresh slide in; block/escalate rows also
  flash alarm for 1.8 s. The first render of the table seeds the known ids, so nothing animates at load.
- Approvals: Approve/Reject shows the decision on the card at once and fades a copy of it out.
- Network scene: 2.4 s camera push-in, node hover highlight (node, shell, label, links), alarm particle bursts
  from nodes with recent block/escalate events. Plotly figures have a 450 ms transition on updates.
- `prefers-reduced-motion` turns all of it off (CSS, count-up, feed rows, scene animation and bursts).
