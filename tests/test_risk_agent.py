from unittest.mock import Mock

import pytest
import requests

from src.risk_agent.github_tool import (
    fetch_open_issues,
    parse_dependencies,
)
from src.risk_agent.report import generate_report
from src.risk_agent.risk_engine import analyze_all, calculate_risk, summarize_project_risk


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


def test_fetch_open_issues_reports_rate_limit(monkeypatch):
    status_code = 429
    response = api_response(status_code, {"message": "rate limited"})
    response.headers = {"X-RateLimit-Reset": "1234567890"}
    monkeypatch.setattr("src.risk_agent.github_tool.requests.get", Mock(return_value=response))

    with pytest.raises(RuntimeError, match="rate limit.*HTTP 429"):
        fetch_open_issues("example/repo", "token")


def test_fetch_open_issues_reports_forbidden_as_access_error(monkeypatch):
    response = api_response(403, {"message": "Resource not accessible"}, text="Resource not accessible")
    monkeypatch.setattr("src.risk_agent.github_tool.requests.get", Mock(return_value=response))

    with pytest.raises(RuntimeError, match="denied access.*HTTP 403"):
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


def test_analyze_all_builds_impact_from_blocked_by_relationship():
    issues = [
        make_issue(1, title="Root", is_overdue=True, days_overdue=2),
        make_issue(2, title="Downstream", blocked_by=[1]),
        make_issue(3, title="Leaf", blocked_by=[2]),
    ]

    analyze_all(issues)

    assert issues[0]["downstream_count"] == 2
    assert issues[0]["downstream_issues"] == [2, 3]
    assert issues[0]["critical_chain_length"] == 3
    assert issues[1]["upstream_blockers"] == [1]
    assert issues[1]["risk_level"] == "High"
    assert issues[2]["risk_level"] == "High"


def test_high_risk_propagation_preserves_high_severity():
    issues = [
        make_issue(1, title="High root", blocks=[2]),
        make_issue(2, title="Downstream"),
    ]
    issues[0]["is_overdue"] = True
    issues[0]["days_overdue"] = 1
    issues[0]["downstream_count"] = 1

    analyze_all(issues)

    assert issues[0]["risk_level"] == "High"
    assert issues[1]["risk_level"] == "High"
    assert "High Risk upstream task #1" in issues[1]["risk_reason"]


def test_analyze_all_propagates_overdue_risk_through_multiple_levels():
    issues = [
        make_issue(1, title="Overdue root", blocks=[2], is_overdue=True, days_overdue=2),
        make_issue(2, title="Middle", blocks=[3]),
        make_issue(3, title="Leaf"),
    ]

    analyze_all(issues)

    assert issues[0]["risk_level"] == "High"
    assert issues[1]["risk_level"] == "High"
    assert issues[2]["risk_level"] == "High"
    assert "Blocked by High Risk upstream task #1." in issues[1]["risk_reason"]
    assert "Blocked by High Risk upstream task #1." in issues[2]["risk_reason"]


def test_analyze_all_propagates_high_risk_to_downstream_issue():
    issues = [
        make_issue(1, title="High root", blocks=[2, 3]),
        make_issue(2, title="Middle", blocks=[3]),
        make_issue(3, title="Leaf"),
    ]
    issues[0]["is_overdue"] = True
    issues[0]["days_overdue"] = 1

    analyze_all(issues)

    assert issues[1]["risk_level"] == "High"
    assert issues[2]["risk_level"] == "High"
    assert "Blocked by High Risk upstream task #1." in issues[2]["risk_reason"]


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


def test_summarize_project_risk_identifies_bottleneck_and_exposure():
    issues = [
        make_issue(1, title="Root", blocks=[2], is_overdue=True, days_overdue=2),
        make_issue(2, title="Middle", blocked_by=[1], blocks=[3]),
        make_issue(3, title="Leaf", blocked_by=[2]),
        make_issue(4, title="Healthy"),
    ]

    analyze_all(issues)
    summary = summarize_project_risk(issues)

    assert summary["posture"] == "Critical"
    assert summary["total_issues"] == 4
    assert summary["high_count"] == 3
    assert summary["bottleneck_issue"] == 1
    assert summary["bottleneck_downstream_count"] == 2


class TestRiskAnalysis:
    def test_analyze_all_propagates_overdue_risk_to_downstream_issue(self):
        from src.risk_agent.risk_engine import analyze_all

        issues = [
            # Overdue upstream: calculate_risk -> High (overdue AND blocks)
            make_issue(1, title="Overdue upstream", is_overdue=True, days_overdue=1, blocks=[2]),
            # Downstream with no dependencies: should become High due to propagation
            make_issue(2, title="Dependent leaf"),
        ]
        analyze_all(issues)

        upstream = next(i for i in issues if i["number"] == 1)
        downstream = next(i for i in issues if i["number"] == 2)

        assert upstream["risk_level"] == "High"
        assert downstream["risk_level"] == "High"
        assert "Blocked by High Risk upstream task #1" in downstream["risk_reason"]
        assert downstream["recommended_action"] == "Escalate the upstream bottleneck and confirm a recovery plan for this issue's dependent work."

    def test_analyze_all_preserves_input_identity_fields_and_adds_analysis_only(self):
        from src.risk_agent.risk_engine import analyze_all

        original = [
            make_issue(1, title="Original title", body="original body", url="https://example.com/1"),
            make_issue(2, title="Another original", body="another body", url="https://example.com/2"),
        ]
        input_copy = [{"number": i["number"], "title": i["title"], "body": i.get("body", ""), "url": i.get("url", "")} for i in original]
        analyze_all(original)

        for original_issue, saved in zip(original, input_copy):
            assert original_issue["number"] == saved["number"]
            assert original_issue["title"] == saved["title"]
            assert original_issue.get("body", "") == saved["body"]
            assert original_issue.get("url", "") == saved["url"]
            # Only analysis/graph-metric fields may have changed
            assert set(original_issue.keys()) >= {"number", "title", "body", "url", "risk_level", "risk_reason", "recommended_action"}

    def test_summarize_project_risk_aggregate_counts_match_individual_analysis(self):
        from src.risk_agent.risk_engine import analyze_all, summarize_project_risk

        issues = [
            make_issue(1, title="High overdue and blocks 2", is_overdue=True, days_overdue=3, blocks=[2]),
            make_issue(2, title="downstream of high 1"),
            make_issue(3, title="Medium due soon", days_until_due=1, blocks=[5]),
            make_issue(4, title="Low isolated 4"),
            make_issue(5, title="Low isolated 5"),
        ]
        analyze_all(issues)
        summary = summarize_project_risk(issues)

        assert summary["total_issues"] == 5
        assert summary["high_count"] == 2
        assert summary["medium_count"] == 1
        assert summary["low_count"] == 2
        assert summary["high_count"] + summary["medium_count"] + summary["low_count"] == summary["total_issues"]
        assert summary["risk_count"] == 3
        assert summary["bottleneck_issue"] == 1
        assert summary["bottleneck_title"] == "High overdue and blocks 2"
        assert summary["bottleneck_downstream_count"] == 1
        assert summary["bottleneck_chain_length"] == 2
        # issue 2 is downstream of high-risk issue 1, escalated to High
        assert issues[1]["risk_level"] == "High"
        # issue 3 is a standalone near-deadline Medium issue
        assert issues[2]["risk_level"] == "Medium"
        # issues 4 and 5 have no downstream impact
        assert issues[3]["downstream_count"] == 0
        assert issues[4]["downstream_count"] == 0

def test_summarize_project_risk_reports_on_track_when_no_risks():
    issues = [make_issue(1), make_issue(2)]
    analyze_all(issues)
    summary = summarize_project_risk(issues)

    assert summary["posture"] == "On Track"
    assert summary["risk_count"] == 0
    assert summary["bottleneck_issue"] is None


class TestRiskHistory:
    def test_baseline_snapshot(self):
        from src.risk_agent.history import compare_history
        issues = [{"number": 1, "title": "Backend", "risk_level": "High", "downstream_count": 2, "critical_chain_length": 3}]
        summary = {"posture": "Critical", "risk_count": 1, "affected_issue_count": 2, "bottleneck_issue": 1}
        result = compare_history(None, issues, summary)
        assert result["baseline"] is True
        assert result["new_risks"] == []

    def test_detects_escalation_and_resolution(self):
        from src.risk_agent.history import compare_history
        previous = {
            "timestamp": "2026-09-14T00:00:00+00:00",
            "project": {"posture": "Watch"},
            "issues": {
                "1": {"title": "Backend", "risk_level": "Medium"},
                "2": {"title": "QA", "risk_level": "High"},
                "3": {"title": "Docs", "risk_level": "Low"},
            },
        }
        issues = [
            {"number": 1, "title": "Backend", "risk_level": "High"},
            {"number": 2, "title": "QA", "risk_level": "Low"},
            {"number": 3, "title": "Docs", "risk_level": "Low"},
            {"number": 4, "title": "New blocker", "risk_level": "Medium"},
        ]
        summary = {"posture": "Critical", "risk_count": 2, "affected_issue_count": 0, "bottleneck_issue": None}
        result = compare_history(previous, issues, summary)
        assert result["escalating"] == ["1"]
        assert result["deescalating"] == ["2"]
        assert result["new_risks"] == ["4"]
        assert result["project_change"] == "Watch → Critical"


    def test_compare_history_deescalating_high_to_low(self):
        from src.risk_agent.history import compare_history

        previous = {
            "timestamp": "2026-09-14T00:00:00+00:00",
            "project": {"posture": "Critical"},
            "issues": {
                "1": {"title": "Root", "risk_level": "High"},
            },
        }
        issues = [
            {"number": 1, "title": "Root", "risk_level": "Low"},
        ]
        summary = {"posture": "On Track", "risk_count": 0, "affected_issue_count": 0, "bottleneck_issue": None}
        result = compare_history(previous, issues, summary)
        assert result["deescalating"] == ["1"]
        assert result["escalating"] == []
        assert result["persistent"] == []
        assert result["resolved"] == []
        assert result["new_risks"] == []
        assert result["baseline"] is False
        assert result["project_change"] == "Critical → On Track"

    def test_compare_history_resolved_issue_absent_from_current(self):
        from src.risk_agent.history import compare_history

        previous = {
            "timestamp": "2026-09-14T00:00:00+00:00",
            "project": {"posture": "Critical"},
            "issues": {
                "1": {"title": "Root", "risk_level": "High"},
                "2": {"title": "Downstream", "risk_level": "Medium"},
            },
        }
        issues = [
            {"number": 1, "title": "Root", "risk_level": "High"},
        ]
        summary = {"posture": "Watch", "risk_count": 1, "affected_issue_count": 0, "bottleneck_issue": None}
        result = compare_history(previous, issues, summary)
        assert result["resolved"] == ["2"]
        assert result["deescalating"] == []
        assert result["escalating"] == []
        assert result["persistent"] == ["1"]
        assert result["new_risks"] == []
        assert result["project_change"] == "Critical → Watch"

    def test_compare_history_multiple_issues_one_branch_each(self):
        from src.risk_agent.history import compare_history

        previous = {
            "timestamp": "2026-09-14T00:00:00+00:00",
            "project": {"posture": "Watch"},
            "issues": {
                "1": {"title": "Escalate", "risk_level": "Low"},
                "2": {"title": "Persist", "risk_level": "Medium"},
                "3": {"title": "De-escalate", "risk_level": "High"},
                "4": {"title": "Old", "risk_level": "High"},
            },
        }
        issues = [
            {"number": 1, "title": "Escalate", "risk_level": "Medium"},
            {"number": 2, "title": "Persist", "risk_level": "Medium"},
            {"number": 3, "title": "De-escalate", "risk_level": "Low"},
        ]
        summary = {"posture": "On Track", "risk_count": 2, "affected_issue_count": 0, "bottleneck_issue": None}
        result = compare_history(previous, issues, summary)
        assert result["escalating"] == ["1"]
        assert result["persistent"] == ["2"]
        assert result["deescalating"] == ["3"]
        assert result["resolved"] == ["4"]
        assert result["new_risks"] == []
        assert result["baseline"] is False

    def test_compare_history_empty_current_snapshot(self):
        from src.risk_agent.history import compare_history

        previous = {
            "timestamp": "2026-09-14T00:00:00+00:00",
            "project": {"posture": "Critical"},
            "issues": {
                "1": {"title": "Root", "risk_level": "High"},
            },
        }
        issues = []
        summary = {"posture": "On Track", "risk_count": 0, "affected_issue_count": 0, "bottleneck_issue": None}
        result = compare_history(previous, issues, summary)
        assert result["resolved"] == ["1"]
        assert result["escalating"] == []
        assert result["deescalating"] == []
        assert result["persistent"] == []
        assert result["new_risks"] == []
        assert result["project_change"] == "Critical → On Track"


    def test_compare_history_escalating_medium_to_high(self):
        from src.risk_agent.history import compare_history

        previous = {
            "timestamp": "2026-09-14T00:00:00+00:00",
            "project": {"posture": "Watch"},
            "issues": {
                "1": {"title": "Root", "risk_level": "Low"},
            },
        }
        issues = [
            {"number": 1, "title": "Root", "risk_level": "High"},
        ]
        summary = {"posture": "Watch", "risk_count": 1, "affected_issue_count": 0, "bottleneck_issue": None}
        result = compare_history(previous, issues, summary)
        assert result["escalating"] == ["1"]
        assert result["deescalating"] == []
        assert result["persistent"] == []
        assert result["resolved"] == []
        assert result["new_risks"] == []
        assert result["baseline"] is False
        assert result["project_change"] == "Unchanged: Watch"

    def test_compare_history_escalating_low_to_medium(self):
        from src.risk_agent.history import compare_history

        previous = {
            "timestamp": "2026-09-14T00:00:00+00:00",
            "project": {"posture": "Watch"},
            "issues": {
                "1": {"title": "Root", "risk_level": "Low"},
            },
        }
        issues = [
            {"number": 1, "title": "Root", "risk_level": "Medium"},
        ]
        summary = {"posture": "Watch", "risk_count": 1, "affected_issue_count": 0, "bottleneck_issue": None}
        result = compare_history(previous, issues, summary)
        assert result["escalating"] == ["1"]
        assert result["deescalating"] == []
        assert result["persistent"] == []
        assert result["resolved"] == []
        assert result["new_risks"] == []
        assert result["baseline"] is False
        assert result["project_change"] == "Unchanged: Watch"

def test_explicit_repo_works_when_env_unset(monkeypatch):
    """Explicit CLI repository works when GITHUB_REPO is unset."""
    # Mock _get_github_token to return a token
    monkeypatch.setattr("src.risk_agent.main._get_github_token", lambda: "ghp_token")
    # Ensure GITHUB_REPO is not set
    monkeypatch.delenv("GITHUB_REPO", raising=False)
    # Run _get_runtime_config with explicit repo
    from src.risk_agent.main import _get_runtime_config
    token, repo = _get_runtime_config(repo="owner/repo")
    assert repo == "owner/repo"
    assert token == "ghp_token"


def test_env_repo_works_when_no_explicit(monkeypatch):
    """Environment repository works when no explicit repository is provided."""
    # Mock _get_github_token to return a token
    monkeypatch.setattr("src.risk_agent.main._get_github_token", lambda: "ghp_token")
    # Set GITHUB_REPO env var
    monkeypatch.setenv("GITHUB_REPO", "env_owner/repo")
    from src.risk_agent.main import _get_runtime_config
    token, repo = _get_runtime_config()
    assert repo == "env_owner/repo"
    assert token == "ghp_token"


def test_explicit_overrides_env(monkeypatch):
    """Explicit repository overrides the environment value."""
    # Mock _get_github_token to return a token
    monkeypatch.setattr("src.risk_agent.main._get_github_token", lambda: "ghp_token")
    # Set GITHUB_REPO env var
    monkeypatch.setenv("GITHUB_REPO", "env_owner/repo")
    from src.risk_agent.main import _get_runtime_config
    # Pass explicit repo which should override env
    token, repo = _get_runtime_config(repo="explicit_owner/repo")
    assert repo == "explicit_owner/repo"
    assert token == "ghp_token"


def test_missing_both_raises_error(monkeypatch):
    """Missing both values raises the expected configuration error."""
    # Mock _get_github_token to return a token
    monkeypatch.setattr("src.risk_agent.main._get_github_token", lambda: "ghp_token")
    # Ensure GITHUB_REPO is not set
    monkeypatch.delenv("GITHUB_REPO", raising=False)
    from src.risk_agent.main import _get_runtime_config
    with pytest.raises(RuntimeError, match="Set GITHUB_REPO"):
        _get_runtime_config()


class TestS3Observability:
    def test_log_event_includes_s3_uri(self, caplog):
        from io import StringIO

        from src.risk_agent.observability import (
            configure_logging,
            log_event,
            _JsonFormatter,
        )
        import logging

        configure_logging()
        stream = StringIO()
        capture = logging.StreamHandler(stream)
        capture.setFormatter(_JsonFormatter())
        root = logging.getLogger()
        root.addHandler(capture)
        try:
            with caplog.at_level(logging.INFO, logger="risk_agent"):
                log_event(logging.INFO, "test", "message", s3_uri="s3://bucket/key")
            output = stream.getvalue()
        finally:
            root.removeHandler(capture)
        assert '"s3_uri":"s3://bucket/key"' in output