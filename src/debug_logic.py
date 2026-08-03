import sys
from pathlib import Path
sys.path.insert(0, "src")
from agent import PRReviewAgent

agent = PRReviewAgent(pr_url="local:./mock/mock_repo", user_id="debug", dry_run=True)
diff_path = agent.output_dir / "pr_diff.txt"
notes_path = agent.output_dir / "review_notes.md"
findings_path = agent.output_dir / "findings.json"

def show(label):
    prompt = agent._build_system_prompt()
    print(f"\n {label} ")
    if "ALREADY been produced" in prompt:
        start = prompt.find("A diff has ALREADY")
        if start == -1:
            start = prompt.find("Review notes and findings")
        print(prompt[start:start+300], "...")
    else:
        print("(no resume language present - would start from downloader-agent)")

for p in (diff_path, notes_path, findings_path):
    p.unlink(missing_ok=True)
show("Fresh run, no files")

diff_path.write_text("fake diff content for testing")
show("Diff exists - should skip downloader")

notes_path.write_text("fake notes")
findings_path.write_text("[]")
show("Diff + notes + findings exist - should skip straight to commenter")

for p in (diff_path, notes_path, findings_path):
    p.unlink(missing_ok=True)
print("\nCleaned up test files.")