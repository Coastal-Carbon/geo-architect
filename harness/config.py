"""Paths, constants, and helpers for the librarian test harness."""

from __future__ import annotations

import json
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# The librarian project — tests are spawned from here so Claude Code
# auto-discovers the geospatial-librarian subagent.
LIBRARIAN_PROJECT_DIR = Path("/Users/thomasstorwick/Documents/Geospatial Libraian")

# The geo-architect venv used for Python execution inside test sessions.
GEO_ARCHITECT_VENV = Path("/Users/thomasstorwick/Documents/geo-architect/.venv")

# Harness root (this package).
HARNESS_DIR = Path(__file__).resolve().parent

# Scenarios file
SCENARIOS_FILE = HARNESS_DIR / "scenarios.yaml"

# Prompt templates
QUERYING_AGENT_PROMPT = HARNESS_DIR / "prompts" / "querying_agent.md"
JUDGE_PROMPT = HARNESS_DIR / "prompts" / "judge.md"

# Results directory (gitignored, created at runtime)
RESULTS_DIR = HARNESS_DIR / "results"

# ---------------------------------------------------------------------------
# Test geometry defaults
# ---------------------------------------------------------------------------

DEFAULT_LAT = 37.76
DEFAULT_LON = -122.43
DEFAULT_BBOX = [-122.45, 37.74, -122.41, 37.78]  # small SF bbox
DEFAULT_DATE_START = "2023-06-01"
DEFAULT_DATE_END = "2023-09-01"
DEFAULT_H3_CELL = "882ab2590bfffff"

# ---------------------------------------------------------------------------
# Outcome categories
# ---------------------------------------------------------------------------

OUTCOME_SUCCESS = "SUCCESS"
OUTCOME_AUTH_FAILURE = "AUTH_FAILURE"
OUTCOME_NO_DATA = "NO_DATA"
OUTCOME_IMPORT_ERROR = "IMPORT_ERROR"
OUTCOME_EXECUTION_ERROR = "EXECUTION_ERROR"
OUTCOME_TIMEOUT = "TIMEOUT"

VALID_OUTCOMES = {
    OUTCOME_SUCCESS,
    OUTCOME_AUTH_FAILURE,
    OUTCOME_NO_DATA,
    OUTCOME_IMPORT_ERROR,
    OUTCOME_EXECUTION_ERROR,
    OUTCOME_TIMEOUT,
}

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_PARALLEL = 3

# Commercial datasets (expected failures)
COMMERCIAL_DATASET_IDS = {
    "capella",
    "umbra",
    "pleiades",
    "skysat",
    "superdove",
    "spot-ms",
    "worldview",
    "wyvern",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def extract_conversation_text(stream_json_path: Path) -> str:
    """Extract a compact text summary from a Claude Code stream-json log.

    This is the lightweight version used internally by the harness for
    quick ``conversation.md`` files.  For a richer human-readable
    transcript, use :func:`generate_transcript`.
    """
    lines: list[str] = []

    with open(stream_json_path) as f:
        for raw_line in f:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                obj = json.loads(raw_line)
            except json.JSONDecodeError:
                continue

            msg_type = obj.get("type")

            if msg_type == "assistant":
                message = obj.get("message", {})
                for block in message.get("content", []):
                    if block.get("type") == "text":
                        lines.append(f"**Assistant:**\n{block['text']}\n")
                    elif block.get("type") == "tool_use":
                        tool_name = block.get("name", "unknown")
                        tool_input = block.get("input", {})
                        if tool_name == "Bash":
                            cmd = tool_input.get("command", "")
                            lines.append(f"**Tool: Bash**\n```\n{cmd}\n```\n")
                        elif tool_name == "Read":
                            fp = tool_input.get("file_path", "")
                            lines.append(f"**Tool: Read** `{fp}`\n")
                        elif tool_name == "Task":
                            prompt = tool_input.get("prompt", "")[:200]
                            lines.append(f"**Tool: Task** (subagent)\n> {prompt}...\n")
                        else:
                            lines.append(f"**Tool: {tool_name}**\n")

            elif msg_type == "result":
                result_text = obj.get("result", "")
                if result_text:
                    if len(result_text) > 2000:
                        result_text = result_text[:2000] + "\n... [truncated]"
                    lines.append(f"**Result:**\n```\n{result_text}\n```\n")

    return "\n".join(lines)


# Max chars to show for tool results and thinking blocks in transcripts
_TOOL_RESULT_MAX = 3000
_THINKING_MAX = 2000


def generate_transcript(
    stream_json_path: Path,
    *,
    dataset_name: str = "",
    dataset_id: str = "",
    outcome: str = "",
    elapsed: float | None = None,
) -> str:
    """Build a rich, human-readable markdown transcript from stream-json.

    Produces collapsible ``<details>`` blocks for tool results and
    thinking, clear turn numbers, and formatted tool calls with context.

    Parameters
    ----------
    stream_json_path:
        Path to the ``.jsonl`` stream-json file produced by ``claude -p``.
    dataset_name:
        Human name for the header.
    dataset_id:
        Dataset id for the header.
    outcome:
        Test outcome string (SUCCESS, IMPORT_ERROR, etc.).
    elapsed:
        Elapsed time in seconds.

    Returns
    -------
    str
        Markdown-formatted transcript.
    """
    with open(stream_json_path) as f:
        objects = [
            json.loads(line)
            for line in f
            if line.strip()
        ]

    md: list[str] = []

    # Header
    title = dataset_name or dataset_id or "Test"
    md.append(f"# {title} — Test Transcript")
    md.append("")
    meta_parts = []
    if outcome:
        meta_parts.append(f"**Outcome:** {outcome}")
    if elapsed is not None:
        meta_parts.append(f"**Duration:** {elapsed:.0f}s")
    if meta_parts:
        md.append(" | ".join(meta_parts))
        md.append("")
    md.append("---")
    md.append("")

    turn = 0

    for obj in objects:
        msg_type = obj.get("type", "")

        if msg_type in ("system", "rate_limit_event"):
            continue

        # User messages are tool results in -p mode
        if msg_type == "user":
            content = obj.get("message", {}).get("content", [])
            if isinstance(content, list):
                for block in content:
                    if block.get("type") == "tool_result":
                        result_content = block.get("content", "")
                        if isinstance(result_content, list):
                            texts = [
                                b.get("text", "")
                                for b in result_content
                                if b.get("type") == "text"
                            ]
                            result_content = "\n".join(texts)
                        if isinstance(result_content, str) and result_content.strip():
                            truncated = result_content[:_TOOL_RESULT_MAX]
                            if len(result_content) > _TOOL_RESULT_MAX:
                                truncated += "\n\n... [truncated]"
                            md.append("<details>")
                            md.append(
                                f"<summary>Tool Result ({len(result_content)} chars)</summary>"
                            )
                            md.append("")
                            md.append("```")
                            md.append(truncated)
                            md.append("```")
                            md.append("</details>")
                            md.append("")
            continue

        if msg_type == "assistant":
            content = obj.get("message", {}).get("content", [])

            for block in content:
                btype = block.get("type")

                if btype == "text":
                    text = block.get("text", "").strip()
                    if text:
                        turn += 1
                        md.append(f"## Agent (turn {turn})")
                        md.append("")
                        md.append(text)
                        md.append("")

                elif btype == "thinking":
                    thinking = block.get("thinking", "").strip()
                    if thinking:
                        truncated = thinking[:_THINKING_MAX]
                        if len(thinking) > _THINKING_MAX:
                            truncated += "\n\n... [truncated]"
                        md.append("<details>")
                        md.append(
                            f"<summary>Thinking ({len(thinking)} chars)</summary>"
                        )
                        md.append("")
                        md.append(f"_{truncated}_")
                        md.append("")
                        md.append("</details>")
                        md.append("")

                elif btype == "tool_use":
                    name = block.get("name", "unknown")
                    inp = block.get("input", {})

                    if name == "Bash":
                        cmd = inp.get("command", "")
                        desc = inp.get("description", "")
                        header = f"**Bash** — {desc}" if desc else "**Bash**"
                        md.append(f"### {header}")
                        md.append("")
                        md.append("```bash")
                        md.append(cmd)
                        md.append("```")
                        md.append("")

                    elif name == "Read":
                        fp = inp.get("file_path", "")
                        short = fp.rsplit("/", 1)[-1] if "/" in fp else fp
                        md.append(f"### **Read** `{short}`")
                        md.append(f"> `{fp}`")
                        md.append("")

                    elif name == "Write":
                        fp = inp.get("file_path", "")
                        short = fp.rsplit("/", 1)[-1] if "/" in fp else fp
                        content_text = inp.get("content", "")
                        md.append(f"### **Write** `{short}`")
                        md.append(f"> `{fp}`")
                        md.append("")
                        if content_text:
                            preview = content_text[:500]
                            md.append("```")
                            md.append(preview)
                            if len(content_text) > 500:
                                md.append("... [truncated]")
                            md.append("```")
                            md.append("")

                    elif name == "Task":
                        prompt = inp.get("prompt", "")
                        subagent = inp.get("subagent_type", "?")
                        desc = inp.get("description", "")
                        md.append(
                            f"### **Task** — {desc} (subagent: {subagent})"
                        )
                        md.append("")
                        md.append(f"> {prompt[:500]}")
                        if len(prompt) > 500:
                            md.append("> ... [truncated]")
                        md.append("")

                    elif name == "Glob":
                        pattern = inp.get("pattern", "")
                        md.append(f"### **Glob** `{pattern}`")
                        md.append("")

                    elif name == "Grep":
                        pattern = inp.get("pattern", "")
                        md.append(f"### **Grep** `{pattern}`")
                        md.append("")

                    elif name == "TodoWrite":
                        md.append("### **TodoWrite** _(internal task tracking)_")
                        md.append("")

                    else:
                        md.append(f"### **{name}**")
                        md.append(f"> {json.dumps(inp)[:300]}")
                        md.append("")

        elif msg_type == "result":
            result_text = obj.get("result", "")
            md.append("---")
            md.append("")
            md.append("## Final Result")
            md.append("")
            md.append(result_text)
            md.append("")

    return "\n".join(md)


def parse_outcome_from_status(status_path: Path) -> str | None:
    """Read the outcome field from a status.json file."""
    if not status_path.exists():
        return None
    try:
        data = json.loads(status_path.read_text())
        return data.get("outcome")
    except (json.JSONDecodeError, OSError):
        return None
