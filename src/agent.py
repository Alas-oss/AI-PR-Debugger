import os
import sys
import shutil
import time
import json
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
from langchain_cerebras import ChatCerebras
from langchain_groq import ChatGroq
from langchain_google_genai import ChatGoogleGenerativeAI
from langfuse import get_client
from langfuse.langchain import CallbackHandler
from deepagents import create_deep_agent
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.middleware.filesystem import FilesystemPermission
from langchain_mistralai import ChatMistralAI

from tools import ALL_TOOLS, TOOLS_BY_NAME
from memory import LongTermMemoryStore
from tui import stream_and_print

root_dir = Path(__file__).resolve().parent.parent  
load_dotenv(dotenv_path=root_dir / ".env")
langfuse = get_client()
langfuse_handler = CallbackHandler()

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

PROVIDER_CHAIN = ["cerebras"]
MAX_GRAPH_STEPS = 50


def _build_model(provider: str):
    if provider == "mistral":
        return ChatMistralAI(model="mistral-medium-2508", api_key=os.getenv("MISTREAK_API_KEY"), temperature=0.2, timeout=60, max_retries=2)
    elif provider == "cerebras":
        return ChatCerebras(model="gpt-oss-120b", api_key=os.getenv("CEREBRAS_API_KEY"), temperature=0.2, timeout=60, max_retries=2)
    elif provider == "gemini":
        return ChatGoogleGenerativeAI(model="gemini-flash-latest", api_key=os.getenv("GOOGLE_API_KEY"), temperature=0.2, timeout=60, max_retries=2)
    else:
        return ChatGroq(model="openai/gpt-oss-120b", api_key=os.getenv("GROQ_API_KEY"), temperature=0.2, timeout=60, max_retries=2)


class PRReviewAgent:
    def __init__(self, pr_url: str, user_id: str = "default_dev_user", dry_run: bool = False):
        self.pr_url = pr_url
        self.dry_run = dry_run
        self.user_namespace = user_id

        repo_root = Path(__file__).resolve().parent.parent
        self.skills_dir = repo_root / "skills"          
        self.output_dir = repo_root / "outputs"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "run_mode.json").write_text(
            json.dumps({"dry_run": self.dry_run}), encoding="utf-8"
        ) 
        for stale in self.output_dir.glob("*"):
            if stale.is_file() and stale.name != "long_term_store.json":
                stale.unlink()

        self.temp_dir = str(repo_root / "outputs" / ".tmp")
        Path(self.temp_dir).mkdir(parents=True, exist_ok=True)

        self.backend = FilesystemBackend(root_dir=str(repo_root), virtual_mode=True)
        self.memory_store = LongTermMemoryStore(filename=self.output_dir / "long_term_store.json")

        self.available_skills_index = self._discover_available_skills()
        self.subagents = self._build_skill_subagents()

        self.permissions = [
            FilesystemPermission(operations=["write"], paths=["/skills/**"], mode="interrupt"),
            FilesystemPermission(operations=["write"], paths=["/workspace/**"], mode="allow"),
            FilesystemPermission(operations=["write"], paths=["/outputs/**"], mode="allow"),
        ]
        self.agent = None

    def _discover_available_skills(self) -> dict:
        skills_map = {}
        if not self.skills_dir.exists():
            return {}
        for folder in self.skills_dir.iterdir():
            if not folder.is_dir():
                continue
            skill_file = folder / "SKILL.md"
            if not skill_file.exists():
                continue
            try:
                text = skill_file.read_text(encoding="utf-8")
                if not text.startswith("---"):
                    continue
                meta_section = text.split("---")[1]
                meta_data = {}
                for line in meta_section.strip().split("\n"):
                    if ":" in line:
                        k, v = line.split(":", 1)
                        meta_data[k.strip()] = v.strip()
                skills_map[folder.name] = meta_data
            except Exception:
                continue
        return skills_map

    def _load_blueprint_body(self, skill_name: str) -> str:
        skill_file = self.skills_dir / skill_name / "SKILL.md"
        text = skill_file.read_text(encoding="utf-8")
        parts = text.split("---", 2)
        body = parts[2].strip() if len(parts) >= 3 else text.strip()
        for cutoff in ("## Example call", "## Additional reference material"):
            idx = body.find(cutoff)
            if idx != -1:
                body = body[:idx].strip()
        return body

    def _build_skill_subagents(self) -> list[dict]:
        subagents = []
        shared_reporting_rule = (
            "\n\nIMPORTANT - reporting back to the supervisor: your final response must be "
            "SHORT (2-6 sentences plus any findings list) - confirm what you did and reference "
            f"file paths/SHAs, don't repeat large content. If you produced something large "
            f"(a long diff, a big findings writeup), write it to a file under {self.temp_dir} "
            "first and mention that path instead of pasting it inline."
        )
        for skill_name, meta in self.available_skills_index.items():
            raw_tools_field = meta.get("tools", "").strip()
            allowed = [t.strip() for t in raw_tools_field.split(",") if t.strip()]
            unknown = [t for t in allowed if t not in TOOLS_BY_NAME]
            if unknown:
                print(f"[agent] WARNING: skill '{skill_name}' lists unknown tool(s) {unknown} "
                      f"in its SKILL.md frontmatter - they will be silently dropped. Known "
                      f"custom tools are: {list(TOOLS_BY_NAME.keys())}", flush=True)
            tools = [TOOLS_BY_NAME[t] for t in allowed if t in TOOLS_BY_NAME]
            subagents.append({
                "name": f"{skill_name}-agent",
                "description": meta.get("description", f"Handles {skill_name} tasks"),
                "system_prompt": (
                    f"You are the '{skill_name}' subagent for a PR-review pipeline.\n"
                    "Follow this skill blueprint exactly:\n\n"
                    f"{self._load_blueprint_body(skill_name)}"
                    f"{shared_reporting_rule}"
                ),
                "tools": tools,
            })
        return subagents

    def _build_system_prompt(self) -> str:
        if self.available_skills_index:
            skills_list = "\n".join(
                f"  - {name}-agent: {meta.get('description', 'No description provided.')}"
                for name, meta in self.available_skills_index.items()
            )
        else:
            skills_list = "  (no skills discovered under ./skills - proceeding with raw tools only)"

        prior_context = self.memory_store.get_all_context(self.user_namespace)
        dry_run_note = (
            "THIS IS A DRY RUN / MOCK-PR TEST. Always tell the commenter-agent to pass "
            "dry_run=true to post_github_comment."
            if self.dry_run else
            "This is a real PR. Only use dry_run=true if you were explicitly told to."
        )

        diff_path = self.output_dir / "pr_diff.txt"
        notes_path = self.output_dir / "review_notes.md"
        findings_path = self.output_dir / "findings.json"

        resume_note = ""
        if diff_path.exists():
            resume_note += (
                "\nA diff has ALREADY been produced this run at outputs/pr_diff.txt (an earlier "
                "provider attempt ran out of quota partway through). Do NOT delegate to "
                "downloader-agent again - go straight to analyzer-agent, telling it to use this "
                "existing file."
            )
        if notes_path.exists() and findings_path.exists():
            resume_note += (
                "\nReview notes and findings have ALREADY been produced this run at "
                "outputs/review_notes.md and outputs/findings.json. Do NOT delegate to "
                "analyzer-agent again - go straight to commenter-agent with these existing files."
            )
        return f"""You are the supervisor of a PR-review agent cluster. Your mission: find real
issues in the given pull request's diff (bugs, edge cases, security problems, likely test
failures) and post clear, actionable suggestions as a single PR comment. You do not guess -
issues must be grounded in the actual diff and surrounding code, not assumed.

PRIOR REVIEW HISTORY FOR THIS USER:
{prior_context}

TARGET PR: {self.pr_url}
{dry_run_note}

AVAILABLE SUBAGENTS (delegate matching work to the exact named subagent via the task tool):
{skills_list}

CRITICAL ARCHITECTURAL CONSTRAINTS:
- Your FIRST action in every run must be a write_todos call laying out your complete plan.
  Each todo item must explicitly name which subagent (downloader-agent/analyzer-agent/
  commenter-agent/skill-builder-agent) will handle it. Do not call any other tool - including
  ls, glob, or task - before this planning step is complete.
- Follow this order and do not skip or reorder: downloader-agent resolves+clones+diffs the PR,
  then analyzer-agent reviews the diff and any needed surrounding files/tests, then
  commenter-agent formats and posts the findings. Only delegate to skill-builder-agent AFTER
  the comment is posted, and only if you noticed a genuinely new, reusable review pattern this
  run - most runs should not touch it at all.
- SUBAGENTS DO NOT SHARE YOUR CONTEXT. Each one starts with a blank slate and only sees what
  you put directly into its task description. Never reference prior content vaguely (e.g.
  "the diff from before") - paste short content directly into the task description, or for
  large content (a full diff, a long findings list), have the producing subagent write it to a
  file under {self.temp_dir} first and tell the next subagent to read_file that path.
- Never call clone_repo, get_diff, checkout_merge_base, checkout_ref, run_tests, or
  post_github_comment yourself - those belong to a skill's subagent so its blueprint's
  constraints are actually enforced. You may call resolve_pr yourself only to sanity-check a
  URL before delegating, never as a substitute for delegating the actual work.
- Never call read_file, ls, or glob yourself to inspect or verify a subagent's output - that
  wastes turns and risks guessing the wrong path. Trust each subagent's own final report, and
  move directly to the next delegation.
"""

    def execute_react_loop(self, printer=None):
        printer = printer or stream_and_print
        try:
            print(f"[agent] starting run - PR: {self.pr_url}", flush=True)
            print(f"[agent] subagents loaded: {[s['name'] for s in self.subagents]}", flush=True)
            errors = []
            for provider in PROVIDER_CHAIN:
                print(f"[agent] trying provider: {provider}", flush=True)
                for attempt in (1, 2):
                    try:
                        self.agent = create_deep_agent(
                            model=_build_model(provider),
                            backend=self.backend,
                            subagents=self.subagents,
                            system_prompt=self._build_system_prompt(),
                            permissions=self.permissions,
                        )
                        result = printer(
                            self.agent,
                            {"messages": [{"role": "user", "content": f"Review the pull request: {self.pr_url}"}]},
                            config={
                                "callbacks": [langfuse_handler],
                                "metadata": {"user_id": self.user_namespace, "tags": ["pr-review", self.pr_url[:60]]},
                                "recursion_limit": MAX_GRAPH_STEPS,
                            },
                        )
                        self.memory_store.save_memory(
                            self.user_namespace, "last_reviewed_pr",
                            {"pr_url": self.pr_url, "status": "completed"},
                        )
                        return result
                    except Exception as e:
                        status = getattr(e, "status_code", None)
                        if status is None and getattr(e, "response", None) is not None:
                            status = getattr(e.response, "status_code", None)
                        err_text = str(2)
                        if getattr(e, "response", None) is not None:
                            try:
                                err_text = f"{err_text} | nody: {e.response.text[:500]}"
                            except Exception:
                                pass
                        is_rate_limited = (
                            status == 429 or "rate_limit" in err_text.lower() or "resource_exhausted" in err_text.lower()
                        )
            raise RuntimeError("All providers exhausted:\n" + "\n".join(errors))
        finally:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
            print(f"[agent] cleaned up temp dir: {self.temp_dir}", flush=True)