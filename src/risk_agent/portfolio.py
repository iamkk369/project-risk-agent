"""Portfolio-level analysis across multiple GitHub repositories.

The portfolio layer deliberately reuses the existing per-project risk pipeline.
It aggregates project posture and bottleneck information without mixing issue
numbers between repositories.
"""
from __future__ import annotations

from collections import Counter
from typing import Callable


PostureRank = {"On Track": 0, "Watch": 1, "Critical": 2}


def parse_project_repos(value: str | None) -> list[str]:
    """Parse a comma/newline-separated repository list and remove duplicates."""
    if not value:
        return []
    repos: list[str] = []
    seen: set[str] = set()
    for raw in value.replace("\n", ",").split(","):
        repo = raw.strip()
        if repo and repo not in seen:
            repos.append(repo)
            seen.add(repo)
    return repos


def summarize_portfolio(results: list[dict]) -> dict:
    """Aggregate independent project summaries into a portfolio summary."""
    counts = Counter(result["summary"]["posture"] for result in results)
    critical = [r for r in results if r["summary"]["posture"] == "Critical"]
    watch = [r for r in results if r["summary"]["posture"] == "Watch"]
    attention = critical + watch
    attention.sort(
        key=lambda r: (
            PostureRank[r["summary"]["posture"]],
            r["summary"].get("high_count", 0),
            r["summary"].get("affected_issue_count", 0),
            r["summary"].get("bottleneck_downstream_count", 0),
        ),
        reverse=True,
    )
    overall = "Critical" if critical else "Watch" if watch else "On Track"
    return {
        "project_count": len(results),
        "critical_projects": counts["Critical"],
        "watch_projects": counts["Watch"],
        "on_track_projects": counts["On Track"],
        "overall_posture": overall,
        "priority_projects": [r["repo"] for r in attention],
    }


def generate_portfolio_report(results: list[dict]) -> str:
    """Render a concise cross-project management report."""
    portfolio = summarize_portfolio(results)
    lines = [
        "# Project Risk Portfolio Report",
        f"**Projects analyzed:** {portfolio['project_count']}",
        f"**Overall posture:** {portfolio['overall_posture']}",
        (
            f"**Portfolio breakdown:** {portfolio['critical_projects']} Critical, "
            f"{portfolio['watch_projects']} Watch, "
            f"{portfolio['on_track_projects']} On Track"
        ),
        "",
        "## Priority Projects",
        "",
    ]
    if not portfolio["priority_projects"]:
        lines.append("All analyzed projects are currently On Track.")
    else:
        for repo in portfolio["priority_projects"]:
            result = next(r for r in results if r["repo"] == repo)
            summary = result["summary"]
            bottleneck = summary.get("bottleneck_issue")
            bottleneck_text = (
                f"#{bottleneck} — {summary.get('bottleneck_title')}"
                if bottleneck is not None else "none"
            )
            lines.append(
                f"- **{repo}** — {summary['posture']}; "
                f"{summary['high_count']} High / {summary['medium_count']} Medium; "
                f"bottleneck: {bottleneck_text}"
            )
    lines.extend(["", "## Project Summaries", ""])
    for result in results:
        summary = result["summary"]
        lines.append(f"### {result['repo']}")
        lines.append(f"- **Posture:** {summary['posture']}")
        lines.append(f"- **Risk-bearing issues:** {summary['risk_count']} of {summary['total_issues']}")
        lines.append(f"- **Downstream exposure:** {summary['affected_issue_count']}")
        if summary.get("bottleneck_issue") is not None:
            lines.append(
                f"- **Primary bottleneck:** #{summary['bottleneck_issue']} — "
                f"{summary['bottleneck_title']}"
            )
        lines.append("")
    return "\n".join(lines)


def analyze_portfolio(
    repos: list[str],
    analyze_project: Callable[[str], dict],
) -> tuple[list[dict], dict, str]:
    """Analyze repositories independently and aggregate their project summaries."""
    if not repos:
        raise ValueError("At least one repository is required for portfolio analysis.")
    results = []
    for repo in repos:
        result = analyze_project(repo)
        results.append({"repo": repo, **result})
    summary = summarize_portfolio(results)
    return results, summary, generate_portfolio_report(results)
