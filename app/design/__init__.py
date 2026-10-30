"""Qt-free SPROUTS design system (the single source of UI colours and metrics).

``app.design.tokens`` holds plain constants and QSS string helpers only, so the Qt
widgets (``app.gui.widgets.tokens`` re-exports it), the app palette and the Dash theme
can share one set of values without importing PyQt6.
"""
