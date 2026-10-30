"""``ui_panels.get_icon_path``: an icon file of ``resources/icons``, or "".

The hidden Load Images button takes its icon from here. The old second folder
(``app/utils``, unused XML icons) is gone, so a name that is not in ``resources/icons`` is
not found anywhere else.
"""

from __future__ import annotations

import os

from app.gui.ui_panels import get_icon_path
from resources.resource_utils import project_root


def test_the_load_button_icon_is_found_in_resources_icons():
    expected = os.path.join(str(project_root()), "resources", "icons", "folder-open.svg")
    assert get_icon_path("folder-open.svg") == expected
    assert os.path.isfile(expected)


def test_a_name_that_is_not_in_resources_icons_gives_an_empty_path():
    assert get_icon_path("no-such-icon.svg") == ""
    assert get_icon_path("calc-area.xml") == ""  # one of the removed app/utils icons
