# Project Report: Agentic PR Review & Debugging Assistant

## 1. Purpose

Reviewing a pull request splits into two jobs: checking that a change is mechanically sound (formatting, type errors, whether tests pass) and checking that it's logically correct (does the new code do what it's supposed to, does it handle edge cases, does it break anything that calls it). The first job is already solved by linters, type checkers, and CI. The second job is where this project is aimed: an agent that reads a diff for meaning, the way a careful reviewer would, and posts concrete suggested fixes as a comment on the PR - not a linter, and not a static analyzer, since neither of those reasons about correctness in this sense.

## 2. Reasons for selecting an agent for the architecture

This project was built on top of a LangGraph-based library called `deepagents`, which provides a sandboxed virtual filesystem with path-escape protection, subagent delegation via a built-in `task` tool, and permission rules.

Separately, RAG was considered and rejected as the primary architecture. RAG earns its cost when you have a large corpus that needs to be narrowed down before it can be sent to the model. A single PR's diff and immediate surrounding code essentially never has that problem; what actually varies between PRs is whether the reviewer needs to look at one more file or run the test suite, which is a tool-use and planning decision, not a retrieval one. An agent with file and test-running tools was the better fit for that reason, not because RAG is worse in general.

## 3. Architecture

- **Supervisor** (`src/agent.py`): discovers skills at runtime by scanning `skills/` for `SKILL.md` files - there is no hardcoded list of what the agent can do - and delegates to four isolated subagents, each restricted to only the tools its own skill file lists.
- **Downloader subagent**: resolves the PR reference (real GitHub URL or a local mock reference), clones the repository, computes and checks out the actual merge-base commit rather than just the base branch's current tip, and produces the diff.
- **Analyzer subagent**: the only subagent doing real judgment - reads the diff and any needed surrounding files or tests, and can run the test suite to confirm a suspected issue is real.
- **Commenter subagent**: formats findings into Markdown and posts them via the GitHub API, or writes them to a local file when running in dry-run/mock mode.
- **Skill-builder subagent**: the only subagent with write access to the `skills/` folder itself (behind an interrupt-approval permission rule), used rarely, only to persist a genuinely new reusable review pattern.
- **Two interfaces**: a CLI (`main.py`) and a Streamlit app (`app.py`), sharing the exact same supervisor and subagent logic - only the rendering of the live run differs between them.

## 4. Current status

- The deterministic pipeline - resolving a PR reference, cloning, computing and checking out the merge-base, producing the diff, running tests against the correct commit, and posting a comment (or writing one locally in dry-run mode) - is built and independently verified without spending any LLM calls: unit tests cover path-safety and PR-URL parsing, and the mock PR generator produces a real local repository whose three intentional bugs were confirmed to actually surface as diff content and as real test failures.
- Skill discovery and subagent construction are verified working end to end via `debug_subagents.py`: all four skills are discovered, and each subagent resolves to the exact tool set its `SKILL.md` specifies, with no unintended broader access.
- The Streamlit interface boots cleanly and its live-rendering logic was verified against a simulated agent stream before being tested against a real run.
- **The full pipeline, including the LLM review step itself, has now been validated end to end against the mock PR.** The supervisor planned the work, delegated in the correct order across all four stages, and the analyzer correctly identified all three intentionally injected bugs, including: the off-by-one in `average`, the removed zero-division guard in `divide`, and the inverted comparison in `clamp` - with textually correct suggested fixes for each one of the errors, plus sound general recommendations (edge-case unit tests, type hints, re-running the test suite). This is the result the mock PR test harness exists to produce, and it directly supports the architectural choice in Section 2: the analyzer reasoned correctly about semantic correctness from the diff and a small amount of surrounding context alone, with no retrieval step involved.