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
    assert summary["bottleneck_chain_length"] == 3
    assert summary["affected_issue_numbers"] == [2, 3]


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

    def test_saves_bounded_history(self, tmp_path):
        from src.risk_agent.history import save_snapshot, load_latest_history
        path = tmp_path / "history.json"
        for i in range(3):
            save_snapshot(path, {"timestamp": str(i)}, max_snapshots=2)
        data = __import__("json").loads(path.read_text())
        assert len(data["snapshots"]) == 2
        assert load_latest_history(path)["timestamp"] == "2"

class TestDecisionSupport:
    def test_prioritizes_highest_delivery_risk_and_adds_consequence(self):
        from src.risk_agent.decision_support import enrich_decision_support

        issues = [
            make_issue(1, title="Bottleneck", blocks=[2, 3], is_overdue=True, days_overdue=2, risk_level="High"),
            make_issue(2, title="Downstream", risk_level="Medium"),
            make_issue(3, title="Other", risk_level="Low"),
        ]
        issues[0]["downstream_count"] = 2
        issues[0]["critical_chain_length"] = 3
        issues[1]["downstream_count"] = 0
        issues[1]["critical_chain_length"] = 1
        summary = {"posture": "Critical"}

        enrich_decision_support(issues, summary)

        assert summary["recommended_focus_issue"] == 1
        assert issues[0]["decision_priority"] == 1
        assert issues[0]["urgency"] == "Immediate"
        assert "2 downstream issue(s)" in issues[0]["potential_consequence"]
        assert "recovery plan" in issues[0]["decision"]

    def test_propagated_high_risk_points_to_upstream_intervention(self):
        from src.risk_agent.decision_support import enrich_decision_support

        issues = [
            make_issue(1, title="Root blocker", blocks=[2], is_overdue=True, days_overdue=2, risk_level="High"),
            make_issue(2, title="Downstream task", blocked_by=[1], risk_level="High"),
        ]
        issues[0]["downstream_count"] = 1
        issues[0]["critical_chain_length"] = 2
        issues[1]["downstream_count"] = 0
        issues[1]["critical_chain_length"] = 1
        issues[1]["upstream_blockers"] = [1]
        summary = {"posture": "Critical"}

        enrich_decision_support(issues, summary)

        assert "upstream blocker #1" in issues[1]["decision"]
        assert "No intervention required" not in issues[1]["decision"]

    def test_on_track_issue_has_no_decision_priority(self):
        from src.risk_agent.decision_support import enrich_decision_support

        issues = [make_issue(1, risk_level="Low")]
        summary = {"posture": "On Track"}

        enrich_decision_support(issues, summary)

        assert issues[0]["decision_priority"] is None
        assert summary["recommended_focus_issue"] is None
        assert "No intervention" in summary["recommended_focus_reason"]

class TestEventDrivenExecution:
    def test_signature_verification(self):
        import hashlib
        import hmac
        from src.risk_agent.webhook import verify_signature

        body = b'{"action":"opened"}'
        secret = "test-secret"
        digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        assert verify_signature(secret, body, f"sha256={digest}")
        assert not verify_signature(secret, body, "sha256=bad")
        assert not verify_signature(secret, body, None)

    def test_supported_issue_event_triggers_analysis(self):
        from src.risk_agent.event_handler import handle_github_event

        calls = []
        payload = {
            "action": "edited",
            "repository": {"full_name": "owner/repo"},
        }
        result = handle_github_event("issues", payload, lambda: calls.append(True), "owner/repo")

        assert result["triggered"] is True
        assert calls == [True]

    def test_unrelated_repository_is_rejected(self):
        from src.risk_agent.event_handler import handle_github_event

        calls = []
        payload = {
            "action": "opened",
            "repository": {"full_name": "attacker/other-repo"},
        }
        result = handle_github_event("issues", payload, lambda: calls.append(True), "owner/repo")

        assert result["status"] == "rejected"
        assert result["triggered"] is False
        assert calls == []

    def test_non_triggering_issue_action_is_ignored(self):
        from src.risk_agent.event_handler import handle_github_event

        calls = []
        payload = {
            "action": "assigned",
            "repository": {"full_name": "owner/repo"},
        }
        result = handle_github_event("issues", payload, lambda: calls.append(True), "owner/repo")

        assert result["status"] == "ignored"
        assert result["triggered"] is False
        assert calls == []


def test_security_prefers_secrets_manager(monkeypatch):
    from src.risk_agent.security import get_credential, get_secret

    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    monkeypatch.setenv("GITHUB_TOKEN_SECRET_ARN", "arn:aws:secretsmanager:us-east-1:123:secret:token")

    # boto3 is imported lazily so the test injects a minimal fake module.
    import sys
    import types
    fake_boto3 = types.SimpleNamespace(client=Mock(return_value=Mock(get_secret_value=Mock(return_value={"SecretString": "secret-token"}))))
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    get_secret.cache_clear()
    assert get_credential("GITHUB_TOKEN", "GITHUB_TOKEN_SECRET_ARN") == "secret-token"


def test_security_falls_back_to_environment(monkeypatch):
    from src.risk_agent.security import get_credential

    monkeypatch.setenv("GEMINI_API_KEY", "env-key")
    monkeypatch.delenv("GEMINI_API_KEY_SECRET_ARN", raising=False)
    assert get_credential("GEMINI_API_KEY", "GEMINI_API_KEY_SECRET_ARN") == "env-key"


def test_security_does_not_log_secret_values(caplog):
    from src.risk_agent.observability import configure_logging, log_event
    import logging

    configure_logging()
    with caplog.at_level(logging.INFO, logger="risk_agent"):
        log_event(logging.INFO, "test", "credential configured", outcome="success")
    assert "credential configured" in caplog.text
    assert "secret-token" not in caplog.text


def test_agentcore_env_example_contains_secret_arn_hooks():
    from pathlib import Path

    text = Path("deploy/agentcore.env.example").read_text(encoding="utf-8")
    assert "GITHUB_TOKEN_SECRET_ARN=" in text
    assert "GEMINI_API_KEY_SECRET_ARN=" in text

class TestPortfolioAnalysis:
    def test_parse_project_repos_deduplicates_and_trims(self):
        from src.risk_agent.portfolio import parse_project_repos
        assert parse_project_repos(" a/b, c/d\na/b, c/d ") == ["a/b", "c/d"]

    def test_summarize_portfolio_prioritizes_critical_projects(self):
        from src.risk_agent.portfolio import summarize_portfolio
        results = [
            {"repo": "team/healthy", "summary": {"posture": "On Track", "high_count": 0, "affected_issue_count": 0, "bottleneck_downstream_count": 0}},
            {"repo": "team/watch", "summary": {"posture": "Watch", "high_count": 0, "affected_issue_count": 2, "bottleneck_downstream_count": 1}},
            {"repo": "team/critical", "summary": {"posture": "Critical", "high_count": 1, "affected_issue_count": 3, "bottleneck_downstream_count": 2}},
        ]
        summary = summarize_portfolio(results)
        assert summary["overall_posture"] == "Critical"
        assert summary["priority_projects"] == ["team/critical", "team/watch"]

    def test_analyze_portfolio_keeps_project_boundaries(self):
        from src.risk_agent.portfolio import analyze_portfolio
        calls = []
        def fake(repo):
            calls.append(repo)
            posture = "Critical" if repo == "a/b" else "On Track"
            return {"summary": {"posture": posture, "high_count": 1 if posture == "Critical" else 0,
                                  "medium_count": 0, "total_issues": 2, "risk_count": 1 if posture == "Critical" else 0,
                                  "affected_issue_count": 1 if posture == "Critical" else 0,
                                  "bottleneck_issue": 1 if posture == "Critical" else None,
                                  "bottleneck_title": "Root" if posture == "Critical" else None}}
        results, summary, report = analyze_portfolio(["a/b", "c/d"], fake)
        assert calls == ["a/b", "c/d"]
        assert summary["project_count"] == 2
        assert summary["overall_posture"] == "Critical"
        assert "a/b" in report and "c/d" in report


def test_persist_report_to_s3_is_disabled_without_bucket(monkeypatch):
    from src.risk_agent.storage import persist_report_to_s3

    monkeypatch.delenv("S3_REPORT_BUCKET", raising=False)
    assert persist_report_to_s3("# report", "example/repo") is None


def test_persist_report_to_s3_uploads_encrypted_markdown(monkeypatch):
    from src.risk_agent.storage import persist_report_to_s3

    client = Mock()
    monkeypatch.setattr("src.risk_agent.storage.boto3.client", Mock(return_value=client))
    monkeypatch.setenv("S3_REPORT_BUCKET", "risk-agent-reports")
    monkeypatch.setenv("S3_REPORT_PREFIX", "reports")
    monkeypatch.setenv("AWS_REGION", "us-east-1")

    uri = persist_report_to_s3("# report", "owner/repo")

    assert uri.startswith("s3://risk-agent-reports/reports/owner-repo/risk-report-")
    assert uri.endswith(".md")
    kwargs = client.put_object.call_args.kwargs
    assert kwargs["Bucket"] == "risk-agent-reports"
    assert kwargs["Key"].startswith("reports/owner-repo/risk-report-")
    assert kwargs["Body"] == b"# report"
    assert kwargs["ContentType"] == "text/markdown; charset=utf-8"
    assert kwargs["ServerSideEncryption"] == "AES256"
