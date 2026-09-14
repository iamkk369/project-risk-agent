"""
GitHub Issues fetcher - pulls open issues with their body text
so we can later parse due dates and dependencies out of them.
"""
import os
import re
import requests
from datetime import datetime, date

GITHUB_API = "https://api.github.com"


def fetch_open_issues(repo: str, token: str) -> list[dict]:
    """
    Fetch all open issues (not PRs) from a GitHub repo.
    Returns a list of dicts with number, title, body, and raw due date text.
    """
    url = f"{GITHUB_API}/repos/{repo}/issues"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }
    params = {"state": "open", "per_page": 100}

    response = requests.get(url, headers=headers, params=params, timeout=15)
    response.raise_for_status()

    raw_issues = response.json()

    issues = []
    for item in raw_issues:
        # Skip pull requests - GitHub's issues endpoint includes PRs too
        if "pull_request" in item:
            continue

        body = item.get("body") or ""
        issues.append({
            "number": item["number"],
            "title": item["title"],
            "body": body,
            "url": item["html_url"],
        })

    return issues


def parse_due_date(body: str) -> date | None:
    """Extract a due date from issue body text like 'Due: 2026-09-12'."""
    match = re.search(r"Due:\s*(\d{4}-\d{2}-\d{2})", body, re.IGNORECASE)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_dependencies(body: str) -> dict:
    """Extract 'blocks #N' and 'blocked by #N' references from issue body text."""
    blocks = [int(n) for n in re.findall(r"blocks #(\d+)", body, re.IGNORECASE)]
    blocked_by = [int(n) for n in re.findall(r"blocked by #(\d+)", body, re.IGNORECASE)]
    return {"blocks": blocks, "blocked_by": blocked_by}


def enrich_issues(issues: list[dict]) -> list[dict]:
    """Attach parsed due_date and dependency info to each issue dict."""
    today = date.today()
    for issue in issues:
        due = parse_due_date(issue["body"])
        deps = parse_dependencies(issue["body"])

        issue["due_date"] = due.isoformat() if due else None
        issue["blocks"] = deps["blocks"]
        issue["blocked_by"] = deps["blocked_by"]

        if due:
            days_overdue = (today - due).days
            issue["is_overdue"] = days_overdue > 0
            issue["days_overdue"] = max(days_overdue, 0)
            issue["days_until_due"] = max(-days_overdue, 0)
        else:
            issue["is_overdue"] = False
            issue["days_overdue"] = 0
            issue["days_until_due"] = None

    return issues
