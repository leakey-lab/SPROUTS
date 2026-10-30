"""The suite keeps the user's config folder (the Enhance defaults) out of reach: every test
has a temp folder of its own, so one test's saved defaults never reach a later test
(conftest.py)."""

import os
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

from app.enhancement import EnhanceDefaultsStore
from app.user_paths import user_config_dir

_FOLDERS: list[str] = []  # the config folders of the tests below that ran
_SAVED: list[str] = []  # the defaults file a test below saved


def _in_temp(path: str) -> bool:
    return Path(path).resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())


@pytest.fixture(scope="module")
def module_config_dir():
    """``SPROUTS_CONFIG_DIR`` as a module fixture sees it: the session's folder."""
    return os.environ["SPROUTS_CONFIG_DIR"]


def test_the_suite_keeps_user_config_in_a_temp_folder():
    configured = os.environ["SPROUTS_CONFIG_DIR"]
    assert _in_temp(configured)
    assert user_config_dir(ensure=False) == configured
    assert Path(EnhanceDefaultsStore().user_path).parent == Path(configured)


def test_module_fixtures_are_kept_out_of_the_user_folder_too(module_config_dir):
    assert _in_temp(module_config_dir)
    configured = os.environ["SPROUTS_CONFIG_DIR"]
    assert configured != module_config_dir  # this test's own folder ...
    assert Path(configured).is_relative_to(Path(module_config_dir))  # ... in the session's


@pytest.mark.parametrize("run", [1, 2])
def test_every_test_has_its_own_config_folder(run):
    configured = os.environ["SPROUTS_CONFIG_DIR"]
    assert configured not in _FOLDERS
    assert not os.path.exists(configured)  # made when the app first writes there
    _FOLDERS.append(configured)


def test_defaults_a_test_saves_stay_in_its_folder():
    store = EnhanceDefaultsStore()
    assert store.save(replace(store.load(), tile=3))
    assert EnhanceDefaultsStore().load().tile == 3
    _SAVED.append(store.user_path)


def test_the_next_test_starts_from_the_factory_defaults():
    if not _SAVED:
        pytest.skip("runs after test_defaults_a_test_saves_stay_in_its_folder")
    assert os.path.exists(_SAVED[0])
    store = EnhanceDefaultsStore()
    assert store.user_path != _SAVED[0]
    assert not os.path.exists(store.user_path)
    assert store.load().tile != 3
