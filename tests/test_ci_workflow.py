"""The CI workflow runs on feature and fix branches too (X7)."""

from pathlib import Path

import pytest

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"


def test_ci_runs_on_feature_and_fix_branches():
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    on = doc.get("on", doc.get(True))
    assert {"feat/**", "fix/**"} <= set(on["push"]["branches"])
    assert "pull_request" in on
