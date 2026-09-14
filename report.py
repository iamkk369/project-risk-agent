"""
Formats analyzed issues into a readable, prioritized Markdown report.
"""
from datetime import date

RISK_EMOJI = {
    "High": "🔴",
    "Medium": "🟡",
    "Low": "🟢",
}


def generate_report(issues: list[dict], repo: str) -> str:
    high = [i for i in issues if i["risk_level"] == "High"]
    medium = [i for i in issues if i["risk_level"] == "Medium"]
    low = [i for i in issues if i["risk_level"] == "Low"]

    lines = []
    lines.append(f"# Project Risk Report — {repo}")
    lines.append(f"**Date:** {date.today().isoformat()}\n")
    lines.append(f"🔴 {len(high)} High Risk &nbsp;&nbsp; 🟡 {len(medium)} Medium Risk &nbsp;&nbsp; 🟢 {len(low)} On Track\n")
    lines.append("---\n")

    for group, label in [(high, "HIGH RISK"), (medium, "MEDIUM RISK")]:
        if not group:
            continue
        lines.append(f"## {RISK_EMOJI[group[0]['risk_level']]} {label}\n")
        for issue in group:
            lines.append(f"### #{issue['number']} — {issue['title']}")
            due_str = issue["due_date"] or "no due date"
            lines.append(f"- **Due:** {due_str}")
            lines.append(f"- **Why it matters:** {issue['risk_reason']}")
            lines.append(f"- **Recommended action:** {issue['recommended_action']}")
            lines.append(f"- [View issue]({issue['url']})\n")
        lines.append("---\n")

    if low:
        lines.append(f"## 🟢 ON TRACK\n")
        lines.append(f"{len(low)} issue(s) currently show no significant delivery risk:\n")
        for issue in low:
            lines.append(f"- #{issue['number']} {issue['title']}")

    return "\n".join(lines)
