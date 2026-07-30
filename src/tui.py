import time
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.pretty import Pretty

console = Console(force_terminal=True, legacy_windows=False)


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def print_banner(title: str) -> None:
    console.print(Panel(title, style="bold cyan"))


def print_tool_call(name: str, args: dict) -> None:
    console.print(Panel(Pretty(args), title=f"[{_ts()}] tool call: {name}", border_style="yellow"))


def print_tool_output(name: str, content) -> None:
    style = "green"
    text = str(content)
    if '"success": false' in text.lower():
        style = "bold red"
    console.print(Panel(text, title=f"[{_ts()}] tool output: {name}", border_style=style))

def print_subagent_start(name: str) -> None:
    console.print(Panel(f"delegate to skill '{name}'", title=f"[{_ts()}] subagent", border_style="magenta"))


def print_thought(content: str) -> None:
    console.print(Panel(content, title=f"[{_ts()}] thinking", border_style="blue"))


def stream_and_print(agent, inputs, config=None):
    start = time.monotonic()
    console.print(f"[{_ts()}] stream opened, waiting for first model response...", style="dim")
    final_state = None
    try:
        for chunk in agent.stream(inputs, config=config, stream_mode="values"):
            final_state = chunk
            last_message = chunk["messages"][-1]

            tool_calls = getattr(last_message, "tool_calls", None)
            if tool_calls:
                for tc in tool_calls:
                    if tc["name"] == "task":
                        print_subagent_start(tc["args"].get("subagent_type", "unknown"))
                    else:
                        print_tool_call(tc["name"], tc["args"])
            elif getattr(last_message, "type", "") == "tool":
                print_tool_output(getattr(last_message, "name", "tool"), last_message.content)
            elif getattr(last_message, "type", "") == "ai" and last_message.content:
                print_thought(last_message.content)
    except Exception as e:
        elapsed = time.monotonic() - start
        console.print(f"[{_ts()}] (+{elapsed:.1f}s) stream stopped with error: {type(e).__name__}: {e}", style="bold red")
        raise

    elapsed = time.monotonic() - start
    console.print(f"[{_ts()}] stream closed after {elapsed:.1f}s total", style="dim")
    return final_state
