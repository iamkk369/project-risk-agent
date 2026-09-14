"""
Deterministic, explainable risk scoring for issues.
No ML, no black box - every score comes with a reason.
"""


def calculate_risk(issue: dict, issue_lookup: dict) -> dict:
    """
    Given an enriched issue and a lookup of all issues by number,
    return a risk level (High/Medium/Low), a reason, and a recommended action.
    """
    blocks_count = len(issue.get("blocks", []))
    is_overdue = issue.get("is_overdue", False)
    days_overdue = issue.get("days_overdue", 0)
    days_until_due = issue.get("days_until_due")

    # How many of the issues this one blocks are still open?
    blocked_titles = []
    for blocked_num in issue.get("blocks", []):
        blocked_issue = issue_lookup.get(blocked_num)
        if blocked_issue:
            blocked_titles.append(f"#{blocked_num} \"{blocked_issue['title']}\"")

    if is_overdue and blocks_count > 0:
        level = "High"
        reason = (
            f"Overdue by {days_overdue} day(s) and blocks "
            f"{blocks_count} other issue(s): {', '.join(blocked_titles)}."
        )
        action = "Escalate immediately and confirm a revised completion estimate with the assignee."

    elif is_overdue and blocks_count == 0:
        level = "Medium"
        reason = f"Overdue by {days_overdue} day(s), but does not block other tracked work."
        action = "Follow up with the assignee on status and updated timeline."

    elif not is_overdue and blocks_count >= 2:
        level = "Medium"
        reason = (
            f"On track but blocks {blocks_count} other issues: {', '.join(blocked_titles)}. "
            "A delay here would cascade."
        )
        action = "Monitor closely; confirm completion plan before downstream work starts."

    elif not is_overdue and days_until_due is not None and days_until_due <= 2 and blocks_count > 0:
        level = "Medium"
        reason = (
            f"Due in {days_until_due} day(s) and blocks {blocks_count} issue(s): "
            f"{', '.join(blocked_titles)}."
        )
        action = "Confirm completion plan with owner before the deadline."

    else:
        level = "Low"
        reason = "On track with no significant downstream dependency risk."
        action = "No action needed."

    return {
        "level": level,
        "reason": reason,
        "action": action,
    }


def _risk_rank(level: str) -> int:
    return {"Low": 0, "Medium": 1, "High": 2}[level]


def _propagate_dependency_risks(
    issues: list[dict],
    issue_lookup: dict,
) -> None:
    """Propagate upstream bottleneck risk through all tracked dependency levels."""
    bottlenecks = {}

    for issue in issues:
        if issue.get("is_overdue"):
            bottlenecks[issue["number"]] = ("Medium", issue["number"], "overdue")
        elif issue["risk_level"] == "High":
            bottlenecks[issue["number"]] = ("High", issue["number"], "high")

    changed = True
    while changed:
        changed = False
        for issue in issues:
            source = bottlenecks.get(issue["number"])
            if source is None:
                continue

            source_level, origin_number, source_type = source
            for downstream_number in issue.get("blocks", []):
                downstream = issue_lookup.get(downstream_number)
                if downstream is None:
                    continue

                previous = bottlenecks.get(downstream_number)
                if previous is None or _risk_rank(source_level) > _risk_rank(previous[0]):
                    bottlenecks[downstream_number] = (
                        source_level,
                        origin_number,
                        source_type,
                    )
                    changed = True

    for issue in issues:
        bottleneck = bottlenecks.get(issue["number"])
        if bottleneck is None or issue["number"] == bottleneck[1]:
            continue

        level, origin_number, source_type = bottleneck
        if source_type == "overdue":
            propagation_reason = f"Blocked by overdue upstream task #{origin_number}."
        else:
            propagation_reason = f"Blocked by High Risk upstream task #{origin_number}."

        if propagation_reason not in issue["risk_reason"]:
            issue["risk_reason"] = f"{propagation_reason} {issue['risk_reason']}"

        if _risk_rank(level) > _risk_rank(issue["risk_level"]):
            issue["risk_level"] = level
            issue["recommended_action"] = (
                "Escalate the upstream bottleneck and confirm a recovery plan "
                "for this issue's dependent work."
            )


def analyze_all(issues: list[dict]) -> list[dict]:
    """Run risk analysis on every issue and attach the result."""
    issue_lookup = {issue["number"]: issue for issue in issues}

    for issue in issues:
        risk = calculate_risk(issue, issue_lookup)
        issue["risk_level"] = risk["level"]
        issue["risk_reason"] = risk["reason"]
        issue["recommended_action"] = risk["action"]

    _propagate_dependency_risks(issues, issue_lookup)
    return issues
