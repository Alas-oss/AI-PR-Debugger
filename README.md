# AI PR Debugger & Advisor - Agentic Pull Request Review

Reviews a GitHub pull request end-to-end: clones the repository, checks out the exact commit the PR branched from, reads the diff plus whatever surrounding code or tests it needs, identifies real issues, and posts the suggested fixes as a comment on the PR. Can also be pointed at a locally generated mock PR with intentionally broken code, so the whole pipeline can be exercised and verified without a GitHub account or API token.

## Reasons for selecting an agent for the architecture of the project

A single PR diff and its immediate surrounding context almost always fits in an LLM's context window, so there's no large corpus of information that needs retrieving from - large pieces of external data are the reason RAG gets used on similar projects, but that's not the situation here. What varies between PRs is how much *extra* context is needed: sometimes none, other times "check who else calls this function" or "run the test suite to confirm that this actually breaks." That's a planning and tool-use problem, which is what an agent is for, not a retrieval problem.

This project was built on top of a LangGraph-based library called `deepagents`, which already provides subagent delegation, a sandboxed virtual filesystem, and permission rules.

## Architecture
```
main.py                   -> CLI entrypoint
app.py                    -> Streamlit web interface (must stay at repo root, not in src/ -
                              see note below)
src/
  agent.py                 -> Supervisor: skill discovery, subagent construction, main run loop
  tools.py                  -> Git and filesystem-path tools used by the subagents
  github_client.py           -> PR URL parsing, GitHub API calls, comment posting
  memory.py                   -> Persistent per-user review history
  tui.py                        -> Console output for the CLI
  debug_subagents.py             -> Standalone skill/subagent wiring check, costs zero API calls
skills/
  downloader/SKILL.md       -> resolves PR, clones repo, checks out merge-base, produces diff
  analyzer/SKILL.md          -> reads diff + context, runs tests, identifies concrete issues
  commenter/SKILL.md          -> formats findings, posts the PR comment
  skill-builder/SKILL.md       -> persists a new reusable review pattern, when one is found (rare)
mock/
  create_mock_pr.py         -> generates a local repo with intentional bugs for testing
tests/
  test_tools.py              -> unit tests for path safety and PR-URL parsing
```
**Important structural note**: `app.py` must stay at the project root, not inside `src/`, since it resolves its own file paths relative to its own location. `agent.py` accepts an injectable `printer` argument specifically so it never needs to know Streamlit exists at all.

## Pipeline

1. **Plan and delegate** (`agent.py`) - the supervisor writes its plan (`write_todos`) and delegates to `downloader-agent`.
2. **Resolve, clone, diff** (`downloader-agent`, `src/tools.py`) - resolves the PR reference, clones the repository, computes and checks out the merge-base commit (the point where the PR's branch actually diverged), and produces the diff.
3. **Analyze** (`analyzer-agent`) - reads the diff, expands into surrounding files or tests as needed, and optionally runs the test suite (after checking out the head commit) to confirm a suspected issue is real rather than assumed.
4. **Comment** (`commenter-agent`, `src/github_client.py`) - formats the findings into Markdown and posts them as a single PR comment, or writes them to a local file in dry-run/mock mode.
5. **Optionally, persist a new skill** (`skill-builder-agent`) - only if a genuinely new, reusable review pattern was identified; most reviews never reach this step.

Subagents do not share context with each other or with the supervisor - each starts fresh and only sees what's explicitly passed into its task description, or written to a shared temp file.

## Verified output

Running the pipeline against the mock PR (three intentionally broken functions) produced this review, confirming that the agent correctly reasons about *why* code is wrong, not just that it differs from some baseline:

| Function | Issue | Impact | Suggested Fix |
|---|---|---|---|
| `average` | Uses `len(numbers) - 1` as the denominator | Wrong results; division-by-zero for single-element lists | Restore `return sum(numbers) / len(numbers)` |
| `divide` | Removed explicit zero-division guard and docstring | Generic `ZeroDivisionError` instead of a clear `ValueError` | Re-add the `if b == 0` check with a descriptive error, restore the docstring |
| `clamp` | Logic inverted (`max(high, min(value, low))`) | Produces opposite-range results | Restore `return max(low, min(value, high))` |

All three intentional bugs were caught, with textually correct fixes for each, plus sound general recommendations (edge-case unit tests, type hints, re-running the full test suite after fixing).

## Setup

```
uv sync
```
```powershell
Copy-Item .env.example .env
```
For bash use `cp .env.example .env`

Fill in `.env` with at least one LLM provider key (`GROQ_API_KEY`, `CEREBRAS_API_KEY`, or `GOOGLE_API_KEY`) - the agent tries each configured provider in order until one works.

## Getting a GitHub token

It's only needed to post comments on a real PR - not needed for the mock test below.
Create a **fine-grained** personal access token at `github.com/settings/personal-access-tokens/new`: resource owner = you, repository access = the specific repo(s) you'll test against, permissions = `Pull requests: Read-only` and `Issues: Read and write`. Add it to `.env` as `GITHUB_TOKEN`.

## Trying it without a real PR

Builds a local repository with three intentionally broken functions on a branch, then reviews it end to end with no network calls and no GitHub token:
```
uv run python mock/create_mock_pr.py
uv run python main.py local:./mock/mock_repo
```

## Reviewing a real pull request

```
uv run python main.py https://github.com/<owner>/<repo>/pull/<number>
```
Add `--dry-run` to run the full pipeline (real clone, real diff, real LLM review) without actually posting the comment - useful for judging review quality before trusting it to post.

## Web interface

```
uv run streamlit run app.py
```
This shows each subagent delegation and tool call live as it happens, plus a button to generate the mock PR directly from the sidebar.

## Testing

```
uv run pytest tests/ -q
```

## Verifying skill/subagent wiring without an API call

```
uv run python src/debug_subagents.py
```
Prints which skills were discovered and which tools each resulting subagent was granted. Run this after editing any `SKILL.md` - it costs nothing and catches wiring mistakes immediately.

## Skills

Each folder under `skills/` is a `SKILL.md` with frontmatter:
```
---
name: "skill-name"
description: one-line summary of what this skill does
tools: tool_a, tool_b
---

Detailed instructions for the subagent
```
`tools:` must be a **bare comma-separated list** - the frontmatter parser does not understand YAML/JSON list syntax (`["a", "b"]`), and will silently drop every entry if written that way. It must reference names from the registry in `src/tools.py`: `resolve_pr`, `clone_repo`, `checkout_merge_base`, `checkout_ref`, `get_diff`, `run_tests`, `post_github_comment`. A skill that needs no custom tools (like `skill-builder`, which only writes files) should leave `tools:` empty - it still gets the built-in filesystem tools (`read_file`, `write_file`, `ls`, `glob`) automatically.

## Limitations

- Review quality depends on the underlying model; this project does not fine-tune or specialize a model for code review.
- `run_tests` assumes a runnable test command exists in the target repo and is configured correctly; it does not infer one automatically.
- GitHub API rate limits apply, and posting comments requires a token with the scopes above.
- This tool is a complement to, not a replacement for, deterministic checks (linters, type checkers, CI test suites) - those remain the right tool for anything expressible as a fixed rule.
- The mock PR test harness exercises one specific set of intentionally introduced bugs; passing it is a smoke test, not a guarantee of correctness against arbitrary real-world PRs.
- End-to-end latency on free-tier providers can be significant (an early full run took ~24 minutes, mostly spent on rate-limit fallback between providers) - a paid tier on at least one provider is recommended for anything beyond occasional testing.