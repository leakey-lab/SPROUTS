"""conftest.py: one QApplication for the session, as in a full run (OpenGL contexts shared,
raster canvases by default), and torch's idle OpenMP workers sleep at once."""

import os

import pytest
from PyQt6.QtCore import QCoreApplication, Qt
from PyQt6.QtWidgets import QApplication

from app.gui import canvas_gl


def test_app_and_qapp_are_the_one_application(app, qapp):
    assert isinstance(app, QApplication)
    assert app is qapp is QApplication.instance()


def test_the_application_shares_opengl_contexts(app):
    assert QCoreApplication.testAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)


def test_the_environment_defaults_are_set():
    # conftest.py sets each one unless the environment already had it
    assert canvas_gl.ENV_VAR in os.environ
    assert "KMP_BLOCKTIME" in os.environ
    assert os.environ["QT_QPA_PLATFORM"]


class TestAModuleWithItsOwnApp:
    """A module's ``app`` fixture of a narrower scope (a themed app, say) still lets a test
    ask for the session's ``qapp``: no scope error."""

    @pytest.fixture(scope="class")
    def app(self):
        return QApplication.instance() or QApplication([])

    def test_qapp_is_the_same_application(self, app, qapp):
        assert app is qapp
