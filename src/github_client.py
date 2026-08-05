import json
import os
import re
from pathlib import Path

import requests

GITHUB_API = "https://api.github.com"

_PR_URL_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)/?"
)


def parse_pr_url(pr_url: str) -> dict:
    if pr_url.startswith("local:"):
        repo_path = Path(pr_url[len("local:"):]).resolve()
        meta_path = repo_path / ".mock_pr.json"
        if not meta_path.exists():
            return {"error": f"No .mock_pr.json found under {repo_path}. Run mock/create_mock_pr.py first."}
        with open(meta_path) as f:
            mock_meta = json.load(f)
        return {"mode": "local", "repo_path": str(repo_path), **mock_meta}

    m = _PR_URL_RE.match(pr_url.strip())
    if not m:
        return {"error": f"Could not parse a GitHub PR URL out of: {pr_url}"}

    return {
        "mode": "github",
        "owner": m.group("owner"),
        "repo": m.group("repo"),
        "pr_number": int(m.group("number")),
    }


def _auth_headers() -> dict:
    token = os.getenv("GITHUB_TOKEN")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_pr_metadata(parsed: dict) -> dict:
    if parsed.get("mode") == "local":
        return parsed 

    if parsed.get("mode") != "github":
        return {"error": "fetch_pr_metadata called with an unparsed/invalid PR reference."}

    url = f"{GITHUB_API}/repos/{parsed['owner']}/{parsed['repo']}/pulls/{parsed['pr_number']}"
    resp = requests.get(url, headers=_auth_headers(), timeout=30)
    if resp.status_code != 200:
        return {"error": f"GitHub API error {resp.status_code} fetching PR metadata: {resp.text[:300]}"}

    data = resp.json()
    return {
        "mode": "github",
        "owner": parsed["owner"],
        "repo": parsed["repo"],
        "pr_number": parsed["pr_number"],
        "title": data.get("title", ""),
        "body": data.get("body", "") or "",
        "base_branch": data["base"]["ref"],
        "base_sha": data["base"]["sha"],
        "base_clone_url": data["base"]["repo"]["clone_url"],
        "head_branch": data["head"]["ref"],
        "head_sha": data["head"]["sha"],
        "head_clone_url": data["head"]["repo"]["clone_url"],
    }


def post_comment(parsed: dict, body: str, dry_run: bool = False) -> dict:
    if dry_run or parsed.get("mode") == "local":
        out_dir = Path(parsed.get("repo_path", ".")).resolve() if parsed.get("mode") == "local" else Path("outputs")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "mock_review_comment.md"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(body)
        return {"success": True, "message": f"[DRY RUN] Comment not posted. Written to {out_path}"}
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        return {"success": False, "message": "Error: GITHUB_TOKEN not set."}
    url = f"{GITHUB_API}/repos/{parsed['owner']}/{parsed['repo']}/issues/{parsed['pr_number']}/comments"
    resp = requests.post(url, headers=_auth_headers(), json={"body": body}, timeout=30)
    if resp.status_code not in (200, 201):
        return {"success": False, "message": f"Error posting comment ({resp.status_code}): {resp.text[:300]}"}
    return {"success": True, "message": f"Comment posted: {resp.json().get('html_url', '(no url returned)')}"}


def post_review_comment(parsed: dict, commit_id: str, path: str, line: int, body: str,
                         side: str = "RIGHT", dry_run: bool = False) -> dict:
    if dry_run or parsed.get("mode") == "local":
        out_dir = Path(parsed.get("repo_path", ".")).resolve() if parsed.get("mode") == "local" else Path("outputs")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "mock_review_line_comments.jsonl"
        with open(out_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"path": path, "line": line, "side": side, "body": body}) + "\n")
        return {"success": True, "message": f"[DRY RUN] Line comment not posted. Appended to {out_path}"}
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        return {"success": False, "message": "Error: GITHUB_TOKEN not set."}
    url = f"{GITHUB_API}/repos/{parsed['owner']}/{parsed['repo']}/pulls/{parsed['pr_number']}/comments"
    payload = {"body": body, "commit_id": commit_id, "path": path, "line": line, "side": side}
    resp = requests.post(url, headers=_auth_headers(), json=payload, timeout=30)
    if resp.status_code != 201:
        return {"success": False, "message": f"Error posting line comment on {path}:{line} ({resp.status_code}): {resp.text[:300]}"}
    return {"success": True, "message": f"Line comment posted: {resp.json().get('html_url', '(no url returned)')}"}