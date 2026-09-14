"""Local historical snapshots and risk-trajectory analysis.

The history layer is intentionally provider-neutral: it can later be backed by
S3 or another durable store without changing the risk engine.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

RISK_RANK = {"Low": 0, "Medium": 1, "High": 2}


def _snapshot(issues: list[dict], project_summary: dict) -> dict:
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "project": {
            "posture": project_summary["posture"],
            "risk_count": project_summary["risk_count"],
            "affected_issue_count": project_summary["affected_issue_count"],
            "bottleneck_issue": project_summary["bottleneck_issue"],
        },
        "issues": {
            str(issue["number"]): {
                "title": issue["title"],
                "risk_level": issue["risk_level"],
                "downstream_count": issue.get("downstream_count", 0),
                "critical_chain_length": issue.get("critical_chain_length", 0),
            }
            for issue in issues
        },
    }


def load_latest_history(path: str | Path) -> dict | None:
    history_path = Path(path)
    if not history_path.exists():
        return None
    try:
        data = json.loads(history_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    snapshots = data.get("snapshots", [])
    return snapshots[-1] if snapshots else None


def compare_history(previous: dict | None, issues: list[dict], project_summary: dict) -> dict:
    """Compare current risk state with the previous successful snapshot."""
    current = _snapshot(issues, project_summary)
    if previous is None:
        return {
            "baseline": True,
            "previous_timestamp": None,
            "project_change": "Baseline — no previous analysis available.",
            "new_risks": [],
            "escalating": [],
            "deescalating": [],
            "persistent": [],
            "resolved": [],
            "current_snapshot": current,
        }

    old_issues = previous.get("issues", {})
    current_issues = current["issues"]
    new_risks, escalating, deescalating, persistent, resolved = [], [], [], [], []

    for number, current_issue in current_issues.items():
        old_issue = old_issues.get(number)
        current_rank = RISK_RANK[current_issue["risk_level"]]
        if old_issue is None:
            if current_rank > 0:
                new_risks.append(number)
            continue
        old_rank = RISK_RANK.get(old_issue.get("risk_level", "Low"), 0)
        if current_rank > old_rank:
            escalating.append(number)
        elif current_rank < old_rank:
            deescalating.append(number)
        elif current_rank > 0:
            persistent.append(number)

    for number, old_issue in old_issues.items():
        if number not in current_issues and RISK_RANK.get(old_issue.get("risk_level", "Low"), 0) > 0:
            resolved.append(number)

    old_posture = previous.get("project", {}).get("posture")
    new_posture = current["project"]["posture"]
    project_change = f"{old_posture} → {new_posture}" if old_posture != new_posture else f"Unchanged: {new_posture}"

    return {
        "baseline": False,
        "previous_timestamp": previous.get("timestamp"),
        "project_change": project_change,
        "new_risks": sorted(new_risks, key=int),
        "escalating": sorted(escalating, key=int),
        "deescalating": sorted(deescalating, key=int),
        "persistent": sorted(persistent, key=int),
        "resolved": sorted(resolved, key=int),
        "current_snapshot": current,
    }


def save_snapshot(path: str | Path, snapshot: dict, max_snapshots: int = 30) -> None:
    """Append a successful snapshot and retain a bounded local history."""
    history_path = Path(path)
    try:
        history_path.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads(history_path.read_text(encoding="utf-8")) if history_path.exists() else {}
    except (OSError, json.JSONDecodeError):
        data = {}
    snapshots = data.get("snapshots", [])
    snapshots.append(snapshot)
    data["snapshots"] = snapshots[-max_snapshots:]
    history_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
