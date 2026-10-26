"""Key text for the platform in use (O21): ``Cmd`` / ``Option`` on macOS, as written elsewhere.

Qt already maps ``Ctrl`` to the Command key and ``Alt`` to Option on macOS, so every binding
works from the same ``PortableText``; only the words shown to the user differ. Texts are
written once with ``Ctrl`` / ``Alt`` (the registry, tooltips, hints) and shown through
:func:`platform_keys`. Qt-free (stdlib only).

Texts are resolved as late as their owner builds them: labels and tooltips on every call, but
module and class constants (``EMPTY_HINT``, ``_ZOOM_TIP``, ``CLEARED_NOTICE``) and
``shortcuts.REGISTRY`` once at import. ``sys.platform`` never changes in a real process; a test
that patches it afterwards sees only the call-time texts change.
"""

from __future__ import annotations

import re
import sys

__all__ = ["platform_keys"]

# A whole token directly before a "+": "Ctrl+Z", "Ctrl+Alt+2"; not "Control panel".
_TOKEN = re.compile(r"\b(Ctrl|Alt)(?=\+)")
_MACOS = {"Ctrl": "Cmd", "Alt": "Option"}


def platform_keys(text: str, platform: str | None = None) -> str:
    """``text`` with its key names for ``platform`` (default: this one).

    On macOS (``darwin``) ``Ctrl`` becomes ``Cmd`` and ``Alt`` becomes ``Option``; anywhere
    else, and for words that only start with those letters, ``text`` is returned as it is.
    """
    if not (sys.platform if platform is None else platform).startswith("darwin"):
        return text
    return _TOKEN.sub(lambda match: _MACOS[match.group(1)], text)
