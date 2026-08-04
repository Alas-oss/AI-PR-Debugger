import subprocess
import json
from pathlib import Path

from langchain_core.tools import tool

from github_client import parse_pr_url, fetch_pr_metadata, post_review as post_review_api

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
PR_META_PATH = "outputs/pr_meta.json"

def _to_real_path(path_like: str) -> Path:
    """Map a path to a real location on disk, refusing anything outside WORKSPACE_ROOT.

    Handles two cases the model can hand us:
      1. A genuine virtual path like '/workspace/pr-42/repo' - what the model uses when
         talking to the built-in read_file/ls/write_file tools - gets remapped under
         WORKSPACE_ROOT.
      2. A real absolute path already inside WORKSPACE_ROOT - what resolve_pr's repo_path
         looks like for local mock PRs (Path(...).resolve() in github_client.py) - gets
         used as-is rather than double-mapped.
    """
    root = WORKSPACE_ROOT.resolve()
    raw = str(path_like).replace("\\", "/")

    native_path = Path(raw)
    if native_path.is_absolute():
        resolved = native_path.resolve()
        if resolved == root or root in resolved.parents:
            return resolved

    rel = Path(raw.lstrip("/")) if raw.startswith("/") else native_path

    resolved = (WORKSPACE_ROOT / rel).resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"path '{path_like}' resolves outside the workspace root and was refused")
    return resolved


def _run_git(args: list, cwd: Path, timeout: int = 120) -> tuple:
    proc = subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


@tool
def resolve_pr(pr_url: str) -> dict:
    """Parse a PR URL (a real https://github.com/<owner>/<repo>/pull/<n> URL, or a
    'local:<path>' mock reference) and fetch its base/head SHAs, branches, and clone URLs."""
    parsed = parse_pr_url(pr_url)
    if "error" in parsed:
        return {"success": False, "message": parsed["error"]}
    meta = fetch_pr_metadata(parsed)
    if "error" in meta:
        return {"success": False, "message": meta["error"]}
    try:
        meta_path = _to_real_path(PR_META_PATH)
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    except Exception:
        pass
    return {"success": True, "message": "Resolved PR metadata.", **meta}


@tool
def save_diff(diff_text: str) -> dict:
    """Save the produced diff text to the canonical path outputs/pr_diff.txt - always this
    exact path, chosen by this tool rather than by you, so a later step (or a retry after a
    provider failure) can reliably detect a diff already exists this run."""
    real_path = _to_real_path("outputs/pr_diff.txt")
    real_path.parent.mkdir(parents=True, exist_ok=True)
    real_path.write_text(diff_text, encoding="utf-8")
    return {"success": True, "message": "Diff saved to outputs/pr_diff.txt.", "path": "outputs/pr_diff.txt"}

@tool
def save_review_output(markdown: str, findings_json: str) -> dict:
    """Save the review markdown and JSON findings to their canonical paths
    (outputs/review_notes.md and outputs/findings.json - always these exact paths).
    findings_json must be a valid JSON array string shaped like:
    [{"path": "relative/file.py", "line": 42, "side": "RIGHT", "body": "..."}]"""
    try:
        parsed_findings = json.loads(findings_json)
        if not isinstance(parsed_findings, list):
            raise ValueError("findings_json must be a JSON array")
    except Exception as e:
        return {"success": False, "message": f"findings_json is not valid JSON: {e}"}
    notes_path = _to_real_path("outputs/review_notes.md")
    findings_path = _to_real_path("outputs/findings.json")
    notes_path.parent.mkdir(parents=True, exist_ok=True)
    notes_path.write_text(markdown, encoding="utf-8")
    findings_path.write_text(json.dumps(parsed_findings, indent=2), encoding="utf-8")
    return {"success": True, "message": "Saved outputs/review_notes.md and outputs/findings.json.",
            "notes_path": "outputs/review_notes.md", "findings_path": "outputs/findings.json"}


@tool
def post_review(pr_url: str, body: str, findings_path: str = "outputs/findings.json", dry_run: bool = False) -> dict:
    """Post ONE atomic PR review containing an overall summary plus every line-anchored
    finding together, via GitHub's 'create a review' endpoint - appears grouped under a
    single reviewer action in the PR's Files changed tab, not scattered as separate items.
    commit_id is read automatically from outputs/pr_meta.json (written by resolve_pr earlier
    this run), never trusted from anything passed in - this is what prevents posting against
    a stale or wrong commit. Always pass dry_run=true for the local mock-PR test."""
    parsed = parse_pr_url(pr_url)
    if "error" in parsed:
        return {"success": False, "message": parsed["error"]}

    try:
        meta = json.loads(_to_real_path(PR_META_PATH).read_text(encoding="utf-8"))
        commit_id = meta["head_sha"]
    except Exception as e:
        return {"success": False, "message": f"Could not read head_sha from {PR_META_PATH} - resolve_pr must run first this run: {e}"}

    try:
        findings = json.loads(_to_real_path(findings_path).read_text(encoding="utf-8"))
    except Exception as e:
        return {"success": False, "message": f"Could not read/parse findings JSON at {findings_path}: {e}"}

    comments = [{"path": f["path"], "line": f["line"], "side": f.get("side", "RIGHT"), "body": f["body"]} for f in findings]
    return post_review_api(parsed, commit_id=commit_id, body=body, comments=comments, dry_run=dry_run)

@tool
def clone_repo(clone_url: str, dest_path: str) -> dict:
    """Clone a repo's clone_url into dest_path (e.g. '/workspace/pr-42/repo'). No-op if that
    path is already a git repo. Skip entirely for local mock PRs - resolve_pr's repo_path is
    already on disk, nothing to clone."""
    dest = _to_real_path(dest_path)
    if (dest / ".git").exists():
        return {"success": True, "message": f"Repo already cloned at {dest_path}.", "path": dest_path}
    dest.parent.mkdir(parents=True, exist_ok=True)
    code, out, err = _run_git(["clone", clone_url, str(dest)], cwd=WORKSPACE_ROOT)
    if code != 0:
        return {"success": False, "message": f"Error cloning {clone_url}: {err}"}
    return {"success": True, "message": f"Cloned {clone_url} into {dest_path}.", "path": dest_path}


@tool
def checkout_merge_base(repo_path: str, base_sha: str, head_sha: str) -> dict:
    """Fetch head_sha if needed, compute the merge-base of base_sha/head_sha, and check it out.
    This puts the working tree at the commit the PR branched FROM, so read_file calls see the
    original surrounding code, not whatever the base branch has moved on to since."""
    real_path = _to_real_path(repo_path)
    _run_git(["fetch", "origin", head_sha], cwd=real_path)  
    code, merge_base, err = _run_git(["merge-base", base_sha, head_sha], cwd=real_path)
    if code != 0:
        return {"success": False, "message": f"Error computing merge-base: {err}"}
    code, out, err = _run_git(["checkout", "--quiet", merge_base], cwd=real_path)
    if code != 0:
        return {"success": False, "message": f"Error checking out merge-base {merge_base}: {err}"}
    return {"success": True, "message": f"Merge-base is {merge_base}; repo checked out to it.", "merge_base_sha": merge_base}


@tool
def checkout_ref(repo_path: str, ref: str) -> dict:
    """Switch the working tree to a specific commit/branch, e.g. head_sha before run_tests -
    checkout_merge_base leaves the repo at the pre-PR commit, which is correct for reading
    original context but wrong for testing the PR's actual changes."""
    real_path = _to_real_path(repo_path)
    code, out, err = _run_git(["checkout", "--quiet", ref], cwd=real_path)
    if code != 0:
        return {"success": False, "message": f"Error checking out {ref}: {err}"}
    return {"success": True, "message": f"Repo now checked out at {ref}."}


@tool
def get_diff(repo_path: str, base_sha: str, head_sha: str) -> dict:
    """Get the unified diff between base_sha and head_sha - the actual PR changes to review."""
    real_path = _to_real_path(repo_path)
    code, out, err = _run_git(["diff", base_sha, head_sha], cwd=real_path)
    if code != 0:
        return {"success": False, "message": f"Error producing diff: {err}"}
    return {"success": True, "message": "Diff produced.", "diff": out or "(empty diff)"}


@tool
def run_tests(repo_path: str, command: str = "pytest -q") -> dict:
    """Run the repo's test command to confirm a suspected issue actually breaks something,
    rather than asserting it does from reading alone. Remember to checkout_ref to head_sha first."""
    real_path = _to_real_path(repo_path)
    try:
        proc = subprocess.run(command.split(), cwd=str(real_path), capture_output=True, text=True, timeout=90)
        output = (proc.stdout + "\n" + proc.stderr).strip()
        return {"success": proc.returncode == 0, "message": f"exit code {proc.returncode}", "output": output[:4000]}
    except FileNotFoundError:
        return {"success": False, "message": f"test command '{command}' not found in this environment."}
    except subprocess.TimeoutExpired:
        return {"success": False, "message": "test run timed out after 90s."}

ALL_TOOLS = [resolve_pr, clone_repo, checkout_merge_base, checkout_ref, get_diff, run_tests, save_diff, save_review_output, post_review]
TOOLS_BY_NAME = {t.name: t for t in ALL_TOOLS}