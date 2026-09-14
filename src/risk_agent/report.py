"""
Formats analyzed issues into a readable, prioritized Markdown report.
"""
from datetime import date
from .risk_engine import summarize_project_risk


def _trajectory_section(trajectory: dict, issues: list[dict]) -> list[str]:
    if trajectory["baseline"]:
        return ["## Risk Trajectory\n", "- **Status:** Baseline — this is the first recorded analysis.\n"]
    lookup = {str(i["number"]): i for i in issues}
    lines = ["## Risk Trajectory\n", f"- **Project change:** {trajectory["project_change"]}"]
    lines.append(f"- **New risks:** {len(trajectory["new_risks"])}")
    lines.append(f"- **Escalating:** {len(trajectory["escalating"])}")
    lines.append(f"- **De-escalating:** {len(trajectory["deescalating"])}")
    lines.append(f"- **Persistent risks:** {len(trajectory["persistent"])}")
    lines.append(f"- **Resolved risks:** {len(trajectory["resolved"])}")
    groups = [("Escalating", trajectory["escalating"]), ("New", trajectory["new_risks"]), ("Resolved", trajectory["resolved"])]
    for label, numbers in groups:
        if numbers:
            rendered = [f"#{n} — {lookup[n]["title"]}" for n in numbers if n in lookup]
            if label == "Resolved":
                rendered = [f"#{n}" for n in numbers]
            lines.append(f"- **{label}:** {", ".join(rendered)}")
    lines.append("")
    return lines

RISK_EMOJI = {
    "High": "🔴",
    "Medium": "🟡",
    "Low": "🟢",
}


def generate_report(issues: list[dict], repo: str, trajectory: dict | None = None) -> str:
    high = [i for i in issues if i["risk_level"] == "High"]
    medium = [i for i in issues if i["risk_level"] == "Medium"]
    low = [i for i in issues if i["risk_level"] == "Low"]

    lines = []
    lines.append(f"# Project Risk Report — {repo}")
    lines.append(f"**Date:** {date.today().isoformat()}\n")
    summary = summarize_project_risk(issues)
    lines.append(f"🔴 {len(high)} High Risk &nbsp;&nbsp; 🟡 {len(medium)} Medium Risk &nbsp;&nbsp; 🟢 {len(low)} On Track\n")
    lines.append("## Project Health\n")
    lines.append(f"- **Posture:** {summary['posture']}")
    lines.append(f"- **Risk-bearing issues:** {summary['risk_count']} of {summary['total_issues']}")
    lines.append(f"- **Downstream issues exposed by risk:** {summary['affected_issue_count']}")
    if summary["bottleneck_issue"] is not None:
        lines.append(
            f"- **Primary bottleneck:** #{summary['bottleneck_issue']} — "
            f"{summary['bottleneck_title']} "
            f"({summary['bottleneck_downstream_count']} downstream, "
            f"chain depth {summary['bottleneck_chain_length']})"
        )
    focus = summary.get("recommended_focus_issue")
    if focus is not None:
        focus_issue = next((i for i in issues if i["number"] == focus), None)
        if focus_issue:
            lines.append("## Decision Focus\n")
            lines.append(f"- **First priority:** #{focus_issue['number']} — {focus_issue['title']}")
            lines.append(f"- **Urgency:** {focus_issue.get('urgency', 'Unknown')}")
            lines.append(f"- **Impact:** {focus_issue.get('impact_summary', 'Not available')}")
            lines.append(f"- **Why first:** {summary.get('recommended_focus_reason', '')}")
            lines.append(f"- **Decision:** {focus_issue.get('decision', focus_issue.get('recommended_action', ''))}\n")
    if trajectory is not None:
        lines.extend(_trajectory_section(trajectory, issues))
    lines.append("\n---\n")

    for group, label in [(high, "HIGH RISK"), (medium, "MEDIUM RISK")]:
        if not group:
            continue
        lines.append(f"## {RISK_EMOJI[group[0]['risk_level']]} {label}\n")
        for issue in group:
            lines.append(f"### #{issue['number']} — {issue['title']}")
            due_str = issue["due_date"] or "no due date"
            lines.append(f"- **Due:** {due_str}")
            lines.append(f"- **Downstream impact:** {issue.get('downstream_count', 0)} issue(s)")
            if issue.get("critical_chain_length", 1) > 1:
                lines.append(f"- **Dependency chain depth:** {issue['critical_chain_length']} level(s)")
            lines.append(f"- **Why it matters:** {issue['risk_reason']}")
            lines.append(f"- **Potential consequence:** {issue.get('potential_consequence', 'Not available')}")
            lines.append(f"- **Decision priority:** {issue.get('decision_priority', '—')}")
            lines.append(f"- **Recommended action:** {issue.get('decision', issue['recommended_action'])}")
            lines.append(f"- [View issue]({issue['url']})\n")
        lines.append("---\n")

    if low:
        lines.append(f"## 🟢 ON TRACK\n")
        lines.append(f"{len(low)} issue(s) currently show no significant delivery risk:\n")
        for issue in low:
            lines.append(f"- #{issue['number']} {issue['title']}")

    return "\n".join(lines)
