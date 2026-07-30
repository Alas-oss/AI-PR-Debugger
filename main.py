import sys 
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
SRC_DIR = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from agent import PRReviewAgent, langfuse

def default_usre_id() -> str:
    import getpass
    try:
        return getpass.getuser() or "anonymous"
    except Exception:
        return "anonymous"

def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print(" python main.py https://github.com/<owner>/<repo>/pull/<n>")
        print(" python main.py local:<path-to-mock-repo> # set up first with mock/create_mock_pr.py")
        sys.exit(1)

    pr_url = sys.argv[1]
    dry_run = "--dry-run" in sys.argv[2:] or pr_url.startswith("local:")

    agent = PRReviewAgent(pr_url=pr_url, user_id=default_usre_id(), dry_run=dry_run)
    try:
        agent.execute_react_loop()
    finally:
        langfuse.flush()

if __name__ == "__main__":
    main()