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
    impact_count = issue.get("downstream_count", blocks_count)
    critical_chain_length = issue.get("critical_chain_length", 0)
    is_overdue = issue.get("is_overdue", False)
    days_overdue = issue.get("days_overdue", 0)
    days_until_due = issue.get("days_until_due")

    # How many of the issues this one blocks are still open?
    blocked_titles = []
    for blocked_num in issue.get("blocks", []):
        blocked_issue = issue_lookup.get(blocked_num)
        if blocked_issue:
            blocked_titles.append(f"#{blocked_num} \"{blocked_issue['title']}\"")

    if is_overdue and impact_count > 0:
        level = "High"
        impact_detail = (
            f" directly blocks {blocks_count} issue(s)"
            if blocks_count
            else " has downstream impact"
        )
        reason = (
            f"Overdue by {days_overdue} day(s) and{impact_detail}; "
            f"{impact_count} downstream issue(s) are affected."
        )
        if blocked_titles:
            reason += f" Directly blocks: {', '.join(blocked_titles)}."
        if critical_chain_length > 1:
            reason += f" Dependency chain depth: {critical_chain_length} level(s)."
        action = "Escalate immediately and confirm a revised completion estimate with the assignee."

    elif is_overdue and impact_count == 0:
        level = "Medium"
        reason = f"Overdue by {days_overdue} day(s), but does not block other tracked work."
        action = "Follow up with the assignee on status and updated timeline."

    elif not is_overdue and impact_count >= 2:
        level = "Medium"
        reason = (
            f"On track but has downstream impact on {impact_count} issue(s). "
            "A delay here could cascade."
        )
        if blocked_titles:
            reason += f" Directly blocks: {', '.join(blocked_titles)}."
        if critical_chain_length > 1:
            reason += f" Dependency chain depth: {critical_chain_length} level(s)."
        action = "Monitor closely; confirm completion plan before downstream work starts."

    elif not is_overdue and days_until_due is not None and days_until_due <= 2 and impact_count > 0:
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


def _build_dependency_graph(issues: list[dict]) -> dict[int, set[int]]:
    """Build a normalized upstream -> downstream dependency graph.

    GitHub issue bodies may express either side of a relationship:
    ``#1 blocks #2`` or ``#2 blocked by #1``. Normalize both forms so
    dependency reasoning does not depend on which issue contains the text.
    """
    lookup = {issue["number"]: issue for issue in issues}
    graph = {number: set() for number in lookup}

    for issue in issues:
        number = issue["number"]
        for downstream in issue.get("blocks", []):
            if downstream in lookup and downstream != number:
                graph[number].add(downstream)
        for upstream in issue.get("blocked_by", []):
            if upstream in lookup and upstream != number:
                graph[upstream].add(number)

    return graph


def _graph_metrics(issues: list[dict], graph: dict[int, set[int]]) -> None:
    """Attach direct and transitive dependency-impact metrics to issues."""
    def downstream_nodes(start: int) -> set[int]:
        seen = set()
        stack = list(graph.get(start, ()))
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            stack.extend(graph.get(node, ()))
        return seen

    def max_depth(start: int, visiting: set[int] | None = None) -> int:
        visiting = set() if visiting is None else visiting
        if start in visiting:
            return 0  # Defensive handling for dependency cycles.
        children = graph.get(start, set())
        if not children:
            return 1
        visiting.add(start)
        depth = 1 + max((max_depth(child, visiting) for child in children), default=0)
        visiting.remove(start)
        return depth

    lookup = {issue["number"]: issue for issue in issues}
    for issue in issues:
        number = issue["number"]
        affected = downstream_nodes(number)
        issue["downstream_count"] = len(affected)
        issue["downstream_issues"] = sorted(affected)
        issue["critical_chain_length"] = max_depth(number)

        upstream = set(issue.get("blocked_by", []))
        for candidate, downstream in graph.items():
            if number in downstream:
                upstream.add(candidate)
        issue["upstream_blockers"] = sorted(n for n in upstream if n in lookup)


def _propagate_dependency_risks(
    issues: list[dict],
    issue_lookup: dict,
) -> None:
    """Propagate the strongest upstream risk through the normalized graph."""
    graph = _build_dependency_graph(issues)
    bottlenecks = {}

    for issue in issues:
        if issue.get("risk_level") == "High":
            bottlenecks[issue["number"]] = ("High", issue["number"], "high")
        elif issue.get("is_overdue"):
            bottlenecks[issue["number"]] = ("Medium", issue["number"], "overdue")

    changed = True
    while changed:
        changed = False
        for number, downstream_numbers in graph.items():
            source = bottlenecks.get(number)
            if source is None:
                continue
            source_level, origin_number, source_type = source
            for downstream_number in downstream_numbers:
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
    graph = _build_dependency_graph(issues)
    _graph_metrics(issues, graph)

    for issue in issues:
        risk = calculate_risk(issue, issue_lookup)
        issue["risk_level"] = risk["level"]
        issue["risk_reason"] = risk["reason"]
        issue["recommended_action"] = risk["action"]

    _propagate_dependency_risks(issues, issue_lookup)
    return issues


def summarize_project_risk(issues: list[dict]) -> dict:
    """Build a deterministic project-level risk summary from analyzed issues."""
    total = len(issues)
    counts = {level: sum(1 for i in issues if i.get("risk_level") == level)
              for level in ("High", "Medium", "Low")}

    high = [i for i in issues if i.get("risk_level") == "High"]
    medium = [i for i in issues if i.get("risk_level") == "Medium"]
    risky = high + medium

    bottleneck_candidates = [i for i in risky if i.get("downstream_count", 0) > 0]
    bottleneck = max(
        bottleneck_candidates,
        key=lambda i: (
            _risk_rank(i.get("risk_level", "Low")),
            i.get("downstream_count", 0),
            i.get("critical_chain_length", 0),
        ),
        default=None,
    )

    affected = sorted({
        downstream
        for issue in risky
        for downstream in issue.get("downstream_issues", [])
    })

    if high:
        posture = "Critical"
    elif medium:
        posture = "Watch"
    else:
        posture = "On Track"

    return {
        "posture": posture,
        "total_issues": total,
        "high_count": counts["High"],
        "medium_count": counts["Medium"],
        "low_count": counts["Low"],
        "risk_count": len(risky),
        "affected_issue_count": len(affected),
        "affected_issue_numbers": affected,
        "bottleneck_issue": bottleneck["number"] if bottleneck else None,
        "bottleneck_title": bottleneck["title"] if bottleneck else None,
        "bottleneck_downstream_count": bottleneck.get("downstream_count", 0) if bottleneck else 0,
        "bottleneck_chain_length": bottleneck.get("critical_chain_length", 0) if bottleneck else 0,
    }
