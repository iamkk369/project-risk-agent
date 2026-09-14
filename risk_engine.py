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


def analyze_all(issues: list[dict]) -> list[dict]:
    """Run risk analysis on every issue and attach the result."""
    issue_lookup = {issue["number"]: issue for issue in issues}

    for issue in issues:
        risk = calculate_risk(issue, issue_lookup)
        issue["risk_level"] = risk["level"]
        issue["risk_reason"] = risk["reason"]
        issue["recommended_action"] = risk["action"]

    return issues
