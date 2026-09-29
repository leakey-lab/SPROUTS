"""Which skeleton file the Correct stage opens and saves (Qt-free).

:class:`SkeletonFiles`: for an image it names the saved correction
(``skeleton_corrections/<stem>_skeleton.png``, the only skeleton file the editor
writes) and the generated skeleton (``skeletons/<stem>_fake.png`` by default,
read-only), via :class:`app.project_layout.ProjectLayout`.
:meth:`SkeletonFiles.start_file` picks the file to open: the correction when one
exists, else the generated skeleton. ``app.gui.correct.skeleton_document`` builds the
skeleton layer's document on it; the unsaved-changes prompt is
:mod:`app.gui.editor_prompts`.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass

from app.project_layout import ProjectLayout

CORRECTED = "corrected"
GENERATED = "generated"

GeneratedResolver = Callable[[str], "str | None"]


@dataclass(frozen=True)
class SkeletonFiles:
    """Skeleton file locations for one image of a project."""

    image_path: str
    project_root: str
    generated_resolver: GeneratedResolver | None = None

    @property
    def layout(self) -> ProjectLayout:
        return ProjectLayout(self.project_root)

    @property
    def stem(self) -> str:
        return ProjectLayout.stem(self.image_path)

    @property
    def correction_path(self) -> str:
        return self.layout.corrected_skeleton_path(self.stem)

    def generated_path(self) -> str | None:
        """The generated skeleton if it exists (injected resolver first, then skeletons/)."""
        if self.generated_resolver is not None:
            path = self.generated_resolver(self.image_path)
            if path and os.path.isfile(path):
                return path
        return self.layout.find_skeleton(self.stem)

    def start_file(self) -> tuple[str | None, str | None]:
        """``(path, CORRECTED | GENERATED)`` to open, or ``(None, None)`` if neither exists."""
        if os.path.isfile(self.correction_path):
            return self.correction_path, CORRECTED
        generated = self.generated_path()
        if generated:
            return generated, GENERATED
        return None, None

    def dialog_start(self) -> str:
        """Where a Load dialog starts: the generated file if any, else the skeletons folder."""
        return self.generated_path() or self.layout.skeletons_dir
