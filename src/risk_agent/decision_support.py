"""Deterministic decision-support signals for project-risk findings.

This layer turns risk findings into manager-oriented decisions without replacing
Strands/Gemini reasoning. The signals are explainable and derived only from the
already analyzed project state.
"""
from .risk_engine import _risk_rank


def enrich_decision_support(issues: list[dict], project_summary: dict) -> list[dict]:
    """Attach urgency, impact, consequence and decision priority to each issue."""
    risky = [i for i in issues if i.get("risk_level") in {"High", "Medium"}]

    def priority_key(issue: dict) -> tuple:
        return (
            _risk_rank(issue.get("risk_level", "Low")),
            1 if issue.get("is_overdue") else 0,
            issue.get("downstream_count", 0),
            issue.get("critical_chain_length", 0),
            -(issue.get("days_until_due") if issue.get("days_until_due") is not None else 999999),
            -issue.get("number", 0),
        )

    ordered = sorted(risky, key=priority_key, reverse=True)
    priority_map = {issue["number"]: index + 1 for index, issue in enumerate(ordered)}

    for issue in issues:
        level = issue.get("risk_level", "Low")
        downstream = issue.get("downstream_count", 0)
        chain = issue.get("critical_chain_length", 1)
        overdue = issue.get("is_overdue", False)
        upstream_blockers = issue.get("upstream_blockers", [])

        if level == "High":
            urgency = "Immediate"
            consequence = (
                f"Ignoring this risk can delay {downstream} downstream issue(s)"
                if downstream else "Ignoring this risk can allow the delivery delay to persist"
            )
        elif level == "Medium":
            urgency = "Near-term"
            consequence = (
                f"A delay could cascade into {downstream} downstream issue(s)"
                if downstream else "Further delay could turn this into a higher-severity risk"
            )
        else:
            urgency = "Routine"
            consequence = "No significant delivery consequence is currently indicated."

        if overdue:
            decision = "Escalate and establish a recovery plan."
        elif upstream_blockers and level == "High":
            blocker_text = ", ".join(f"#{n}" for n in upstream_blockers[:3])
            decision = (
                f"Intervene on the upstream blocker {blocker_text}; this issue is exposed by"
                " dependency risk and should be resolved through that upstream intervention point."
            )
        elif downstream >= 2:
            decision = "Protect the dependency chain and confirm the completion plan."
        elif level == "Medium":
            decision = "Confirm ownership and the next milestone before the risk increases."
        else:
            decision = "No intervention required; continue normal tracking."

        issue["decision_priority"] = priority_map.get(issue["number"])
        issue["urgency"] = urgency
        issue["potential_consequence"] = consequence
        issue["decision"] = decision
        issue["impact_summary"] = (
            f"{downstream} downstream issue(s), dependency chain depth {chain}."
        )

    project_summary["recommended_focus_order"] = [issue["number"] for issue in ordered]
    if ordered:
        top = ordered[0]
        project_summary["recommended_focus_issue"] = top["number"]
        project_summary["recommended_focus_reason"] = (
            f"#{top['number']} is the highest-priority risk based on severity, "
            "deadline pressure, and downstream dependency impact."
        )
    else:
        project_summary["recommended_focus_issue"] = None
        project_summary["recommended_focus_reason"] = "No intervention is currently indicated."

    return issues
