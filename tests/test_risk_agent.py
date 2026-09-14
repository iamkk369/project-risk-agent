from unittest.mock import Mock

import pytest
import requests

from src.risk_agent.github_tool import (
    fetch_open_issues,
    parse_dependencies,
)
from src.risk_agent.report import generate_report
from src.risk_agent.risk_engine import analyze_all, calculate_risk


def make_issue(number, **overrides):
    issue = {
        "number": number,
        "title": f"Issue {number}",
        "body": "",
        "url": f"https://github.com/example/repo/issues/{number}",
        "blocks": [],
        "blocked_by": [],
        "is_overdue": False,
        "days_overdue": 0,
        "days_until_due": None,
    }
    issue.update(overrides)
    return issue


def api_response(status_code, payload, text=""):
    response = Mock()
    response.status_code = status_code
    response.headers = {}
    response.text = text
    response.json.return_value = payload
    return response


def test_fetch_open_issues_normalizes_issues_and_skips_pull_requests(monkeypatch):
    response = api_response(
        200,
        [
            {
                "number": 1,
                "title": "Implement feature",
                "body": None,
                "html_url": "https://github.com/example/repo/issues/1",
            },
            {
                "number": 2,
                "title": "Pull request",
                "body": "PR body",
                "html_url": "https://github.com/example/repo/pull/2",
                "pull_request": {"url": "https://api.github.com/pulls/2"},
            },
        ],
    )
    get = Mock(return_value=response)
    monkeypatch.setattr("src.risk_agent.github_tool.requests.get", get)

    assert fetch_open_issues("example/repo", "token") == [
        {
            "number": 1,
            "title": "Implement feature",
            "body": "",
            "url": "https://github.com/example/repo/issues/1",
        }
    ]
    assert get.call_args.kwargs["params"] == {
        "state": "open",
        "per_page": 100,
        "page": 1,
    }


def test_fetch_open_issues_fetches_all_pages(monkeypatch):
    first_page = [
        {
            "number": number,
            "title": f"Issue {number}",
            "body": "body",
            "html_url": f"https://github.com/example/repo/issues/{number}",
        }
        for number in range(1, 101)
    ]
    second_page = [
        {
            "number": 101,
            "title": "Issue 101",
            "body": "body",
            "html_url": "https://github.com/example/repo/issues/101",
        }
    ]
    get = Mock(
        side_effect=[
            api_response(200, first_page),
            api_response(200, second_page),
        ]
    )
    monkeypatch.setattr("src.risk_agent.github_tool.requests.get", get)

    issues = fetch_open_issues("example/repo", "token")

    assert [issue["number"] for issue in issues] == list(range(1, 102))
    assert [call.kwargs["params"]["page"] for call in get.call_args_list] == [1, 2]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            "BLOCKS #1; blocks issue 2; Blocking #3; blocks #1",
            {"blocks": [1, 2, 3], "blocked_by": []},
        ),
        (
            "blocked by #4; BLOCKED-BY #5; depends on #6; depends-on #4",
            {"blocks": [], "blocked_by": [4, 5, 6]},
        ),
    ],
)
def test_parse_dependencies_supports_case_and_syntax_variations(body, expected):
    assert parse_dependencies(body) == expected


def test_fetch_open_issues_reports_network_failure(monkeypatch):
    def raise_timeout(*args, **kwargs):
        raise requests.exceptions.Timeout("connection timed out")

    monkeypatch.setattr("src.risk_agent.github_tool.requests.get", raise_timeout)

    with pytest.raises(RuntimeError, match="Unable to fetch open issues"):
        fetch_open_issues("example/repo", "token")


@pytest.mark.parametrize("status_code", [403, 429])
def test_fetch_open_issues_reports_rate_limit(monkeypatch, status_code):
    response = api_response(status_code, {"message": "rate limited"})
    response.headers = {"X-RateLimit-Reset": "1234567890"}
    monkeypatch.setattr("src.risk_agent.github_tool.requests.get", Mock(return_value=response))

    with pytest.raises(RuntimeError, match="rate limit.*HTTP"):
        fetch_open_issues("example/repo", "token")


def test_calculate_risk_marks_overdue_blocker_high():
    issue = make_issue(1, blocks=[2], is_overdue=True, days_overdue=3)
    lookup = {1: issue, 2: make_issue(2, title="Downstream")}

    risk = calculate_risk(issue, lookup)

    assert risk["level"] == "High"
    assert "Overdue by 3 day(s)" in risk["reason"]
    assert '#2 "Downstream"' in risk["reason"]


def test_calculate_risk_covers_medium_and_low_rules():
    overdue = calculate_risk(
        make_issue(1, is_overdue=True, days_overdue=1),
        {},
    )
    multi_blocker = calculate_risk(
        make_issue(2, blocks=[3, 4]),
        {3: make_issue(3), 4: make_issue(4)},
    )
    soon_due = calculate_risk(
        make_issue(5, blocks=[6], days_until_due=2),
        {6: make_issue(6)},
    )
    low = calculate_risk(make_issue(7), {})

    assert overdue["level"] == "Medium"
    assert multi_blocker["level"] == "Medium"
    assert soon_due["level"] == "Medium"
    assert low["level"] == "Low"


def test_analyze_all_propagates_overdue_risk_through_multiple_levels():
    issues = [
        make_issue(1, title="Overdue root", blocks=[2], is_overdue=True, days_overdue=2),
        make_issue(2, title="Middle", blocks=[3]),
        make_issue(3, title="Leaf"),
    ]

    analyze_all(issues)

    assert issues[0]["risk_level"] == "High"
    assert issues[1]["risk_level"] == "Medium"
    assert issues[2]["risk_level"] == "Medium"
    assert "Blocked by overdue upstream task #1." in issues[1]["risk_reason"]
    assert "Blocked by overdue upstream task #1." in issues[2]["risk_reason"]


def test_analyze_all_propagates_high_risk_to_downstream_issue():
    issues = [
        make_issue(1, title="High root", blocks=[2, 3]),
        make_issue(2, title="Middle", blocks=[3]),
        make_issue(3, title="Leaf"),
    ]
    issues[0]["is_overdue"] = True
    issues[0]["days_overdue"] = 1

    analyze_all(issues)

    assert issues[1]["risk_level"] == "Medium"
    assert issues[2]["risk_level"] == "Medium"
    assert "Blocked by overdue upstream task #1." in issues[2]["risk_reason"]


def test_generate_report_formats_risk_groups_and_issue_details():
    issues = [
        make_issue(
            1,
            title="Critical task",
            due_date="2026-09-01",
            risk_level="High",
            risk_reason="Blocked by overdue upstream task #9.",
            recommended_action="Escalate.",
        ),
        make_issue(
            2,
            title="Watch task",
            due_date=None,
            risk_level="Medium",
            risk_reason="Due soon.",
            recommended_action="Monitor.",
        ),
        make_issue(
            3,
            title="Healthy task",
            due_date=None,
            risk_level="Low",
            risk_reason="On track.",
            recommended_action="No action needed.",
        ),
    ]

    report = generate_report(issues, "example/repo")

    assert "# Project Risk Report — example/repo" in report
    assert "🔴 1 High Risk" in report
    assert "🟡 1 Medium Risk" in report
    assert "🟢 1 On Track" in report
    assert "### #1 — Critical task" in report
    assert "- **Due:** 2026-09-01" in report
    assert "- **Due:** no due date" in report
    assert "- **Why it matters:** Blocked by overdue upstream task #9." in report
    assert "[View issue](https://github.com/example/repo/issues/1)" in report
    assert "## 🟢 ON TRACK" in report
    assert "- #3 Healthy task" in report
