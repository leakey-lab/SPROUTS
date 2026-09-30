"""
Tests for the GT class registry (``app/gt/registry.py``).

Covers the default registry, the ``to_dict``/``from_dict`` round trip, every
``RegistryError`` validation rule, id/key/colour mutation (``add``/``update``/
``retire``), ``next_id`` id reuse avoidance, ``slugify_key``, and the
``load_registry``/``save_registry`` file round trip.
"""

from __future__ import annotations

import re

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
    # No temp file, whatever its name.
    assert [p.name for p in (tmp_path / "gt").iterdir()] == ["classes.json"]


def test_load_registry_invalid_json_raises_registry_error(tmp_path):
    gt_dir = tmp_path / "gt"
    gt_dir.mkdir()
    (gt_dir / "classes.json").write_text("{not valid json", encoding="utf-8")
    with pytest.raises(RegistryError):
        load_registry(str(tmp_path))


# --- "background" is reserved: the dataset export writes id 0 as "background" ---
def test_background_key_is_reserved():
    data = _default_dict()
    data["classes"][0]["key"] = "background"
    with pytest.raises(RegistryError):
        Registry.from_dict(data)
    with pytest.raises(RegistryError):
        default_registry().add(key="background", name="Background", color="#010203")


@pytest.mark.parametrize("name", ["Background", "background", " BACKGROUND "])
def test_slugify_key_never_produces_a_reserved_key(name):
    slug = slugify_key(name)
    assert slug == "background_class"
    # The slug is usable as a key as-is.
    reg = default_registry().add(key=slug, name=name.strip(), color="#010203")
    assert reg.by_key("background_class").id == 4


# --- wrong-shaped JSON is a RegistryError, never AttributeError/TypeError ---
@pytest.mark.parametrize(
    "data",
    [
        pytest.param([], id="top-level-list"),
        pytest.param("registry", id="top-level-str"),
        pytest.param(None, id="top-level-null"),
        pytest.param(7, id="top-level-int"),
        pytest.param({"version": 1, "classes": {"root": 1}}, id="classes-dict"),
        pytest.param({"version": 1, "classes": "root"}, id="classes-str"),
        pytest.param({"version": 1, "classes": 5}, id="classes-int"),
        pytest.param({"version": 1, "classes": [1]}, id="entry-int"),
        pytest.param({"version": 1, "classes": ["root"]}, id="entry-str"),
        pytest.param({"version": 1, "classes": [["root"]]}, id="entry-list"),
    ],
)
def test_from_dict_wrong_shape_raises_registry_error(data):
    with pytest.raises(RegistryError):
        Registry.from_dict(data)


@pytest.mark.parametrize("text", ["[]", '"x"', "null", '{"version": 1, "classes": {}}'])
def test_load_registry_wrong_shape_raises_registry_error(tmp_path, text):
    (tmp_path / "gt").mkdir()
    (tmp_path / "gt" / "classes.json").write_text(text, encoding="utf-8")
    with pytest.raises(RegistryError):
        load_registry(str(tmp_path))


def test_load_registry_non_utf8_raises_registry_error(tmp_path):
    (tmp_path / "gt").mkdir()
    (tmp_path / "gt" / "classes.json").write_bytes(b"\xff\xfe\x00\x81 not utf-8")
    with pytest.raises(RegistryError):
        load_registry(str(tmp_path))


# --- strict field types (RB-09): a hand-edited classes.json is never coerced ---
_IGNORE_ENTRY = (
    '{"id": 255, "key": "ignore", "name": "Ignore", "color": "#8a8f98", "retired": false}'
)


def _registry_text(version: str = "1", **raw_fields: str) -> str:
    """classes.json text for Root + Ignore, with raw JSON values replacing Root's fields."""
    fields = {"id": "1", "key": '"root"', "name": '"Root"', "color": '"#5fd6a0"'}
    fields.update(raw_fields)
    root = ", ".join(f'"{name}": {value}' for name, value in fields.items())
    return f'{{"version": {version}, "classes": [{{{root}}}, {_IGNORE_ENTRY}]}}'


def _load_text(tmp_path, text: str):
    (tmp_path / "gt").mkdir()
    (tmp_path / "gt" / "classes.json").write_text(text, encoding="utf-8")
    return load_registry(str(tmp_path))


@pytest.mark.parametrize(
    "field,raw",
    [
        ("id", "1e999"),  # json -> float('inf'): int() raised OverflowError
        ("id", "Infinity"),
        ("id", "-Infinity"),
        ("id", "NaN"),
        ("id", "1.9"),  # was silently truncated to 1
        ("id", "true"),  # was accepted as id 1
        ("id", '"1"'),
        ("id", "null"),
        ("key", "null"),
        ("key", "7"),
        ("name", "null"),  # was accepted as the name 'None'
        ("name", "7"),
        ("color", "null"),
        ("color", "16777215"),
        ("retired", '"false"'),  # was accepted as retired=True
        ("retired", "0"),
        ("retired", "1"),
        ("retired", "null"),
    ],
)
def test_load_registry_rejects_wrong_field_type_naming_the_field(tmp_path, field, raw):
    with pytest.raises(RegistryError, match=re.escape(f"classes[0].{field}")):
        _load_text(tmp_path, _registry_text(**{field: raw}))


def test_load_registry_names_the_missing_field_and_its_class(tmp_path):
    text = _registry_text().replace('"id": 1, ', "")
    with pytest.raises(RegistryError, match=r"classes\[0\].*'id'"):
        _load_text(tmp_path, text)


@pytest.mark.parametrize("version", ["true", '"1"', "1.5", "null"])
def test_load_registry_rejects_a_version_that_is_not_the_integer_1(tmp_path, version):
    with pytest.raises(RegistryError, match="version"):
        _load_text(tmp_path, _registry_text(version=version))


def test_load_registry_accepts_integral_float_id_as_int(tmp_path):
    reg = _load_text(tmp_path, _registry_text(id="1.0"))
    root = reg.by_key("root")
    assert root.id == 1 and type(root.id) is int


def test_load_registry_defaults_missing_retired_to_false(tmp_path):
    reg = _load_text(tmp_path, _registry_text())  # Root has no "retired" field
    assert reg.by_key("root").retired is False


def test_load_registry_keeps_an_explicit_retired_true(tmp_path):
    reg = _load_text(tmp_path, _registry_text(retired="true"))
    assert reg.by_key("root").retired is True
    assert "root" not in [c.key for c in reg.active()]


def test_save_registry_failure_leaves_no_tmp_file(tmp_path, monkeypatch):
    import os

    def _boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", _boom)
    with pytest.raises(OSError, match="disk full"):
        save_registry(str(tmp_path), default_registry())
    assert os.listdir(tmp_path / "gt") == []
