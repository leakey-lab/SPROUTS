"""The suite logs into a temp folder, never into the user's application log (X5)."""

import os
import tempfile
from pathlib import Path

from app import logging_config


def test_the_suite_logs_into_a_temp_folder(monkeypatch):
    configured = os.environ["SPROUTS_LOG_DIR"]
    assert logging_config.default_log_dir() == Path(configured)
    assert Path(configured).resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())

    from app.user_paths import user_log_dir

    monkeypatch.delenv("SPROUTS_LOG_DIR")
    assert Path(user_log_dir()) != Path(configured)
