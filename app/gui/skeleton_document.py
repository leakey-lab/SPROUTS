"""Which skeleton file the Correct stage opens and saves (Qt-free).

:class:`SkeletonFiles`: for an image it names the GT skeleton
(``gt/skeletons/<stem>.png``, the only skeleton file the editor writes) and the model's
prediction (``skeletons/<stem>_fake.png`` by default, read-only), via
:class:`app.project_layout.ProjectLayout`.
:meth:`SkeletonFiles.start_file` picks the file to open: the GT skeleton when one
exists, else the prediction. ``app.gui.correct.skeleton_document`` builds the
skeleton layer's document on it; the unsaved-changes prompt is
:mod:`app.gui.editor_prompts`.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass

from app.project_layout import ProjectLayout

GT = "gt"
PREDICTION = "prediction"

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
    def gt_path(self) -> str:
        """``gt/skeletons/<stem>.png``: where a saved skeleton edit goes."""
        return self.layout.gt_skeleton_path(self.stem)

    def generated_path(self) -> str | None:
        """The generated skeleton if it exists (injected resolver first, then skeletons/)."""
        if self.generated_resolver is not None:
            path = self.generated_resolver(self.image_path)
            if path and os.path.isfile(path):
                return path
        return self.layout.find_skeleton(self.stem)

    def start_file(self) -> tuple[str | None, str | None]:
        """``(path, GT | PREDICTION)`` to open, or ``(None, None)`` if neither exists."""
        if os.path.isfile(self.gt_path):
            return self.gt_path, GT
        generated = self.generated_path()
        if generated:
            return generated, PREDICTION
        return None, None

    def dialog_start(self) -> str:
        """Where a Load dialog starts: the generated file if any, else the skeletons folder."""
        return self.generated_path() or self.layout.skeletons_dir
