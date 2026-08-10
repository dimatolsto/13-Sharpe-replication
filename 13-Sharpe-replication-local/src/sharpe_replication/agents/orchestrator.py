"""Optional OpenAI Agents SDK supervisor.

The LLM layer never computes returns. It can only inspect deterministic summaries and request named
experiments. Importing this module requires the optional `agent` dependency.
"""

from __future__ import annotations

import json
from pathlib import Path

try:
    from agents import Agent, Runner, function_tool
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Install with: uv sync --extra agent") from exc


@function_tool
def read_experiment_summary(experiment_id: str, reports_dir: str = "reports/generated") -> str:
    """Read a deterministic experiment summary JSON by experiment ID (P0-P5)."""
    allowed = {"P0", "P1", "P2", "P3", "P4", "P5"}
    if experiment_id not in allowed:
        raise ValueError(f"experiment_id must be one of {sorted(allowed)}")
    path = Path(reports_dir) / f"{experiment_id}_summary.json"
    if not path.exists():
        return json.dumps({"status": "missing", "experiment": experiment_id})
    return path.read_text(encoding="utf-8")


METHODOLOGY_AUDITOR = Agent(
    name="Methodology Auditor",
    instructions=(
        "Review deterministic P0-P5 summaries for methodological validity. Never propose parameter "
        "changes. Focus on timing, survivorship, corporate actions, data provenance, and accounting."
    ),
    tools=[read_experiment_summary],
)

RED_TEAM = Agent(
    name="Red Team",
    instructions=(
        "Try to invalidate the replication by finding leakage or certification failures. Never modify "
        "strategy parameters or calculate returns yourself."
    ),
    tools=[read_experiment_summary],
)

ORCHESTRATOR = Agent(
    name="Replication Orchestrator",
    instructions=Path("docs/prompt.md").read_text(encoding="utf-8") if Path("docs/prompt.md").exists() else (
        "Supervise forensic replication. Never optimize parameters."
    ),
    tools=[
        read_experiment_summary,
        METHODOLOGY_AUDITOR.as_tool(tool_name="methodology_audit", tool_description="Audit replication methodology."),
        RED_TEAM.as_tool(tool_name="red_team", tool_description="Attempt to invalidate the replication."),
    ],
)


def run_agent(question: str) -> str:
    result = Runner.run_sync(ORCHESTRATOR, question)
    return str(result.final_output)
