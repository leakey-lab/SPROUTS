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
from dataclasses import dataclass, replace

from app.project_layout import ProjectLayout

GT = "gt"
PREDICTION = "prediction"

GeneratedResolver = Callable[[str], "str | None"]


@dataclass(frozen=True)
class SkeletonFiles:
    """Skeleton file locations for one image of a project.

    ``gt_file``: the GT path as :meth:`resolved` looked it up once (None: looked up on each
    access of :attr:`gt_path`; on a case-sensitive disk a miss lists ``gt/skeletons/``).
    ``generated_file``: the same for :meth:`generated_path` (``""``: none was found).
    """

    image_path: str
    project_root: str
    generated_resolver: GeneratedResolver | None = None
    gt_file: str | None = None
    generated_file: str | None = None

    @property
    def layout(self) -> ProjectLayout:
        return ProjectLayout(self.project_root)

    @property
    def gt_path(self) -> str:
        """``gt/skeletons/<stem>.png``: the image's GT skeleton, its stem compared without
        case (O7), as the mask's ``gt_mask_of``; while there is none, where a saved skeleton
        edit goes. Looked up from the image path: the stem is never stripped twice
        (``X.png.jpg`` -> ``X.png``, final review D1). ``gt_file`` when it is set."""
        if self.gt_file is not None:
            return self.gt_file
        layout = self.layout
        return layout.find_gt_skeleton(self.image_path) or layout.gt_skeleton_path(self.image_path)

    def resolved(self) -> SkeletonFiles:
        """These files with :attr:`gt_path` and :meth:`generated_path` looked up now, once
        (``gt_file``, ``generated_file``): what one open, plan or save asks several times."""
        return replace(self, gt_file=self.gt_path, generated_file=self.generated_path() or "")

    def generated_path(self) -> str | None:
        """The generated skeleton if it exists (injected resolver first, then skeletons/);
        ``generated_file`` when it is set."""
        if self.generated_file is not None:
            return self.generated_file or None
        if self.generated_resolver is not None:
            path = self.generated_resolver(self.image_path)
            if path and os.path.isfile(path):
                return path
        return self.layout.find_skeleton(self.image_path)

    def start_file(self) -> tuple[str | None, str | None]:
        """``(path, GT | PREDICTION)`` to open, or ``(None, None)`` if neither exists."""
        gt_path = self.gt_path
        if os.path.isfile(gt_path):
            return gt_path, GT
        generated = self.generated_path()
        if generated:
            return generated, PREDICTION
        return None, None

    def dialog_start(self) -> str:
        """Where a Load dialog starts: the generated file if any, else the skeletons folder."""
        return self.generated_path() or self.layout.skeletons_dir
