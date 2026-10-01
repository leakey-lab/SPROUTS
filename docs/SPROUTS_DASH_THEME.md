# SPROUTS — Dashboard Visualization Theme & Polish

A handoff for applying a cohesive, professional look to the embedded Dash/Plotly
dashboards (`root length` + `root area`) **without touching any data logic**.

You (Claude, running locally in the repo) will:

1. Drop in two new files (`theme.py`, `assets/style.css`).
2. Register the theme at app startup.
3. Replace the repeated 40-line `update_layout(...)` blocks with the template.
4. Remove emoji / inline-`<span>` hover HTML in favour of `theme.hover(...)`.
5. Switch the view selector from a dropdown to a one-click segmented control.
6. Wrap graphs in `dcc.Loading` and move pure UI toggles to clientside callbacks.

> **Guardrail:** none of these steps change *what* is computed or plotted — only
> styling, structure, and round-trip behaviour. Keep every existing trace,
> `customdata`, and callback `Output` intact. Run `pytest` after each step.

---

## 0. Where things are

| Concern | File |
|---|---|
| Length dashboard layout + callbacks | `app/visualization/dash_app.py` |
| Length chart builders | `app/visualization/dash_visualizations.py` |
| Area dashboard | `app/visualization/dash_app_area.py` |
| Qt wrappers (embed Dash in `QWebEngineView`) | `root_length_visulization.py`, `root_area_visualization.py` |
| Ports | `app/config.py` (`DASH_LENGTH_PORT`, `DASH_AREA_PORT`) |

Both dashboards currently use `external_stylesheets=[dbc.themes.BOOTSTRAP]`, a
light `#f8f9fa` page, white cards, `#2c3e50` 36px titles, the default
`px.colors.qualitative.Plotly` rainbow, and **re-declare the same layout dict**
(`hoverlabel`, `legend`, `plot_bgcolor`, margins, fonts) inside every chart
method. That duplication is the root cause of the generic look.

---

## 1. Install the two files

```
app/
  visualization/
    theme.py            ← new  (Plotly template + helpers)
  assets/               ← new folder if it doesn't exist
    style.css           ← new  (Dash HTML dark theme)
```

**Important — the `assets/` location.** Dash auto-serves an `assets/` folder
that sits **next to the file where `Dash(__name__, ...)` is instantiated**, or at
the app root. Because both dashboards do `Dash(__name__, ...)` from inside
`app/visualization/`, put `assets/` at `app/assets/` **and** pass an explicit
`assets_folder` to be safe (step 2). If you prefer, set
`assets_folder=os.path.join(os.path.dirname(__file__), "assets")`.

Fonts: `style.css` and `theme.py` ask for **IBM Plex Sans / Mono**. Either bundle
the font files in `assets/fonts/` with an `@font-face`, or add the Google Fonts
`<link>` via `app.index_string`. They degrade to `system-ui` if absent.

---

## 2. Register the theme + go dark (both dashboards)

In `dash_app.py` and `dash_app_area.py`, at the top:

```python
from . import theme           # registers "sprouts" + "sprouts_light" on import
theme.use("sprouts")          # make it the global Plotly default
```

Change the `Dash(...)` construction. Swap the light Bootstrap base for a dark one
so dbc components start dark, and point at the assets folder:

```python
import os
import dash_bootstrap_components as dbc

self.app = Dash(
    __name__,
    external_stylesheets=[dbc.themes.DARKLY],   # was BOOTSTRAP
    assets_folder=os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets"),
    suppress_callback_exceptions=True,
    update_title=None,
    compress=True,
)
```

If you want IBM Plex via CDN, set `app.index_string` (optional):

```python
self.app.index_string = '''<!DOCTYPE html><html><head>{%metas%}<title>{%title%}</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">
{%favicon%}{%css%}</head><body>{%app_entry%}<footer>{%config%}{%scripts%}{%renderer%}</footer></body></html>'''
```

**Page + title classes.** In `_setup_layout`, give the outer `html.Div` the app
class and drop the inline light background; turn the H1 into a themed title:

```python
self.app.layout = html.Div(
    className="sprouts-app",
    style={"display": "flex", "flexDirection": "column", "alignItems": "center",
           "minHeight": "100vh", "width": "100%", "padding": "16px"},   # no backgroundColor
    children=[
        html.Div("Root Length Visualization",          # eyebrow + title pattern
                 className="sprouts-title", style={"fontSize": "26px", "margin": "8px 0 24px"}),
        ...
```

Remove the per-element `"color": "#2c3e50"`, `"backgroundColor": "#f8f9fa"`,
`"backgroundColor": "white"` style keys throughout the layout — the stylesheet
now owns those. Leave structural styles (flex, widths, spacing) alone.

---

## 3. Collapse the duplicated layout dicts

Because `theme.use("sprouts")` sets the default template, **every `go.Figure()`
inherits fonts, colours, grid, legend, and hoverlabel automatically.** Each chart
method should now only set its title and axis *titles*.

### `dash_visualizations.py` — example: `create_stacked_bar_chart`

**Before** (≈45 lines of `update_layout` with hardcoded legend/hover/bg/margins):

```python
fig.update_layout(
    barmode="stack",
    title={"text": "Root Length Growth by Tube and Date", "x": 0.5, ...},
    xaxis=dict(title=dict(text="Tube", font=dict(size=18)), tickfont=dict(size=14)),
    yaxis=dict(title=dict(text="Total Length (mm)", ...)),
    legend_title=...,  legend={...}, plot_bgcolor="white",
    hoverlabel=dict(bgcolor="white", ...), margin={...}, ...
)
fig.update_xaxes(showgrid=True, gridcolor="lightgray", showline=True, linecolor="black", ...)
fig.update_yaxes(...)
```

**After:**

```python
fig.update_layout(
    barmode="stack",
    title_text="Root Length Growth by Tube and Date",
    xaxis_title_text="Tube",
    yaxis_title_text="Total Length (mm)",
    legend_title_text="Date",
    hovermode="x unified",          # keep view-specific overrides only
)
```

Delete the `update_xaxes`/`update_yaxes` colour calls — the template supplies grid,
zeroline, and axis-line styling. Do the same trim in `show_growth_lines`,
`show_growth_over_time`, and the area dashboard's chart builders.

### `make_subplots` figures (`create_faceted_depth_profile`)

Subplots **do not** inherit the default template as reliably. Pass it explicitly
once, then keep your existing per-axis range/tick logic (that's data-driven, keep it):

```python
fig = make_subplots(rows=num_dates, cols=num_tubes, ...)
...
theme.style(fig, title="Faceted Depth Profile — Field Avg (left) vs Tube (right)")
```

For the manual `fig.add_shape` background rectangles in the faceted view, change
the light-blue fill to a theme surface:

```python
fillcolor="rgba(40,42,55,0.55)",            # was rgba(173,216,230,0.35)
line=dict(color="#2a2c39", width=1),        # was black width 2
```

And the inline-coloured "No Data" / error annotations:

```python
font=dict(size=20, color="#9498ad"),        # was "red"
```

---

## 4. Clean hover text (drop the emoji + inline colours)

The current hovertemplates embed `🧪 📏 🌾 ⚠` and per-line
`<span style='color:#27ae60'>…</span>`. The colour now comes from the template's
`hoverlabel`, so replace those with `theme.hover(...)`:

**Before:**

```python
hovertemplate=(
    "<b style='font-size:15px; color:#2c3e50;'>🧪 Tube %{x}</b><br>"
    "<span style='color:#7f8c8d;'>────────────</span><br>"
    "<b>Date:</b> <span style='color:#3498db;'>%{x|%b %d, %Y}</span><br>"
    "<b>Total Length:</b> <span style='color:#27ae60;'>%{y:.2f} mm</span><br>"
    "<extra></extra>"
)
```

**After:**

```python
hovertemplate=theme.hover("Tube %{x}", [
    ("Date",   "%{x|%b %d, %Y}"),
    ("Length", "%{y:.2f} mm"),
])
```

Keep every `customdata=` array exactly as-is — only the display string changes.
Apply across `show_growth_lines`, `show_growth_over_time`, `create_stacked_bar_chart`,
`create_faceted_depth_profile`, and the area equivalents.

---

## 5. View selector → segmented control (one click instead of a dropdown)

You have exactly four views (`stacked / time / lines / faceted`). Replace the
`dcc.Dropdown(id="view-selector", ...)` with a `dcc.RadioItems` styled as a
segmented control. **Keep the id and the `value`s** so the existing
`toggle_view_panels` and `update_visualization` callbacks (which read
`Input("view-selector", "value")`) work unchanged.

```python
dcc.RadioItems(
    id="view-selector",
    options=[
        {"label": "Stacked Bar", "value": "stacked"},
        {"label": "Growth Over Time", "value": "time"},
        {"label": "Growth Lines", "value": "lines"},
        {"label": "Faceted Depth", "value": "faceted"},
    ],
    value="stacked",
    className="seg",                 # styled in style.css
    inputClassName="seg-radio",
    labelClassName="seg-btn",
)
```

`style.css` styles `.seg`/`.seg-btn`. To get the "pill highlights the selected
option" effect with `RadioItems`, either hide the native radio dot
(`.seg-radio{display:none}`) and toggle `.on` via a tiny clientside callback, or
use `dbc.RadioItems(..., class_name="btn-group", input_class_name="btn-check",
label_class_name="btn btn-outline-secondary")` for Bootstrap's native button
group. The dbc button-group route needs no JS — prefer it if you want zero
clientside code.

---

## 6. Loading states + clientside toggles

**Wrap the graph** so recomputes show a spinner instead of freezing:

```python
dcc.Loading(
    type="default", color="#5fd6a0",
    children=dcc.Graph(id="main-graph", className="sprouts-graph-card", config={...}),
)
```

(Keep the existing `config={...}` dict — scrollZoom, SVG export, downloadCsv.)

**Move pure-UI server round-trips to the browser.** The legend toggle currently
costs a server callback. Replace `toggle_legend` with a clientside callback:

```python
self.app.clientside_callback(
    "function(n, vis){ return n ? !vis : vis; }",
    Output("legend-visible-store", "data"),
    Input("toggle-legend-btn", "n_clicks"),
    State("legend-visible-store", "data"),
)
```

The panel show/hide in `toggle_view_panels` can stay server-side (it also computes
options), or be split: keep the options server-side, move the `style` flips
clientside. Optional — only if you want the snappier feel.

---

## 7. Apply the same pass to the area dashboard

`dash_app_area.py` mirrors the length dashboard (note `_GRAPH_STYLE` at module top,
the light `#f8f9fa` page, `#2c3e50` H1). Repeat steps 2–6 there:

- `theme.use("sprouts")` is global, so once is enough — but still swap
  `BOOTSTRAP → DARKLY` and add `assets_folder` for the area `Dash(...)`.
- Replace `_GRAPH_STYLE`'s `backgroundColor/boxShadow` with `className="sprouts-graph-card"`.
- Trim its chart builders' `update_layout` dicts and hovertemplates the same way.
- Its sequential metric is area — keep `theme.SEQUENTIAL` (Plasma) for any
  depth/area colour mapping.

---

## 8. Verify

```bash
pytest -q                       # logic untouched → all existing tests pass
python main.py                  # launch; open both dashboards
```

Visual checklist:
- [ ] Dashboard page is dark; cards, dropdowns, tabs, badges all themed.
- [ ] Charts use the green/blue/purple colorway, not the default rainbow.
- [ ] Hover boxes are dark, consistent, emoji-free.
- [ ] View switch is one click; selected view is visually obvious.
- [ ] Switching views shows a brief spinner, not a frozen chart.
- [ ] Faceted view: subplot backgrounds + "No Data" text read on dark.
- [ ] SVG export (modebar camera) still works and downloads.

See `Theme Preview.html` (in the design project) for the target before/after look.

---

## Token reference (theme.py ↔ style.css ↔ SPROUTS shell)

| Token | Value | Use |
|---|---|---|
| `BG_0` | `#15161c` | page / `paper_bgcolor` |
| `BG_2` | `#21232e` | panels / `plot_bgcolor` |
| `BG_3` | `#282a37` | cards, inputs, hover box |
| `BORDER` | `#2a2c39` | hairlines, axis lines |
| `TEXT` | `#eceef5` | primary text, titles |
| `TEXT_MUTED` | `#9498ad` | axis ticks, legend, secondary |
| `ACCENT` | `#5fd6a0` | primary action, spinner, selection pill |
| `SEL` | `#b794f6` | highlight / selection (purple) |
| `COLORWAY` | 10 hues | categorical series |
| `SEQUENTIAL` | `Plasma` | ordered (depth) colour mapping |

To produce a **light** build instead (e.g. for print/export), call
`theme.use("sprouts_light")` — identical geometry, light surfaces — and skip the
DARKLY swap / dark `style.css` classes.

## API quick reference (`theme.py`)

```python
theme.use("sprouts")                  # set global default template
theme.style(fig, title=..., height=...)   # explicit apply (subplots, pre-default figs)
theme.hover("Tube %{x}", [("Date","%{x}"), ("Length","%{y:.2f} mm")])  # clean hovertemplate
theme.color(i)                        # categorical colour by series index
theme.COLORWAY, theme.SEQUENTIAL, theme.ACCENT, ...   # raw tokens
theme.bar_labels(text, bars)          # go.Bar label args: text only up to MAX_LABELLED_BARS (30) bars
theme.margin_right(show_legend)       # MARGIN_RIGHT_LEGEND (180) or MARGIN_RIGHT_NO_LEGEND (24)
theme.message_figure(text, color=...) # text-only figure for hints, empty states and errors
```

## Changes since this handoff (2026-09-30)

The dashboards follow this guide; the UX and speed pass changed these parts (display only,
no data changes):

- **plotly.js bundle.** `app/assets/plotly-basic.min.js` (plotly.js basic 3.5.0, MIT,
  `app/assets/licenses/plotly.js-MIT.txt`) is served before the Dash renderer, and
  `dcc.Graph` uses it through `window.Plotly` instead of fetching the full 4.8 MB bundle.
  It must match plotly.py's plotly.js version, and the dashboards may use only its trace
  types (scatter, bar, pie): `tests/test_viz_dash_plotly_bundle.py` fails otherwise, and
  `app/assets/THIRD_PARTY.md` says how to update it.
- **`style.css`.** The chart card (`.viz-chart-card`) uses the paper colour `--bg-0` and
  `scrollbar-gutter: stable`, so a scrollbar appearing under the tall faceted grid no longer
  narrows the chart and redraws it. The control strip collapses without a transition
  (`.viz-controls .collapsing`), which used to redraw the chart on every frame.
- **Bar labels.** `theme.bar_labels` shows value labels only when at most
  `theme.MAX_LABELLED_BARS` (30) bars share the x axis; the hover keeps the values.
- **Legend.** The time views of both dashboards show their legend automatically for up to
  `dash_visualizations.AUTO_LEGEND_MAX_TUBES` (30) tubes: `legend-visible-store` starts as
  `None` (automatic), the toggle flips what is shown, and `theme.margin_right` makes room
  for the legend only when it is shown. The area view takes these pieces from `dash_shell`
  (`legend_store`, `legend_shown`, `flip_legend`, `legend_patch`,
  `DashboardShell.register_legend_toggle`); its *Toggle Legend* floats over the chart's
  top-left margin on the time view only, so that view keeps no control strip.
- **Message figures.** `theme.message_figure` is text only (no axes, transparent plot area),
  so a hint no longer looks like an empty chart.
- **Section 6, further.** The strip's open state, its caret and the length view's option
  cards are clientside callbacks (ids and outputs unchanged; the tests run the JavaScript in
  QJSEngine through `tests/viz_dash_helpers.call_clientside`). The whole strip
  (`viz-controls`) hides for views without settings. After a strip or view change the refit
  clears plotly.js's `_initialAutoSizeIsDone`, so the next draw measures the chart card
  instead of drawing at the old size and redrawing.
