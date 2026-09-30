"""The image library tree: build it fast, find, step, filter, and show per-image status.

The library is a ``QTreeWidget`` of Field -> Tube -> Date -> image rows. At 46k images
every whole-tree walk costs 60-250 ms on the GUI thread, so :class:`LibraryIndex` keeps,
per window, what those walks used to recompute:

- leaves in display order, ``name -> position`` (O(1) lookup and next/previous step);
- lower-case match keys and group spans, so a search touches only rows whose
  visibility changes;
- no per-row status colour: :class:`LibraryItemDelegate` draws a small mask / skeleton
  badge from the injected status callables at paint time (only visible rows), so a
  status change is one repaint and the tree never needs a recolour pass. Row colours
  set with ``setForeground`` are ignored under the app stylesheet; decoration icons
  are not.

The index stays correct when other code rebuilds the tree by hand (tests do): any row
insert/remove marks it stale and the next query re-reads the tree once.

The pure helpers (:func:`build_hierarchy`, :func:`leaf_label`, :func:`count_label`,
:func:`natural_sort_key`, and :func:`label_forms` / :func:`stem_forms` / :func:`fit_label`,
the shorter forms a narrow panel shows) are Qt-free.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from itertools import accumulate

from PyQt6.QtCore import QEvent, QObject, QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QLabel,
    QLineEdit,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolTip,
    QTreeWidget,
    QTreeWidgetItem,
)

from app.gui.widgets import tokens
from app.naming import image_stem, parse_image_name

USER_ROLE = Qt.ItemDataRole.UserRole
NO_DEPTH = float("inf")

# field -> tube -> date -> [(depth, image name), ...] sorted by depth
Hierarchy = dict[str, dict[str, dict[str, list[tuple[float, str]]]]]

#: Search results up to this size expand their groups so every match is on screen.
EXPAND_MATCHES_LIMIT = 300

_DIGITS = re.compile(r"(\d+)")
_CLOCK_TAIL = re.compile(r"(\d{2}:\d{2}):\d{2}$")  # "11:15:30" -> "11:15"
_COUNT_TAIL = re.compile(r"([\d,]+) images?$")  # "128 images" -> "128"
_FULL_DATE = re.compile(r"\d{4}\.\d{2}\.\d{2}")  # a date row's head


# ---------------------------------------------------------------- Qt-free helpers
def natural_sort_key(text: str) -> tuple[bool, float, str]:
    """Sort key that orders numbers naturally (T1, T2, T10) and "No Tube" last."""
    if text == "No Tube":
        return (True, float("inf"), text)
    match = _DIGITS.search(text)
    if match:
        return (False, int(match.group(1)), text)
    return (False, 0, text)


def index_names(names: Iterable[str]) -> tuple[Hierarchy, dict[str, str]]:
    """Parse every name once: the :func:`build_hierarchy` tree and each :func:`leaf_label`.

    Run it off the GUI thread (the folder loader does): ~0.2 s at 46k names.
    """
    hierarchy: Hierarchy = {}
    labels: dict[str, str] = {}
    for name in names:
        info = parse_image_name(name)
        labels[name] = _label(name, info)
        tube = f"T{info.tube}" if info.tube is not None else "No Tube"
        depth = info.position if info.position is not None else NO_DEPTH
        dates = hierarchy.setdefault(info.field or "Unknown", {}).setdefault(tube, {})
        dates.setdefault(info.date or "No Date", []).append((depth, name))
    for tubes in hierarchy.values():
        for dates in tubes.values():
            for images in dates.values():
                images.sort(key=lambda entry: entry[0])
    return hierarchy, labels


def build_hierarchy(names: Iterable[str]) -> Hierarchy:
    """Group image stems by field, tube and date; each date's images sorted by depth.

    Unparseable parts fall back to "Unknown" / "No Tube" / "No Date"; images without a
    depth sort last. Uses :func:`app.naming.parse_image_name` (the one name parser).
    """
    return index_names(names)[0]


def leaf_label(name: str) -> str:
    """Short row text for an image: ``"L44 · 11:15:30"`` (the date is the parent row).

    Falls back to ``"L44 · <stem>"`` without a time and to the stem without a depth.
    """
    return _label(name, parse_image_name(name))


def _label(name: str, info) -> str:
    if info.position is None:
        return name
    return f"L{info.position} · {info.time or name}"


def count_label(count: int) -> str:
    """``"1 image"`` / ``"46,178 images"``."""
    return f"{count:,} image{'' if count == 1 else 's'}"


def label_forms(text: str) -> list[str]:
    """``text`` of a library row, then shorter forms for a narrow panel (most complete first).

    ``"L44 · 11:15:30"`` -> ``"L44 · 11:15"`` -> ``"L44"``; ``"2024.07.18 · 128 images"``
    -> ``"2024.07.18 · 128"`` -> ``"2024.07.18"`` -> ``"07.18"``; ``"L7 · <stem>"`` ->
    ``"L7"``. The head (depth or date) is what stays; a text without a ``" · "`` tail has
    no shorter form.
    """
    head, sep, tail = text.partition(" · ")
    if not sep or not head:
        return [text]
    forms = [text]
    time = _CLOCK_TAIL.match(tail)
    count = _COUNT_TAIL.match(tail)
    if time:
        forms.append(f"{head} · {time.group(1)}")
    elif count:
        forms.append(f"{head} · {count.group(1)}")
    forms.append(head)
    if _FULL_DATE.fullmatch(head):
        forms.append(head[5:])  # month.day: a trial rarely spans years
    return forms


def stem_forms(stem: str) -> list[str]:
    """``stem``, then shorter forms that keep what identifies the capture (most complete
    first): ``"T1 · L44 · 2024.07.18 · 11:15:30"``, ``"T1 · L44 · 2024.07.18"``,
    ``"T1 · L44"``. A name without a tube or depth has no shorter form."""
    info = parse_image_name(stem)
    if info.tube is None or info.position is None:
        return [stem]
    core = f"T{info.tube} · L{info.position}"
    forms = [stem]
    if info.date and info.time:
        forms.append(f"{core} · {info.date} · {info.time}")
    if info.date:
        forms.append(f"{core} · {info.date}")
    forms.append(core)
    return forms


def fit_label(forms: list[str], fits: Callable[[str], bool]) -> str:
    """The first of ``forms`` that ``fits``, else the last (shortest) one."""
    return next((form for form in forms if fits(form)), forms[-1])


def match_count_label(count: int, query: str) -> str:
    """Search feedback under the field: ``"3 matches"`` or ``No images match “q”``."""
    if count == 0:
        return f"No images match “{query}”"
    return f"{count:,} match{'' if count == 1 else 'es'}"


# -------------------------------------------------------------------- status badge
StatusOf = Callable[[str], tuple[bool, bool]]

_BADGE = QSize(22, 12)


def _badge_pixmap(has_mask: bool, has_skeleton: bool, dpr: float = 2.0) -> QPixmap:
    """Two dots: mask (left) and skeleton (right). Filled = present, ring = absent.

    Shape carries the state as well as colour, so it reads without colour vision.
    """
    pixmap = QPixmap(round(_BADGE.width() * dpr), round(_BADGE.height() * dpr))
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    for x, present, colour in ((1.5, has_mask, tokens.MASK), (11.5, has_skeleton, tokens.SKEL)):
        rect = QRectF(x, 1.5, 9, 9)
        if present:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(colour))
            painter.drawEllipse(rect)
        else:
            painter.setPen(QPen(QColor(tokens.TEXT_FAINT), 1.2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(rect.adjusted(1, 1, -1, -1))
    painter.end()
    return pixmap


def status_text(has_mask: bool, has_skeleton: bool) -> str:
    """Words for the badge (tooltip / accessibility)."""
    mask = "Mask saved" if has_mask else "No mask"
    skeleton = "skeleton ready" if has_skeleton else "no skeleton"
    return f"{mask} · {skeleton}"


class LibraryItemDelegate(QStyledItemDelegate):
    """Draws each image row's status badge and serves its tooltip on hover.

    Both are computed for the rows Qt actually paints / hovers, from ``status_of`` and
    ``tooltip_of``, so building the tree sets neither per row. A row too narrow for its
    text is painted in the longest :func:`label_forms` form that fits (its item text is
    unchanged); a shortened group row shows its full text as tooltip.
    """

    def __init__(self, status_of: StatusOf, tooltip_of: Callable[[str], str], parent=None):
        super().__init__(parent)
        self._status_of = status_of
        self._tooltip_of = tooltip_of
        self._icons: dict[tuple[bool, bool], QIcon] = {}

    def status_icon(self, has_mask: bool, has_skeleton: bool) -> QIcon:
        key = (has_mask, has_skeleton)
        icon = self._icons.get(key)
        if icon is None:
            icon = self._icons[key] = QIcon(_badge_pixmap(has_mask, has_skeleton))
        return icon

    def initStyleOption(self, option: QStyleOptionViewItem, index) -> None:  # noqa: N802
        super().initStyleOption(option, index)
        name = index.data(USER_ROLE)
        if name:
            option.icon = self.status_icon(*self._status_of(name))
            option.decorationSize = _BADGE
            option.features |= QStyleOptionViewItem.ViewItemFeature.HasDecoration
        self._fit_text(option)

    @staticmethod
    def _fit_text(option: QStyleOptionViewItem) -> None:
        """Paint the longest form of the row text that fits its text rect (Qt elides the
        shortest one if even that does not fit)."""
        text = option.text
        if not text or option.rect.width() <= 0 or " · " not in text:
            return
        widget = option.widget
        style = widget.style() if widget is not None else QApplication.style()
        room = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, option, widget).width()
        metrics = option.fontMetrics
        option.text = fit_label(label_forms(text), lambda t: metrics.horizontalAdvance(t) <= room)

    def helpEvent(self, event, view, option, index) -> bool:  # noqa: N802
        if not index.isValid() or event.type() != QEvent.Type.ToolTip:
            return super().helpEvent(event, view, option, index)
        name = index.data(USER_ROLE)
        if name:
            QToolTip.showText(event.globalPos(), self._tooltip_of(name), view)
            return True
        full = index.data(Qt.ItemDataRole.DisplayRole)
        shown = QStyleOptionViewItem(option)
        self.initStyleOption(shown, index)
        if full and shown.text != full:  # a shortened group row: say what it holds
            QToolTip.showText(event.globalPos(), full, view)
            return True
        return super().helpEvent(event, view, option, index)


# ---------------------------------------------------------------------- the index
@dataclass(slots=True)
class _Group:
    item: QTreeWidgetItem
    key: str  # lower-case label, without the image count
    start: int  # leaves[start:end] are this group's images
    end: int
    parent: int  # position of the parent group (groups are in pre-order), -1 at the top


class LibraryIndex:
    """Per-window index of the library ``QTreeWidget`` (see the module docstring).

    Args:
        tree: the library tree (``main_window.file_list``).
        path_of: image name -> path, for the hover tooltip.
        has_mask / has_skeleton: image name -> status; read when a row is painted.
    """

    def __init__(
        self,
        tree: QTreeWidget,
        *,
        path_of: Callable[[str], str | None] | None = None,
        has_mask: Callable[[str], bool] | None = None,
        has_skeleton: Callable[[str], bool] | None = None,
    ) -> None:
        self._tree = tree
        self._path_of = path_of or (lambda _name: None)
        self._has_mask = has_mask or (lambda _name: False)
        self._has_skeleton = has_skeleton or (lambda _name: False)
        self._delegate = LibraryItemDelegate(self.status_of, self.tooltip_for, tree)
        tree.setItemDelegate(self._delegate)

        self._leaves: list[QTreeWidgetItem] = []
        self._pos: dict[str, int] = {}
        self._keys: list[str] = []
        self._groups: list[_Group] = []
        self._leaf_hidden: list[bool] = []
        self._group_hidden: list[bool] = []
        self._query = ""
        self._dirty = True
        self._building = False
        self._group_icons: dict[int, QIcon] = {}

        model = tree.model()
        for signal in (
            model.rowsInserted,
            model.rowsRemoved,
            model.rowsMoved,
            model.modelReset,
            model.layoutChanged,
        ):
            signal.connect(self._mark_dirty)

    # ------------------------------------------------------------------ building
    def build(self, hierarchy: Hierarchy, labels: dict[str, str] | None = None) -> None:
        """Replace the tree's rows with ``hierarchy`` (detached items, one insert).

        ``labels`` (from :func:`index_names`) saves re-parsing each name for its row
        text. The first level is expanded and the current search is re-applied.
        """
        tree = self._tree
        labels = labels or {}
        bold = QFont(tree.font())
        bold.setBold(True)
        leaves: list[QTreeWidgetItem] = []
        keys: list[str] = []
        groups: list[_Group] = []
        top: list[QTreeWidgetItem] = []

        def group(label: str, key: str, depth: int, parent: int) -> tuple[QTreeWidgetItem, int]:
            item = QTreeWidgetItem([label])
            item.setIcon(0, self._group_icon(depth))
            if depth < 2:  # field and tube rows are bold
                item.setFont(0, bold)
            groups.append(_Group(item, key.lower(), len(leaves), 0, parent))
            return item, len(groups) - 1

        for field in sorted(hierarchy):
            field_item, field_at = group(field, field, 0, -1)
            tubes = hierarchy[field]
            for tube in sorted(tubes, key=natural_sort_key):
                tube_item, tube_at = group(tube, tube, 1, field_at)
                dates = tubes[tube]
                for date in sorted(dates, key=lambda d: (d == "No Date", d)):
                    images = dates[date]
                    date_item, date_at = group(
                        f"{date} · {count_label(len(images))}", date, 2, tube_at
                    )
                    children = []
                    for _depth, name in images:
                        label = labels.get(name) or leaf_label(name)
                        leaf = QTreeWidgetItem([label])
                        leaf.setData(0, USER_ROLE, name)
                        children.append(leaf)
                        leaves.append(leaf)
                        keys.append(f"{label}\n{name}".lower())
                    date_item.addChildren(children)
                    groups[date_at].end = len(leaves)
                    tube_item.addChild(date_item)
                groups[tube_at].end = len(leaves)
                field_item.addChild(tube_item)
            groups[field_at].end = len(leaves)
            top.append(field_item)

        self._building = True
        tree.setUpdatesEnabled(False)
        try:
            tree.clear()
            tree.insertTopLevelItems(0, top)
            tree.expandToDepth(0)
        finally:
            self._building = False
            tree.setUpdatesEnabled(True)
        self._adopt(leaves, keys, groups)
        if self._query:
            self.apply_filter(self._query)

    def _group_icon(self, depth: int) -> QIcon:
        icon = self._group_icons.get(depth)
        if icon is None:
            from app.gui.widgets.icons import load_icon

            name = ("folder", "tube", "date")[min(depth, 2)]
            icon = self._group_icons[depth] = load_icon(name, tokens.TEXT_MUTED, 14)
        return icon

    def _adopt(self, leaves, keys, groups) -> None:
        self._leaves = leaves
        self._keys = keys
        self._groups = groups
        self._pos = {leaf.data(0, USER_ROLE): i for i, leaf in enumerate(leaves)}
        self._leaf_hidden = [leaf.isHidden() for leaf in leaves]
        self._group_hidden = [g.item.isHidden() for g in groups]
        self._dirty = False

    def _mark_dirty(self, *_args) -> None:
        if not self._building:
            self._dirty = True

    def _ensure(self) -> None:
        """Re-read a tree that was changed behind the index's back."""
        if not self._dirty:
            return
        leaves: list[QTreeWidgetItem] = []
        keys: list[str] = []
        groups: list[_Group] = []

        def walk(item: QTreeWidgetItem, parent: int) -> None:
            name = item.data(0, USER_ROLE)
            if name and item.childCount() == 0:
                leaves.append(item)
                keys.append(f"{item.text(0)}\n{name}".lower())
                return
            record = _Group(item, item.text(0).lower(), len(leaves), 0, parent)
            position = len(groups)
            groups.append(record)
            for i in range(item.childCount()):
                walk(item.child(i), position)
            record.end = len(leaves)

        for i in range(self._tree.topLevelItemCount()):
            walk(self._tree.topLevelItem(i), -1)
        self._adopt(leaves, keys, groups)

    # ------------------------------------------------------------------- lookups
    def leaves(self, *, visible_only: bool = False) -> list[QTreeWidgetItem]:
        """Image rows in display order (optionally only those not hidden)."""
        self._ensure()
        if visible_only:
            return [leaf for leaf in self._leaves if not leaf.isHidden()]
        return list(self._leaves)

    def item_for(self, name: str | None) -> QTreeWidgetItem | None:
        """The row of image ``name`` (O(1)), or None."""
        self._ensure()
        i = self._pos.get(name) if name else None
        return None if i is None else self._leaves[i]

    def position(self, item: QTreeWidgetItem | None) -> int | None:
        """Display position of an image row, or None for a group row / foreign item."""
        if item is None:
            return None
        self._ensure()
        i = self._pos.get(item.data(0, USER_ROLE))
        return i if i is not None and self._leaves[i] is item else None

    def step(
        self, item: QTreeWidgetItem | None, delta: int, *, visible_only: bool = True
    ) -> QTreeWidgetItem | None:
        """The image row ``delta`` rows after ``item`` (hidden rows skipped), or None.

        From no / an unknown item, ``+1`` gives the first row.
        """
        if delta == 0:
            return item
        self._ensure()
        i = self.position(item)
        j = -1 if i is None else i
        direction = 1 if delta > 0 else -1
        remaining = abs(delta)
        filtered_out = self._leaf_hidden  # the filter's own state: skipped without Qt calls
        while remaining:
            j += direction
            if not 0 <= j < len(self._leaves):
                return None
            if not visible_only or not (filtered_out[j] or self._leaves[j].isHidden()):
                remaining -= 1
        return self._leaves[j]

    def neighbours(self, path: str, n: int = 1) -> list[str]:
        """Paths of the images PgDn / PgUp would open from ``path`` (an image path or
        stem), nearest first: next, previous, next but one, ... (``n`` each way).

        Hidden (filtered-out) rows are skipped like :meth:`step` skips them; an image
        that is not in the library has none. The Correct page and the Library display
        read these ahead.
        """
        i = self._visible_position(path)
        if i is None:
            return []
        after = self._visible_run(i, 1, n)
        before = self._visible_run(i, -1, n)
        order = [j for pair in zip(after, before, strict=False) for j in pair]
        order += after[len(before) :] + before[len(after) :]
        paths = (self._path_of(self._leaves[j].data(0, USER_ROLE)) for j in order)
        return [p for p in paths if p]

    def position_of(self, path: str) -> tuple[int, int] | None:
        """``(index, count)``: the 1-based place of ``path`` (an image path or stem) among
        the images the search shows, and how many it shows; None when it is not shown.

        Counted from the filter's own state (no per-row Qt call: 46k rows stay cheap).
        """
        i = self._visible_position(path)
        if i is None:
            return None
        hidden = self._leaf_hidden
        return hidden[:i].count(False) + 1, len(hidden) - hidden.count(True)

    def _visible_position(self, path: str) -> int | None:
        """Position of the visible leaf of ``path`` (image path or stem), else None."""
        if not path:
            return None
        self._ensure()
        i = self._pos.get(image_stem(path))
        if i is None or self._leaf_hidden[i] or self._leaves[i].isHidden():
            return None
        return i

    def _visible_run(self, i: int, direction: int, n: int) -> list[int]:
        """Up to ``n`` positions of visible leaves after (``direction`` 1) or before ``i``."""
        out: list[int] = []
        j = i + direction
        while 0 <= j < len(self._leaves) and len(out) < n:
            if not (self._leaf_hidden[j] or self._leaves[j].isHidden()):
                out.append(j)
            j += direction
        return out

    # -------------------------------------------------------------------- status
    def status_of(self, name: str) -> tuple[bool, bool]:
        """(has mask, has skeleton) of image ``name``, from the injected providers."""
        return bool(self._has_mask(name)), bool(self._has_skeleton(name))

    def tooltip_for(self, name: str) -> str:
        """Full stem, status in words, and the image path."""
        lines = [name, status_text(*self.status_of(name))]
        path = self._path_of(name)
        if path:
            lines.append(path)
        return "\n".join(lines)

    def refresh_status(self, name: str | None = None) -> None:
        """Repaint one row (or every visible row) after its status changed."""
        viewport = self._tree.viewport()
        item = self.item_for(name) if name else None
        if item is None:
            viewport.update()
        else:
            viewport.update(self._tree.visualItemRect(item))

    # -------------------------------------------------------------------- filter
    @property
    def query(self) -> str:
        return self._query

    def apply_filter(self, query: str) -> int:
        """Show the images whose label or stem contains ``query`` (case-insensitive).

        A group whose own label matches keeps all of its images; a group stays visible
        while it has a visible image. Small result sets are expanded and the first one
        scrolled to. Only rows whose visibility changes are touched. Returns the
        number of visible images.
        """
        self._ensure()
        self._query = query or ""
        needle = self._query.strip().lower()
        groups = self._groups
        if not needle:
            leaf_visible = [True] * len(self._leaves)
            group_visible = [True] * len(groups)
        else:
            leaf_visible = [needle in key for key in self._keys]
            label_match = [needle in g.key for g in groups]
            for g, matched in zip(groups, label_match, strict=True):
                if matched and g.end > g.start:
                    leaf_visible[g.start : g.end] = [True] * (g.end - g.start)
            seen = [0, *accumulate(leaf_visible)]
            forced = list(label_match)  # a matching label shows its whole subtree
            for i, g in enumerate(groups):  # pre-order: parents come first
                if g.parent >= 0 and forced[g.parent]:
                    forced[i] = True
            group_visible = [
                shown or seen[g.end] > seen[g.start]
                for g, shown in zip(groups, forced, strict=True)
            ]
            for i in range(len(groups) - 1, -1, -1):  # a visible row keeps its parents
                if group_visible[i] and groups[i].parent >= 0:
                    group_visible[groups[i].parent] = True

        tree = self._tree
        tree.setUpdatesEnabled(False)
        try:
            self._sync_hidden(self._leaves, self._leaf_hidden, leaf_visible)
            self._sync_hidden([g.item for g in groups], self._group_hidden, group_visible)
            count = sum(leaf_visible)
            if needle and 0 < count <= EXPAND_MATCHES_LIMIT:
                for g, visible in zip(groups, group_visible, strict=True):
                    if visible and not g.item.isExpanded():
                        g.item.setExpanded(True)
        finally:
            tree.setUpdatesEnabled(True)
        if needle and 0 < count <= EXPAND_MATCHES_LIMIT:
            first = next(i for i, visible in enumerate(leaf_visible) if visible)
            tree.scrollToItem(self._leaves[first])
        return count

    @staticmethod
    def _sync_hidden(items, hidden: list[bool], visible: list[bool]) -> None:
        for i, (item, show) in enumerate(zip(items, visible, strict=True)):
            if hidden[i] == show:  # state changes
                item.setHidden(not show)
                hidden[i] = not show


# ------------------------------------------------------------------ search field
class LibrarySearchController(QObject):
    """Debounces a search field into ``apply(query) -> match count`` and reports the count.

    Typing restarts a single-shot timer (``delay_ms``), so a burst of keystrokes runs one
    filter; clearing the field applies at once. ``status_label`` (optional) shows
    "N matches" / "No images match" and hides while the field is empty.
    """

    def __init__(
        self,
        line_edit: QLineEdit,
        apply: Callable[[str], int],
        status_label: QLabel | None = None,
        *,
        delay_ms: int = 150,
    ) -> None:
        super().__init__(line_edit)
        self._edit = line_edit
        self._apply = apply
        self._label = status_label
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(delay_ms)
        self._timer.timeout.connect(self.apply_now)
        line_edit.textChanged.connect(self._on_text_changed)
        if status_label is not None:
            status_label.setVisible(False)

    def _on_text_changed(self, text: str) -> None:
        if text.strip():
            self._timer.start()
        else:
            self._timer.stop()
            self.apply_now()

    def apply_now(self) -> int:
        """Filter with the field's current text immediately; returns the match count."""
        self._timer.stop()
        query = self._edit.text()
        count = self._apply(query)
        if self._label is not None:
            active = bool(query.strip())
            if active:
                self._label.setText(match_count_label(count, query.strip()))
            self._label.setVisible(active)
        return count
