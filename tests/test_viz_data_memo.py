"""FigureMemo: bounded, least-recently-used, never stores a failed build (PV-17)."""

import pytest

from app.visualization.figure_memo import FigureMemo


class _CountingBuilder:
    def __init__(self):
        self.calls = []

    def __call__(self, key):
        def build():
            self.calls.append(key)
            return f"figure-{key}"

        return build


def test_second_request_is_served_without_rebuilding():
    memo, builder = FigureMemo(maxsize=4), _CountingBuilder()

    assert memo.get_or_build("a", builder("a")) == "figure-a"
    assert memo.get_or_build("a", builder("a")) == "figure-a"
    assert builder.calls == ["a"]


def test_least_recently_used_entry_is_evicted_first():
    memo, builder = FigureMemo(maxsize=2), _CountingBuilder()
    memo.get_or_build("a", builder("a"))
    memo.get_or_build("b", builder("b"))
    memo.get_or_build("a", builder("a"))  # touch a -> b is now the oldest
    memo.get_or_build("c", builder("c"))  # evicts b

    memo.get_or_build("a", builder("a"))
    memo.get_or_build("b", builder("b"))

    assert builder.calls == ["a", "b", "c", "b"]


def test_failed_build_is_not_memoized():
    memo, builder = FigureMemo(maxsize=2), _CountingBuilder()

    def broken():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        memo.get_or_build("a", broken)
    assert memo.get_or_build("a", builder("a")) == "figure-a"


def test_clear_forgets_everything():
    memo, builder = FigureMemo(maxsize=2), _CountingBuilder()
    memo.get_or_build("a", builder("a"))

    memo.clear()
    memo.get_or_build("a", builder("a"))

    assert builder.calls == ["a", "a"]


def test_size_must_be_positive():
    with pytest.raises(ValueError):
        FigureMemo(maxsize=0)
