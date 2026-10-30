"""LibraryIndex: the library tree's builder, lookup, navigation, filter and status (PG-05..08).

Offscreen and file-free: names are synthetic, so the 46k perf smoke runs on CI too.
"""

import os
import time

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QApplication,
    QLabel,
    QLineEdit,
    QStyleOptionViewItem,
    QTreeWidget,
    QTreeWidgetItem,
)

from app.gui.library_index import (
    LibraryIndex,
    LibrarySearchController,
    build_hierarchy,
    count_label,
    leaf_label,
)

USER = Qt.ItemDataRole.UserRole


def _name(tube, depth, date="2024.07.18", hhmmss="111530", field="24EFCAM1"):
    return f"{field}_T{tube:03d}_L{depth:03d}_{date}_{hhmmss}_001_SMM"


def synth_names(n_tubes=3, dates=("2024.07.18", "2024.08.07"), depths=(11, 12, 13, 14, 15)):
    names = []
    for t in range(1, n_tubes + 1):
        for d in dates:
            for k, depth in enumerate(depths):
                names.append(_name(t, depth, d, f"{9 + k // 60:02d}{k % 60:02d}{(7 * k) % 60:02d}"))
    return names


# --------------------------------------------------------------------- Qt-free parts
def test_build_hierarchy_groups_by_field_tube_date_and_sorts_by_depth():
    names = [_name(2, 45), _name(2, 11), _name(10, 3), "loose_image"]
    h = build_hierarchy(names)
    assert h["24EF"]["T2"]["2024.07.18"] == [(11, _name(2, 11)), (45, _name(2, 45))]
    assert h["24EF"]["T10"]["2024.07.18"] == [(3, _name(10, 3))]
    assert h["loose"]["No Tube"]["No Date"] == [(float("inf"), "loose_image")]


def test_entries_that_tie_on_depth_are_ordered_by_name_in_any_input_order():
    """Same field / tube / date and no depth (or the same depth): scan order never shows (WX-T9)."""
    names = [
        "loose_b",
        "loose_a",
        "loose_c",
        _name(2, 11, hhmmss="111530"),
        _name(2, 11, "2024.07.18", "090000"),
    ]
    expected = build_hierarchy(sorted(names))
    assert build_hierarchy(names) == expected
    assert build_hierarchy(reversed(names)) == expected
    assert [n for _, n in expected["loose"]["No Tube"]["No Date"]] == [
        "loose_a",
        "loose_b",
        "loose_c",
    ]


def test_leaf_label_is_depth_and_time_with_stem_fallback():
    assert leaf_label(_name(1, 44, hhmmss="111530")) == "L44 · 11:15:30"
    assert leaf_label("24EF_T001_L007_nodate") == "L7 · 24EF_T001_L007_nodate"
    assert leaf_label("plain") == "plain"


def test_count_label_pluralises_and_groups_digits():
    assert count_label(1) == "1 image"
    assert count_label(2) == "2 images"
    assert count_label(46178) == "46,178 images"


# ------------------------------------------------------------------------ the index
def _built(app, names=None, **kw):
    tree = QTreeWidget()
    index = LibraryIndex(tree, **kw)
    index.build(build_hierarchy(names or synth_names()))
    return tree, index


def _leaf(index, prefix):
    return next(leaf for leaf in index.leaves() if leaf.data(0, USER).startswith(prefix))


def test_build_makes_plain_text_rows_with_stems_in_user_role(app):
    names = synth_names()
    tree, index = _built(app, names)
    leaves = index.leaves()
    assert [leaf.data(0, USER) for leaf in leaves] == sorted(
        names, key=lambda n: (int(n[10:13]), n[19:29], int(n[15:18]))
    )
    field = tree.topLevelItem(0)
    assert field.text(0) == "24EF" and field.isExpanded()
    tube = field.child(0)
    assert tube.text(0) == "T1"
    date = tube.child(0)
    assert date.text(0) == "2024.07.18 · 5 images"
    texts = [date.child(i).text(0) for i in range(date.childCount())]
    assert len(set(texts)) == len(texts)  # distinct, readable rows (UI-10)
    assert texts[0].startswith("L11 · ")
    assert not any(ch in tree.topLevelItem(0).text(0) for ch in "📁🧪📅🖼")
    assert not field.icon(0).isNull() and not date.icon(0).isNull()


def test_item_for_and_step_skip_hidden_leaves(app):
    tree, index = _built(app)
    leaves = index.leaves()
    first, second, third = leaves[:3]
    assert index.item_for(second.data(0, USER)) is second
    assert index.item_for("missing") is None
    second.setHidden(True)
    assert index.step(first, 1) is third
    assert index.step(third, -1) is first
    assert index.step(first, -1) is None
    assert index.step(leaves[-1], 1) is None
    assert index.step(None, 1) is first
    assert index.leaves(visible_only=True)[1] is third


def test_index_resyncs_after_the_tree_is_rebuilt_by_hand(app):
    tree, index = _built(app)
    tree.clear()
    folder = QTreeWidgetItem(["Field"])
    for name in ("a", "b"):
        leaf = QTreeWidgetItem([name])
        leaf.setData(0, USER, name)
        folder.addChild(leaf)
    tree.addTopLevelItem(folder)
    assert [leaf.data(0, USER) for leaf in index.leaves()] == ["a", "b"]
    assert index.step(index.item_for("a"), 1) is index.item_for("b")


@pytest.mark.parametrize("query", ["", "t002", "2024.08.07", "zzz"])
def test_a_build_holds_what_a_walk_of_the_tree_reads(app, query):
    """build() keeps the names and hidden states of the rows it made (no read back through
    Qt, S06-07): they equal what a walk of the tree reads, also re-opened under a search."""
    names = [*synth_names(), "loose_b", "loose_a"]
    tree, index = _built(app, names)
    index.apply_filter(query)
    index.build(build_hierarchy(names))
    built = (list(index._leaves), dict(index._pos), list(index._leaf_hidden))
    built_groups, built_keys = list(index._group_hidden), list(index._keys)

    index._dirty = True
    index._ensure()  # re-reads every row through Qt

    assert all(a is b for a, b in zip(built[0], index._leaves, strict=True))
    assert built[1:] == (index._pos, index._leaf_hidden)
    assert (built_groups, built_keys) == (index._group_hidden, index._keys)
    assert built[2] == [leaf.isHidden() for leaf in index.leaves()]
    assert any(built[2]) == bool(query) and any(built_groups) == bool(query)


def test_filter_matches_the_stem_not_only_the_label(app):
    tree, index = _built(app)
    assert index.apply_filter("t002_l013_2024.08") == 1
    visible = index.leaves(visible_only=True)
    assert [leaf.data(0, USER)[:29] for leaf in visible] == ["24EFCAM1_T002_L013_2024.08.07"]
    leaf = visible[0]
    parent = leaf.parent()
    while parent is not None:  # the match is revealed, not hidden in a collapsed group
        assert parent.isExpanded() and not parent.isHidden()
        parent = parent.parent()


def test_filter_group_label_match_keeps_its_images(app):
    tree, index = _built(app)
    assert index.apply_filter("T2") == 10
    tube = _leaf(index, "24EFCAM1_T002_L011").parent().parent()
    assert tube.text(0) == "T2" and not tube.isHidden()
    assert all(not tube.child(i).isHidden() for i in range(tube.childCount()))
    assert _leaf(index, "24EFCAM1_T001_L011").isHidden()


def test_filter_no_match_and_clear(app):
    tree, index = _built(app)
    assert index.apply_filter("zzz") == 0
    assert all(leaf.isHidden() for leaf in index.leaves())
    assert tree.topLevelItem(0).isHidden()
    assert index.apply_filter("") == len(index.leaves())
    assert not any(leaf.isHidden() for leaf in index.leaves())
    assert not tree.topLevelItem(0).isHidden()


def test_tooltip_names_the_full_stem_path_and_status(app):
    name = _name(1, 11)
    tree, index = _built(
        app,
        [name],
        path_of=lambda n: f"D:/p/{n}.jpg",
        mask_state=lambda n: "prediction",
        skeleton_state=lambda n: "none",
    )
    text = index.tooltip_for(name)
    assert name in text and f"D:/p/{name}.jpg" in text and "Mask: prediction" in text


def test_status_is_drawn_by_the_delegate_from_the_providers(app):
    masked = set()
    name = _name(1, 11)
    tree, index = _built(app, [name], mask_state=lambda n: "prediction" if n in masked else "none")
    delegate = tree.itemDelegate()
    model_index = tree.indexFromItem(index.item_for(name))

    def icon_key():
        option = QStyleOptionViewItem()
        delegate.initStyleOption(option, model_index)
        assert not option.icon.isNull()
        return option.icon.cacheKey()

    before = icon_key()
    masked.add(name)
    index.refresh_status(name)
    assert icon_key() != before
    # group rows keep their own icon
    option = QStyleOptionViewItem()
    delegate.initStyleOption(option, tree.indexFromItem(tree.topLevelItem(0)))
    assert option.icon.cacheKey() == tree.topLevelItem(0).icon(0).cacheKey()


def test_status_icon_renders_under_the_app_stylesheet(app):
    """ForegroundRole is ignored under the app QSS (UI-09); the status badge is not."""
    from PyQt6.QtGui import QColor

    from app.gui.widgets import tokens

    qss_path = os.path.join(
        os.path.dirname(__file__), "..", "resources", "themes", "dark_theme.qss"
    )
    with open(qss_path, encoding="utf-8") as fh:
        qss = fh.read()
    name = _name(1, 11)
    tree, index = _built(app, [name], mask_state=lambda n: "prediction")
    tree.setStyleSheet(qss)
    tree.resize(320, 200)
    tree.expandAll()
    tree.show()
    QApplication.processEvents()
    img = tree.viewport().grab().toImage()
    rect = tree.visualItemRect(index.item_for(name))
    target = QColor(tokens.MASK)
    hits = 0
    for x in range(rect.left(), min(rect.right(), rect.left() + 120)):
        for y in range(rect.top(), rect.bottom()):
            c = img.pixelColor(x, y)
            if (
                abs(c.red() - target.red()) < 40
                and abs(c.green() - target.green()) < 40
                and abs(c.blue() - target.blue()) < 40
            ):
                hits += 1
    tree.close()
    assert hits > 5


def test_perf_smoke_46k(app):
    dates = ("2024.07.18", "2024.08.07", "2024.08.26")
    names = synth_names(n_tubes=170, dates=dates, depths=range(11, 102))
    assert len(names) == 46410
    hierarchy = build_hierarchy(names)
    tree = QTreeWidget()
    index = LibraryIndex(tree)
    t = time.perf_counter()
    index.build(hierarchy)
    build_ms = (time.perf_counter() - t) * 1000
    t = time.perf_counter()
    for i in range(1, 9):
        index.apply_filter("t001_l01"[:i])
    filter_ms = (time.perf_counter() - t) * 1000 / 8
    leaves = index.leaves()
    steps = []
    for k in range(9):
        t = time.perf_counter()
        index.step(leaves[len(leaves) // 2 + k], 1)
        steps.append(time.perf_counter() - t)
    step_ms = sorted(steps)[4] * 1000
    print(f"build {build_ms:.0f} ms, filter {filter_ms:.1f} ms/key, step {step_ms:.3f} ms")
    assert build_ms < 1500 and filter_ms < 100 and step_ms < 5


# ------------------------------------------------------------------ search debounce
def test_search_controller_debounces_typing_and_clears_immediately(app):
    edit, label = QLineEdit(), QLabel()
    applied = []
    LibrarySearchController(edit, lambda q: applied.append(q) or 3, label, delay_ms=60)
    for text in ("t", "t0", "t00", "t001", "t001_"):
        edit.setText(text)
    assert applied == []
    QTest.qWait(200)
    assert applied == ["t001_"]
    assert label.text() == "3 matches" and not label.isHidden()
    edit.setText("")
    assert applied == ["t001_", ""]
    assert label.isHidden()


def test_search_controller_reports_no_match(app):
    edit, label = QLineEdit(), QLabel()
    LibrarySearchController(edit, lambda q: 0, label, delay_ms=10)
    edit.setText("zzz")
    QTest.qWait(80)
    assert label.text() == "No images match “zzz”"
