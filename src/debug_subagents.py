import sys
sys.path.insert(0, "src")
from agent import PRReviewAgent

d = PRReviewAgent(pr_url="local:./mock/mock_repo", user_id="debug", dry_run=True)
print("skills discovered:", list(d.available_skills_index.keys()))
print("subagent count:", len(d.subagents))
for s in d.subagents:
    print(" -", s["name"], "| tools:", [t.name for t in s["tools"]], "| prompt length:", len(s["system_prompt"]))
