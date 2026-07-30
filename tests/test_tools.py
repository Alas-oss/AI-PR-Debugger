import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest
from tools import _to_real_path, WORKSPACE_ROOT, resolve_pr


def test_virtual_path_maps_under_workspace_root():
    result = _to_real_path("/workspace/pr-42/repo")
    assert result == (WORKSPACE_ROOT / "workspace" / "pr-42" / "repo").resolve()


def test_real_path_already_under_root_passes_through():
    real = WORKSPACE_ROOT / "mock" / "mock_repo"
    assert _to_real_path(str(real)) == real.resolve()


def test_relative_traversal_is_refused():
    with pytest.raises(ValueError):
        _to_real_path("../../../etc/passwd")


def test_absolute_path_outside_root_is_remapped_not_followed():
    result = _to_real_path("/etc/passwd")
    assert str(result).startswith(str(WORKSPACE_ROOT.resolve()))


def test_resolve_pr_rejects_garbage_url():
    result = resolve_pr.func("not-a-url-at-all")
    assert result["success"] is False


def test_resolve_pr_missing_mock_metadata():
    result = resolve_pr.func("local:/tmp/definitely-not-a-mock-repo-dir")
    assert result["success"] is False
    assert "mock_pr.json" in result["message"] or ".mock_pr.json" in result["message"]
