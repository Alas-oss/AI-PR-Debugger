import streamlit as st
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "mock"))

from agent import PRReviewAgent, langfuse  # noqa: E402
from mock import create_mock_pr  # noqa: E402

st.set_page_config(
    page_title="PR Review Agent",
    page_icon="🔍",
    layout="centered",
)


def render_stream_to_status(agent, inputs, config, status):
    final_state = None
    for chunk in agent.stream(inputs, config=config, stream_mode="values"):
        final_state = chunk
        last_message = chunk["messages"][-1]
        tool_calls = getattr(last_message, "tool_calls", None)

        if tool_calls:
            for tc in tool_calls:
                if tc["name"] == "task":
                    skill = tc["args"].get("subagent_type", "unknown")
                    status.update(label=f"Delegating to {skill}...")
                    status.write(f"🗂️ **delegating to `{skill}`**")
                else:
                    status.write(f"🔧 `{tc['name']}`&nbsp;&nbsp;`{tc['args']}`", unsafe_allow_html=False)
        elif getattr(last_message, "type", "") == "tool":
            content = str(last_message.content)
            if len(content) > 500:
                content = content[:500] + " …(truncated)"
            status.write(f"↳ {content}")
        elif getattr(last_message, "type", "") == "ai" and last_message.content:
            status.write(f"💭 {last_message.content}")

    return final_state


def run_review(pr_url: str, dry_run: bool, status) -> tuple:
    agent = PRReviewAgent(pr_url=pr_url, user_id="streamlit_user", dry_run=dry_run)
    result = agent.execute_react_loop(
        printer=lambda a, i, config: render_stream_to_status(a, i, config, status)
    )
    langfuse.flush()
    if not result or not result.get("messages"):
        return "No response produced.", None
    return result["messages"][-1].content, result


with st.sidebar:
    st.header("About")
    st.write(
        "This reviews a pull request using an **agent** (not RAG) - a supervisor delegates "
        "to specialist subagents that clone the repo, check out the merge-base, read the "
        "diff plus surrounding code and tests, and post suggested fixes as a PR comment."
    )
    st.divider()
    st.subheader("Try it without a real PR")
    st.caption("Builds a local repo with 3 intentionally broken functions - no GitHub account needed.")
    if st.button("Build mock PR", use_container_width=True):
        with st.spinner("Building mock repo..."):
            create_mock_pr.main()
        st.success(f"Mock repo ready at `local:{REPO_ROOT / 'mock' / 'mock_repo'}`")
    st.divider()
    if st.button("Clear history", use_container_width=True):
        st.session_state.reviews = []
        st.rerun()
    st.caption(f"{len(st.session_state.get('reviews', []))} review(s) this session")

st.title("🔍 PR Review Agent")
st.caption(
    "Paste a PR URL, or use the mock PR from the sidebar, to get an automated review. "
    "Each step below (cloning, diffing, analyzing, commenting) is a separate subagent."
)

if "reviews" not in st.session_state:
    st.session_state.reviews = []

for entry in st.session_state.reviews:
    st.chat_message("user", avatar="🔗").write(entry["pr_url"])
    st.chat_message("assistant", avatar="🔍").write(entry["result"])

if pr_url := st.chat_input("https://github.com/<owner>/<repo>/pull/<n>  or  local:./mock/mock_repo"):
    dry_run = pr_url.startswith("local:")
    st.chat_message("user", avatar="🔗").write(pr_url)

    with st.chat_message("assistant", avatar="🔍"):
        elapsed = None
        with st.status("Starting review...", expanded=True) as status:
            try:
                start = time.monotonic()
                result_text, _ = run_review(pr_url, dry_run, status)
                elapsed = time.monotonic() - start
                status.update(label=f"Review complete in {elapsed:.1f}s", state="complete")
            except Exception as e:
                result_text = f"Something went wrong while running the review: {e}"
                status.update(label="Review failed", state="error")
        st.write(result_text)
        if elapsed is not None:
            st.caption(f"Finished in {elapsed:.1f}s")

    st.session_state.reviews.append({"pr_url": pr_url, "result": result_text})
