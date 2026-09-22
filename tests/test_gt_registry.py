"""
Tests for the GT class registry (``app/gt/registry.py``).

Covers the default registry, the ``to_dict``/``from_dict`` round trip, every
``RegistryError`` validation rule, id/key/colour mutation (``add``/``update``/
``retire``), ``next_id`` id reuse avoidance, ``slugify_key``, and the
``load_registry``/``save_registry`` file round trip.
"""

from __future__ import annotations

import pytest

from app.gt.registry import (
    Registry,
    RegistryError,
    default_registry,
    load_registry,
    save_registry,
    slugify_key,
)


def _default_dict() -> dict:
    return default_registry().to_dict()


def test_default_registry_keys_and_order():
    reg = default_registry()
    assert [c.key for c in reg.classes] == ["root", "nodule", "root_tip", "ignore"]
    labelled_keys = [c.key for c in reg.labelled()]
    assert "ignore" not in labelled_keys
    assert labelled_keys == ["root", "nodule", "root_tip"]
    active = reg.active()
    assert active[-1].key == "ignore"


def test_to_dict_from_dict_round_trip():
    reg = default_registry()
    data = reg.to_dict()
    reg2 = Registry.from_dict(data)
    assert reg2 == reg


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda d: d.update(version=2), id="version-2"),
        pytest.param(
            lambda d: d["classes"].__setitem__(0, {**d["classes"][0], "id": 2}), id="dup-id"
        ),
        pytest.param(
            lambda d: d["classes"].__setitem__(0, {**d["classes"][0], "key": "nodule"}),
            id="dup-key",
        ),
        pytest.param(
            lambda d: d["classes"].__setitem__(0, {**d["classes"][0], "color": "#FF7EB6"}),
            id="dup-color-case-insensitive",
        ),
        pytest.param(
            lambda d: d["classes"].__setitem__(0, {**d["classes"][0], "key": "Root"}), id="key-Root"
        ),
        pytest.param(
            lambda d: d["classes"].__setitem__(0, {**d["classes"][0], "key": "3root"}),
            id="key-starts-digit",
        ),
        pytest.param(
            lambda d: d["classes"].__setitem__(0, {**d["classes"][0], "name": ""}), id="empty-name"
        ),
        pytest.param(
            lambda d: d["classes"].__setitem__(0, {**d["classes"][0], "name": "x" * 41}),
            id="name-41-chars",
        ),
        pytest.param(
            lambda d: d["classes"].__setitem__(0, {**d["classes"][0], "color": "green"}),
            id="color-not-hex",
        ),
        pytest.param(
            lambda d: d["classes"].__setitem__(0, {**d["classes"][0], "id": 0}), id="normal-id-0"
        ),
        pytest.param(
            lambda d: d["classes"].__setitem__(0, {**d["classes"][0], "id": 255}),
            id="normal-id-255",
        ),
        pytest.param(
            lambda d: d["classes"].__setitem__(0, {**d["classes"][0], "id": 300}),
            id="normal-id-300",
        ),
        pytest.param(lambda d: d["classes"].pop(3), id="missing-ignore"),
        pytest.param(
            lambda d: d["classes"].__setitem__(3, {**d["classes"][3], "id": 7}), id="ignore-id-7"
        ),
        pytest.param(
            lambda d: d["classes"].__setitem__(3, {**d["classes"][3], "retired": True}),
            id="ignore-retired",
        ),
    ],
)
def test_from_dict_raises_registry_error(mutate):
    data = _default_dict()
    mutate(data)
    with pytest.raises(RegistryError):
        Registry.from_dict(data)


def test_from_dict_raises_for_duplicate_colour_including_retired():
    data = _default_dict()
    # Retire "root_tip" and give it the same colour as the active "root" class.
    data["classes"][2]["retired"] = True
    data["classes"][2]["color"] = data["classes"][0]["color"]
    with pytest.raises(RegistryError):
        Registry.from_dict(data)


def test_next_id_on_defaults_is_4():
    reg = default_registry()
    assert reg.next_id() == 4


def test_next_id_does_not_reuse_retired_ids():
    reg = default_registry()
    reg = reg.add(key="dead_root", name="Dead root", color="#9aa0a6")
    assert reg.by_key("dead_root").id == 4
    reg = reg.retire("dead_root")
    assert reg.next_id() == 5


def test_add_appends_before_ignore_with_next_id():
    reg = default_registry()
    reg2 = reg.add(key="dead_root", name="Dead root", color="#9aa0a6")
    keys = [c.key for c in reg2.classes]
    assert keys == ["root", "nodule", "root_tip", "dead_root", "ignore"]
    assert reg2.by_key("dead_root").id == 4


def test_update_changes_only_given_fields():
    reg = default_registry()
    reg2 = reg.update("nodule", name="Nod", color="#123456")
    updated = reg2.by_key("nodule")
    assert updated.name == "Nod"
    assert updated.color == "#123456"
    assert updated.id == 2
    assert updated.key == "nodule"
    assert updated.retired is False
    # Other classes are untouched.
    assert reg2.by_key("root") == reg.by_key("root")


def test_update_unknown_key_raises_key_error():
    reg = default_registry()
    with pytest.raises(KeyError):
        reg.update("does_not_exist", name="X")


def test_retire_ignore_raises_registry_error():
    reg = default_registry()
    with pytest.raises(RegistryError):
        reg.retire("ignore")


def test_retire_nodule_marks_retired_and_excludes_from_active():
    reg = default_registry()
    reg2 = reg.retire("nodule")
    assert reg2.by_key("nodule").retired is True
    assert "nodule" not in [c.key for c in reg2.active()]
    assert "nodule" not in [c.key for c in reg2.labelled()]
    # Still present in the full class list.
    assert "nodule" in [c.key for c in reg2.classes]


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Dead root (old)", "dead_root_old"),
        ("3 tips", "c_3_tips"),
    ],
)
def test_slugify_key(name, expected):
    assert slugify_key(name) == expected


def test_slugify_key_result_is_at_most_32_chars():
    slug = slugify_key("a" * 100)
    assert len(slug) <= 32


def test_load_registry_without_file_returns_defaults_and_creates_no_file(tmp_path):
    project_dir = str(tmp_path)
    reg = load_registry(project_dir)
    assert reg == default_registry()
    assert not (tmp_path / "gt" / "classes.json").exists()


def test_save_registry_then_load_registry_round_trips(tmp_path):
    project_dir = str(tmp_path)
    reg = default_registry().add(key="dead_root", name="Dead root", color="#9aa0a6")
    path = save_registry(project_dir, reg)
    assert path == str(tmp_path / "gt" / "classes.json")
    loaded = load_registry(project_dir)
    assert loaded == reg


def test_save_registry_leaves_no_tmp_file(tmp_path):
    project_dir = str(tmp_path)
    save_registry(project_dir, default_registry())
    assert not (tmp_path / "gt" / "classes.json.tmp").exists()
    assert (tmp_path / "gt" / "classes.json").exists()


def test_load_registry_invalid_json_raises_registry_error(tmp_path):
    gt_dir = tmp_path / "gt"
    gt_dir.mkdir()
    (gt_dir / "classes.json").write_text("{not valid json", encoding="utf-8")
    with pytest.raises(RegistryError):
        load_registry(str(tmp_path))
