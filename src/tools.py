import subprocess
import json
from pathlib import Path

from langchain_core.tools import tool

from github_client import parse_pr_url, fetch_pr_metadata, post_comment, post_review_comment

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent


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
    return {"success": True, "message": "Resolved PR metadata.", **meta}


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


@tool
def post_github_comment(pr_url: str, body: str, dry_run: bool = False) -> dict:
    """Post the finished review as a comment on the PR. Always pass dry_run=true for the
    local mock-PR test - without it this will try (and fail) to hit the real GitHub API."""
    parsed = parse_pr_url(pr_url)
    if "error" in parsed:
        return {"success": False, "message": parsed["error"]}
    result = post_comment(parsed, body, dry_run=dry_run)
    return {"success": not result.startswith("Error"), "message": result}


# make it publish comments by line
# Give it a place to actually post the created commnet
# change the prompt so that it doesn't just create one .md code file but multiple smaller files that can be 
# routed to the different lines of code
# instead of md make a json file so that the model could directly upload the comments to the corresponding lines
# body can still be in md format but check the doc for other parameters

@tool
def post_review_comments(pr_url: str, commit_id: str, findings_path: str, dry_run: bool = False) -> dict:
    """Post multiple line-anchored review comments on a PR, one per finding, from a JSON file 
    at findings_path. The file mush contain a JSON array of objects shaped like: 
    {"path": "relative/file.py", "line": 42, "body": "explanation + suggested fix", "side": "RIGHT"}
    ("side" is optional, defaults to "RIGHT" - use "LEFT" only for a findings about a deleted line).
    Always pass dry_run=true for the local mock-Pr test."""
    parsed = parse_pr_url(pr_url)
    if "error" in parsed:
        return {"success": False, "message": parsed["error"]}

    real_path = _to_real_path(findings_path)
    try:
        findings = json.loads(real_path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"success": False, "message": f"Could not read/parse findings JSON at {findings_path}: {e}"}

    if not isinstance(findings, list) or not findings:
        return {"success": False, "message": f"Expected a non-empty JSON array of findings at {findings_path}."}

    posted, failed, details = 0, 0, []
    for item in findings:
        result = post_review_comment(
            parsed, commit_id=commit_id, path=item["path"], line=item["line"],
            body=item["body"], side=item.get("side", "RIGHT"), dry_run=dry_run,
        )
        details.append(result["message"])
        posted += int(result["success"])
        failed += int(not result["success"])

    return {"success": failed == 0, "message": f"Posted {posted}/{len(findings)} line comments.", "details": details}

ALL_TOOLS = [resolve_pr, clone_repo, checkout_merge_base, checkout_ref, get_diff, run_tests, post_github_comment, post_review_comments]
TOOLS_BY_NAME = {t.name: t for t in ALL_TOOLS}