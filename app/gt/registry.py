"""
Ground-truth class registry (``gt/classes.json``).

The registry is the project-level list of GT classes: their ids, keys,
display names and colours. It is deliberately independent of every ML model
in the app (see
``docs/superpowers/specs/2026-09-22-multiclass-labels-design.md`` §2) and has
no Qt dependency so it can be unit tested headlessly and reused by the
dataset exporter.

Ids are permanent once assigned (``next_id`` never reuses a retired id).
Colours are unique across the whole registry, retired classes included,
because the tracing editor maps a painted colour back to a class id by exact
match. The Ignore class (id 255) is always present, always last in display
order, and can never be retired.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from dataclasses import dataclass, replace

from app.config import DEFAULT_GT_CLASSES, GT_CLASSES_FILENAME, GT_DIRNAME, GT_IGNORE_ID
from app.io_utils import atomic_write_text

logger = logging.getLogger(__name__)

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_MAX_NAME_LEN = 40
_MAX_KEY_LEN = 32
_MIN_NORMAL_ID = 1
_MAX_NORMAL_ID = 254
# Keys no class may take: the dataset export writes id 0 as "background"
# (spec §4), so a class keyed "background" would make its classes.json ambiguous.
_RESERVED_KEYS = frozenset({"background"})


class RegistryError(ValueError):
    """Raised for an invalid or corrupt class registry."""


@dataclass(frozen=True)
class ClassDef:
    """One GT class: its pixel ``id`` in ``gt/<stem>.png``, a stable ``key``,
    a display ``name`` and a ``#rrggbb`` colour.

    ``id`` is 1-254 for ordinary classes and 255 for Ignore (0 is background and
    never a class). ``retired`` classes keep their id and colour reserved.
    """

    id: int
    key: str
    name: str
    color: str
    retired: bool = False

    @property
    def is_ignore(self) -> bool:
        """True for the Ignore class (key ``"ignore"``, id 255)."""
        return self.key == "ignore"


@dataclass(frozen=True)
class Registry:
    """Immutable, validated list of GT classes (the content of ``gt/classes.json``).

    Mutators (``add``/``update``/``retire``) return a new registry and re-check
    every invariant; an invalid result raises ``RegistryError``.
    """

    classes: tuple[ClassDef, ...]  # display order, Ignore last, retired included

    def active(self) -> tuple[ClassDef, ...]:
        """Non-retired classes, in display order (Ignore last)."""
        return tuple(c for c in self.classes if not c.retired)

    def labelled(self) -> tuple[ClassDef, ...]:
        """Active classes excluding Ignore."""
        return tuple(c for c in self.active() if not c.is_ignore)

    def by_id(self, id: int) -> ClassDef | None:
        """The class with pixel id ``id`` (retired included), or ``None``."""
        for c in self.classes:
            if c.id == id:
                return c
        return None

    def by_key(self, key: str) -> ClassDef:
        """The class with ``key`` (retired included). Raises ``KeyError`` if absent."""
        for c in self.classes:
            if c.key == key:
                return c
        raise KeyError(key)

    def next_id(self) -> int:
        """Lowest id in 1..254 not used by any class (retired included)."""
        used = {c.id for c in self.classes}
        for candidate in range(_MIN_NORMAL_ID, _MAX_NORMAL_ID + 1):
            if candidate not in used:
                return candidate
        raise RegistryError("No free class id in range 1-254")

    def add(self, *, key: str, name: str, color: str) -> Registry:
        """Return a new registry with a class appended just before Ignore."""
        new_class = ClassDef(id=self.next_id(), key=key, name=name, color=color, retired=False)
        classes = list(self.classes)
        ignore_index = next(i for i, c in enumerate(classes) if c.is_ignore)
        classes.insert(ignore_index, new_class)
        return self._with_classes(tuple(classes))

    def update(self, key: str, *, name: str | None = None, color: str | None = None) -> Registry:
        """Return a new registry with ``name``/``color`` changed for ``key``.

        Raises ``KeyError`` if ``key`` does not exist.
        """
        target = self.by_key(key)
        updated = replace(
            target,
            name=target.name if name is None else name,
            color=target.color if color is None else color,
        )
        classes = tuple(updated if c.key == key else c for c in self.classes)
        return self._with_classes(classes)

    def retire(self, key: str) -> Registry:
        """Return a new registry with ``key`` marked retired. Ignore cannot be retired."""
        target = self.by_key(key)
        if target.is_ignore:
            raise RegistryError("The Ignore class cannot be retired")
        updated = replace(target, retired=True)
        classes = tuple(updated if c.key == key else c for c in self.classes)
        return self._with_classes(classes)

    def to_dict(self) -> dict:
        """The ``gt/classes.json`` document (schema version 1), classes in display order."""
        return {
            "version": 1,
            "classes": [
                {
                    "id": c.id,
                    "key": c.key,
                    "name": c.name,
                    "color": c.color,
                    "retired": c.retired,
                }
                for c in self.classes
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> Registry:
        """Parse and validate the JSON of spec §2. Ignore is moved last if needed.

        Any malformed input (wrong JSON shape or field type included) raises
        ``RegistryError``; values are never coerced (``"retired": "false"`` is an
        error, not a retired class).
        """
        if not isinstance(data, dict):
            raise RegistryError(f"Registry must be a JSON object, got {type(data).__name__}")
        version = data.get("version")
        if isinstance(version, bool) or version != 1:
            raise RegistryError(f"Unsupported registry version: {version!r}")
        try:
            raw_classes = data["classes"]
        except KeyError as exc:
            raise RegistryError("Registry data missing 'classes'") from exc
        if not isinstance(raw_classes, list):
            raise RegistryError(f"'classes' must be a list, got {type(raw_classes).__name__}")
        parsed = tuple(_parse_class(index, item) for index, item in enumerate(raw_classes))

        non_ignore = tuple(c for c in parsed if not c.is_ignore)
        ignore = tuple(c for c in parsed if c.is_ignore)
        ordered = non_ignore + ignore
        _validate_classes(ordered)
        return cls(classes=ordered)

    def _with_classes(self, classes: tuple[ClassDef, ...]) -> Registry:
        _validate_classes(classes)
        return Registry(classes=classes)


_REQUIRED_FIELDS = ("id", "key", "name", "color")


def _parse_class(index: int, item: object) -> ClassDef:
    """Build one ``ClassDef`` from ``classes[index]`` with strict JSON types.

    Raises ``RegistryError`` naming the entry and field (``classes[2].id``).
    """
    where = f"classes[{index}]"
    if not isinstance(item, dict):
        raise RegistryError(f"{where} must be a JSON object, got {type(item).__name__}")
    missing = [name for name in _REQUIRED_FIELDS if name not in item]
    if missing:
        raise RegistryError(f"{where} is missing {', '.join(repr(name) for name in missing)}")
    return ClassDef(
        id=_json_int(item["id"], f"{where}.id"),
        key=_json_str(item["key"], f"{where}.key"),
        name=_json_str(item["name"], f"{where}.name"),
        color=_json_str(item["color"], f"{where}.color"),
        retired=_json_bool(item.get("retired", False), f"{where}.retired"),
    )


def _json_int(value: object, where: str) -> int:
    """A JSON integer. An integral float (``1.0``) is accepted; a bool, a
    fractional or non-finite number, or any other type is not."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    raise RegistryError(f"{where} must be an integer, got {value!r}")


def _json_str(value: object, where: str) -> str:
    if not isinstance(value, str):
        raise RegistryError(f"{where} must be a string, got {value!r}")
    return value


def _json_bool(value: object, where: str) -> bool:
    if not isinstance(value, bool):
        raise RegistryError(f"{where} must be true or false, got {value!r}")
    return value


def _validate_classes(classes: tuple[ClassDef, ...]) -> None:
    """Enforce the invariants of spec §2. Raises ``RegistryError`` on violation."""
    if not classes:
        raise RegistryError("Registry must contain at least the Ignore class")

    for c in classes:
        if not _KEY_RE.match(c.key):
            raise RegistryError(f"Invalid class key: {c.key!r}")
        if c.key in _RESERVED_KEYS:
            raise RegistryError(f"Class key {c.key!r} is reserved")
        if not c.name or len(c.name) > _MAX_NAME_LEN:
            raise RegistryError(f"Invalid class name: {c.name!r}")
        if not _COLOR_RE.match(c.color):
            raise RegistryError(f"Invalid class color: {c.color!r}")

    keys = [c.key for c in classes]
    if len(set(keys)) != len(keys):
        raise RegistryError("Duplicate class key")

    ids = [c.id for c in classes]
    if len(set(ids)) != len(ids):
        raise RegistryError("Duplicate class id")

    colors_lower = [c.color.lower() for c in classes]
    if len(set(colors_lower)) != len(colors_lower):
        raise RegistryError("Duplicate class colour (colours must be unique, retired included)")

    ignore_classes = [c for c in classes if c.is_ignore]
    if len(ignore_classes) != 1:
        raise RegistryError("Registry must contain exactly one Ignore class")
    ignore = ignore_classes[0]
    if ignore.id != GT_IGNORE_ID:
        raise RegistryError(f"Ignore class must have id {GT_IGNORE_ID}")
    if ignore.retired:
        raise RegistryError("The Ignore class cannot be retired")
    if classes[-1].key != "ignore":
        raise RegistryError("Ignore class must be last in display order")

    for c in classes:
        if c.is_ignore:
            continue
        if not (_MIN_NORMAL_ID <= c.id <= _MAX_NORMAL_ID):
            raise RegistryError(f"Class id out of range 1-254: {c.id}")


def slugify_key(name: str) -> str:
    """Slug a display name into a valid registry key (``^[a-z][a-z0-9_]{0,31}$``).

    A reserved key gets a ``_class`` suffix (``"Background"`` -> ``"background_class"``).
    """
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    if not slug:
        slug = "class"
    if not slug[0].isalpha():
        slug = f"c_{slug}"
    slug = slug[:_MAX_KEY_LEN]
    if slug in _RESERVED_KEYS:
        slug = f"{slug}_class"
    return slug


def default_registry() -> Registry:
    """The built-in registry (``app.config.DEFAULT_GT_CLASSES``)."""
    classes = tuple(ClassDef(**c) for c in DEFAULT_GT_CLASSES)
    _validate_classes(classes)
    return Registry(classes=classes)


def registry_path(project_dir: str) -> str:
    """``<project_dir>/gt/classes.json`` (the file may not exist yet)."""
    return os.path.join(project_dir, GT_DIRNAME, GT_CLASSES_FILENAME)


def load_registry(project_dir: str) -> Registry:
    """Load ``gt/classes.json``, or the defaults if it does not exist yet.

    Every way the file can be bad (unreadable, not UTF-8, not JSON, nested too
    deep, wrong shape, invalid classes) raises ``RegistryError``.
    """
    path = registry_path(project_dir)
    if not os.path.exists(path):
        return default_registry()
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, RecursionError) as exc:
        # ValueError: JSONDecodeError, UnicodeDecodeError. RecursionError: nested too deep (R4).
        raise RegistryError(f"Could not read registry {path}: {exc}") from exc
    return Registry.from_dict(data)


def save_registry(project_dir: str, registry: Registry) -> str:
    """Atomically write ``gt/classes.json`` (creates ``gt/`` if needed). Returns the path."""
    path = registry_path(project_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Hand-made data: the shared durable writer, with its lock retry (R8). A file
    # another program holds open raises FileLockedError, an OSError.
    atomic_write_text(path, json.dumps(registry.to_dict(), indent=2), durable=True)
    return path
