# SPROUTS — Dashboard Visualization Theme & Polish

> Sections 0 to 8 describe the 2026-06-01 migration as done (the theme, `style.css` and the
> segmented control exist); the "Changes since this handoff" section at the end is current.

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
| Length dashboard layout + callbacks | `app/visualization/dash_app.py` (composition), `dash_length_layout.py`, `dash_length_views.py`, `dash_length_callbacks.py` |
| Length chart builders | `app/visualization/dash_visualizations.py` |
| Area dashboard | `app/visualization/dash_app_area.py` |
| Qt wrappers (embed Dash in `QWebEngineView`) | `root_length_visulization.py`, `root_area_visualization.py` |
| Ports | `app/config.py` (`DASH_LENGTH_PORT`, `DASH_AREA_PORT`) |

Before the migration both dashboards used `external_stylesheets=[dbc.themes.BOOTSTRAP]`, a
light `#f8f9fa` page, white cards, `#2c3e50` 36px titles, the default
`px.colors.qualitative.Plotly` rainbow, and **re-declared the same layout dict**
(`hoverlabel`, `legend`, `plot_bgcolor`, margins, fonts) inside every chart
method. That duplication was the root cause of the generic look. No `dbc.themes` or
`external_stylesheets` is left in `app/`.

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

Fonts: `style.css` asks for **IBM Plex Sans / Mono**, `theme.py` for IBM Plex Sans. The fonts are
vendored in `app/assets/fonts/`; nothing is fetched from the internet
(`app/assets/THIRD_PARTY.md`). They degrade to `system-ui` if absent.

---

## 2. Register the theme + go dark (both dashboards)

In `dash_app.py` and `dash_app_area.py`, at the top:

```python
from . import theme           # registers "sprouts" on import
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
    title={"text": "Root Length by Tube and Date", "x": 0.5, ...},
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
    title_text="Root Length by Tube and Date",
    xaxis_title_text="Tube",
    yaxis_title_text="Total Length (mm)",
    legend_title_text="Measurement date",
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
fig.update_layout(template="sprouts", title_text="Depth Profile Grid - Field Average (Left) vs Tube (Right)")
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
    "<b>Date:</b> <span style='color:#3498db;'>%{x|%Y-%m-%d}</span><br>"
    "<b>Total Length:</b> <span style='color:#27ae60;'>%{y:.2f} mm</span><br>"
    "<extra></extra>"
)
```

**After:**

```python
hovertemplate=theme.hover("Tube %{x}", [
    ("Date",   "%{x|%Y-%m-%d}"),
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
        {"label": "Totals by Tube", "value": "stacked"},
        {"label": "Growth Over Time", "value": "time"},
        {"label": "Depth Profile", "value": "lines"},
        {"label": "Depth Profile Grid", "value": "faceted"},
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

## Token reference (dashboards: `app/visualization/theme.py` ↔ `app/assets/style.css`)

These are the dashboards' tokens. The desktop shell's own tokens are in `app/design/tokens.py`; there
`ACCENT` is the purple `#c39af6` and the green `#5fd6a0` is `MASK` (the mask layer).

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
| `COLORWAY` | 10 hues | categorical series (tubes) |
| `DATE_RAMP` | 13 blues | measurement dates (see "Date ramp") |
| `SEQUENTIAL` | `Plasma` | ordered (depth) colour mapping |

## View names and the date ramp

Both dashboards name their views with the same words (keys unchanged: `stacked`, `time`,
`lines` / `profile`, `faceted`):

| Key | View label | Chart title |
|---|---|---|
| `stacked` | Totals by Tube | `Root {Length\|Area} by Tube and Date` |
| `time` | Growth Over Time | `Root {Length\|Area} Growth Over Time` |
| `lines` (length), `profile` (area) | Depth Profile | `Root {Length\|Area} Depth Profile - Tube N` |
| `faceted` (length only) | Depth Profile Grid | `Depth Profile Grid - Field Average (Left) vs Tube (Right)` |

The legend title is "Measurement date" everywhere, hover dates are ISO (`%Y-%m-%d`) and both
depth axes are titled "Position (L)". The faceted options card is "Depth Profile Grid Options".

**Date ramp.** Measurement dates are drawn on one blue ramp, `theme.DATE_RAMP`, oldest darkest,
newest lightest:
`#127abe #2c84c4 #3e8dcb #4f97d1 #5ea1d8 #6cabde #7bb4e5 #89beeb #97c8f2 #a6d2f8 #b4dcff #c8e5ff #dbeefe`.
Every stop is at least 3:1 on `BG_2` and at least 10 (OKLab distance x 100) from the mask green,
the skeleton orange and the selection purple, so a date is never read as a layer. Use
`theme.date_colors(n)` with `n` = the number of dates of the whole trial
(`len(cache.dates)`) and index it with the date's place among all of them
(`cache.dates.index(date)`): a date then has the same colour in every view and for every tube,
even for a tube that was not measured on some dates. Up to 13 dates take evenly spaced stops
(`DATE_RAMP[round(i * 12 / (n - 1))]`; one date takes the first); more are interpolated linearly
in sRGB between the 13 stops. It is applied to the stacked bars of both dashboards, the length
Depth Profile lines and the area Depth Profile bars. The tubes of Growth Over Time keep the
categorical `COLORWAY`, and the faceted bars keep Plasma (depth).

Above about seven dates neighbouring ramp colours are close. The legend order (oldest first) and
the hover (which names the date) carry the date then, not the colour alone.

## API quick reference (`theme.py`)

```python
theme.use("sprouts")                  # set global default template
theme.hover("Tube %{x}", [("Date","%{x}"), ("Length","%{y:.2f} mm")])  # clean hovertemplate
theme.color(i)                        # categorical colour by series index
theme.date_colors(n)                  # n blues from DATE_RAMP: oldest darkest, newest lightest
theme.COLORWAY, theme.SEQUENTIAL, theme.ACCENT, ...   # raw tokens
theme.bar_labels(text, bars)          # go.Bar label args: text only up to MAX_LABELLED_BARS (30) bars
theme.margin_right(show_legend)       # MARGIN_RIGHT_LEGEND (180) or MARGIN_RIGHT_NO_LEGEND (24)
theme.message_figure(text, color=...) # text-only figure for hints, empty states and errors
```

## Changes since this handoff (2026-09-30)

The dashboards follow this guide; the UX and speed pass changed these parts (display only,
no data changes), and the redundancy and speed pass (2026-10-27) made the speed changes
dated 2026-10-27, with no display or data change:

- **plotly.js bundle.** `app/assets/plotly-basic.min.js` (plotly.js basic 4.1.1, MIT,
  `app/assets/licenses/plotly.js-MIT.txt`) is served before the Dash renderer, and
  `dcc.Graph` uses it through `window.Plotly` instead of fetching the full 4.8 MB bundle.
  It must match plotly.py's plotly.js version, and the dashboards may use only its trace
  types (scatter, bar, pie): `tests/test_viz_dash_plotly_bundle.py` fails otherwise, and
  `app/assets/THIRD_PARTY.md` says how to update it. The plotly.js 3 defaults that
  plotly.js 4 changed are set back: no "Upload to Cloud" modebar button, a 300 ms
  double-click (`dash_shell.graph_config`) and the bar label colours
  (`theme.inside_label_colour`).
- **Zoom through Dash 4.4.1.** `app/assets/sprouts-uirevision.js` keeps a zoom when a
  callback redraws the view with the same `uirevision` (Select All, Clear All): Dash 4.4.1
  lost it (plotly/dash#4093).
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
  top-left margin on the time view only, so that view keeps no control strip. The length
  view takes `legend_store`, `flip_legend` and `legend_patch` (its toggle keeps its own
  registration, which fires on page load).
- **Message figures.** `theme.message_figure` is text only (no axes, transparent plot area),
  so a hint no longer looks like an empty chart.
- **Figures without validation (2026-10-27).** The Growth Over Time views and the Depth
  Profile Grid are plain dicts passed to `go.Figure(..., _validate=False)`; tests compare
  their JSON with validated figures. A template in such a dict must be the template object
  (`pio.templates["sprouts"]`), not its name: plotly.js ignores a template given as a
  string. The grid no longer calls `make_subplots` (section 3's example is history):
  `faceted_profile.grid_layout` computes the same axes, domains and titles.
- **Figures converted once.** `dash_shell.browser_figure` keeps what Dash sends of a
  memoized figure (its `to_plotly_json()`) on the figure, with its `uirevision`, so a view
  shown again is not converted again.
- **Totals by Tube, one builder (2026-10-27).** Both dashboards draw it with
  `dash_visualizations.stacked_bar_figure`, which sends each date's totals as a list:
  plotly.js leaves the `null` of a date a tube was not measured on out of the stack. A NaN
  inside a typed array (a numpy `y`) is stacked instead and restarted the tube's later dates
  at 0; the area chart used to draw that way.
- **Section 6, further.** The strip's open state, its caret and the length view's option
  cards are clientside callbacks (ids and outputs unchanged; the tests run the JavaScript in
  QJSEngine through `tests/viz_dash_helpers.call_clientside`). The whole strip
  (`viz-controls`) hides for views without settings. After a strip or view change the refit
  clears plotly.js's `_initialAutoSizeIsDone`, so the next draw measures the chart card
  instead of drawing at the old size and redrawing.
- **Per-tube selection in the browser (2026-10-27).** When the view becomes Depth Profile,
  the dropdown shows the tube the chart plots (the picked tube, else the first). This is a
  clientside callback of `dash_shell`: it reads the dataset's tubes from the
  `tube-selector` options, which list them all, so `DashboardShell.register_callbacks` no
  longer takes `tubes`. A view switch no longer waits for a server answer before it asks
  for the figure.
- **Same-size relayout skipped (2026-10-27).** `app/assets/sprouts-autosize.js` (loaded
  after `plotly-basic.min.js`: assets load in name order) answers at once the
  `relayout({autosize: true})` that `Plotly.Plots.resize` makes after a resize or a refit,
  when the chart already has the size plotly.js would measure. Every other relayout runs
  as before; `tests/test_viz_dash_plotly_bundle.py` checks the plotly.js code the guard
  mirrors.
- **Hover-image names after the first chart (2026-10-27).** The length dashboard parses its
  hover-image names on a background thread once its first chart has been sent
  (`DashApp._parse_images_after_this_response`), not while the page loads: the parse held
  the interpreter lock and delayed the page's first requests. A lookup before the parse
  ends waits for it.
