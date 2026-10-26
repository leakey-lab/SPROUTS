"""SPROUTS desktop entry point.

Start it with ``python main.py`` from any folder, or with the ``sprouts`` script
that ``pip install .`` creates (``sprouts = "main:main"``).

Import order matters: the Qt/Chromium environment flags must be set before Qt
loads, logging before any other app module, and QtWebEngine before the
QApplication is created.
"""

import os
import sys

# Qt / Chromium start-up flags per OS (D3D11 / ANGLE on Windows only); see
# app/qt_environment.py. Qt reads them once, when it loads.
from app.qt_environment import (
    DESKTOP_FILE_NAME,
    configure_qt_environment,
    qt_style_for,
)

_QT_ENV_SET = configure_qt_environment(
    os.environ, euid=os.geteuid() if hasattr(os, "geteuid") else None
)

# Logging must be configured before any app module is imported so that
# module-level loggers (e.g. config._auto_skeleton_batch) are captured.
from app.logging_config import current_log_file, setup_logging  # noqa: E402

setup_logging()
import logging  # noqa: E402

logger = logging.getLogger(__name__)

from PyQt6.QtCore import QCoreApplication, QEvent, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QIcon, QPalette  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

# QtWebEngine refuses to load once a QCoreApplication exists, and the dashboards
# may be imported lazily (after startup), so load it here explicitly.
if QCoreApplication.instance() is None:
    try:
        import PyQt6.QtWebEngineWidgets  # noqa: E402,F401
    except ImportError as _exc:
        logger.warning("QtWebEngine unavailable, dashboards will not open: %s", _exc)

try:
    from app.gui.error_hooks import (
        enable_fault_log,
        install_exception_hooks,
        install_qt_message_handler,
    )
    from app.gui.main_window import MainWindow
    from app.gui.widgets import tokens
    from app.workers import describe_error
    from resources.resource_utils import resource_path  # re-exported: the ui-harness uses it
except Exception:
    # Otherwise the traceback reaches only stderr, which pythonw.exe and windowed
    # builds discard: a broken install would close without a trace.
    logger.critical("Could not import the application", exc_info=True)
    raise

__all__ = ["MainWindow", "apply_stylesheet", "main", "resource_path"]

# The running QApplication and main window, held at module level. Once the event
# loop returns, main() deletes the window itself (see _release_window); the
# QApplication lives until interpreter exit.
_session: tuple[QApplication, MainWindow] | None = None


def apply_stylesheet(app: QApplication) -> None:
    """Apply the SPROUTS QSS theme and the matching application palette."""
    with open(resource_path("resources", "themes", "dark_theme.qss"), encoding="utf-8") as f:
        app.setStyleSheet(f.read())
    app.setPalette(_sprouts_palette())


def _sprouts_palette() -> QPalette:
    """Application palette built from the SPROUTS design tokens (matches the QSS)."""
    palette = QPalette()
    role = QPalette.ColorRole
    for color_role, color in (
        (role.Window, tokens.BG_0),
        (role.WindowText, tokens.TEXT),
        (role.Base, tokens.BG_1),
        (role.AlternateBase, tokens.BG_2),
        (role.ToolTipBase, tokens.BG_1),
        (role.ToolTipText, tokens.TEXT),
        (role.Text, tokens.TEXT),
        (role.Button, tokens.BG_3),
        (role.ButtonText, tokens.TEXT),
        (role.Link, tokens.INFO),
        (role.Highlight, tokens.SEL),
        (role.HighlightedText, tokens.BG_0),
    ):
        palette.setColor(color_role, QColor(color))
    palette.setColor(role.BrightText, QColor(Qt.GlobalColor.white))
    # Disabled controls use the disabled tier (TEXT_FAINT is informational text, AA).
    for disabled_role in (role.Text, role.WindowText, role.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, disabled_role, QColor(tokens.TEXT_DISABLED))
    return palette


def _app_identity() -> tuple[str, str, str]:
    """(organization, application name, version) from the ``app`` package."""
    import app as package

    return (
        getattr(package, "ORGANIZATION_NAME", "LeakeyLab"),
        getattr(package, "APP_NAME", "SPROUTS"),
        getattr(package, "__version__", ""),
    )


def _apply_window_icon(app: QApplication) -> None:
    icon_path = resource_path("resources", "app_icon.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    else:
        logger.warning("App icon not found: %s", icon_path)


def _release_window(window: MainWindow) -> None:
    """Delete ``window`` and its pages now, while the QApplication still exists.

    Left to the garbage collector, the window (its editor pages are in reference
    cycles) could be destroyed after the QApplication at interpreter exit, which
    sometimes crashed the process on its way out (access violation). By the time
    the event loop returns, the window's closeEvent has already asked about unsaved
    edits and stopped any running job.
    """
    window.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def main(argv: list[str] | None = None) -> int:
    """Start the SPROUTS GUI and run the event loop.

    Args:
        argv: Qt command-line arguments (defaults to ``sys.argv``).

    Returns:
        The process exit code (1 if the main window could not be created).
    """
    global _session
    app = QApplication.instance() or QApplication(sys.argv if argv is None else argv)
    organization, name, version = _app_identity()
    app.setOrganizationName(organization)
    app.setApplicationName(name)
    app.setApplicationVersion(version)

    install_exception_hooks(app)
    install_qt_message_handler()
    log_file = current_log_file()
    if log_file is not None:
        enable_fault_log(log_file.with_name("fault.log"))

    logger.info(
        "Starting %s %s (QSG_RHI_BACKEND=%s; start-up environment set: %s)",
        name,
        version,
        os.environ.get("QSG_RHI_BACKEND"),
        _QT_ENV_SET,
    )
    _apply_window_icon(app)
    app.setDesktopFileName(DESKTOP_FILE_NAME)
    style = qt_style_for()
    if style:
        app.setStyle(style)
    try:
        apply_stylesheet(app)
    except OSError:
        logger.exception("Theme not applied; continuing with the default style")

    logger.info("Creating MainWindow")
    try:
        window = MainWindow()
    except Exception as exc:
        logger.critical("Startup failed", exc_info=True)
        where = f"\n\nDetails were written to {log_file}" if log_file else ""
        QMessageBox.critical(None, f"{name} could not start", f"{describe_error(exc)}{where}")
        return 1
    window.show()
    _session = (app, window)
    logger.info("Entering Qt event loop")
    try:
        return app.exec()
    finally:
        _release_window(window)


if __name__ == "__main__":
    sys.exit(main())
