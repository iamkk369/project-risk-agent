"""
Project Risk Agent - main entry point.

Two ways to run this:
1. python main.py --local   -> runs the deterministic pipeline directly.
2. python main.py           -> runs it through the Strands Agent + Gemini,
                                so the model adds the executive reasoning pass.
"""
import os
import argparse
from dotenv import load_dotenv

from .github_tool import fetch_open_issues, enrich_issues
from .risk_engine import analyze_all, summarize_project_risk
from .report import generate_report
from .history import load_latest_history, compare_history, save_snapshot
from .decision_support import enrich_decision_support
from .security import get_credential
from .observability import configure_logging, log_event, set_request_id
from .portfolio import analyze_portfolio, parse_project_repos
from .storage import persist_report_to_s3

configure_logging()

load_dotenv()


def _get_github_token() -> str:
    """Read the GitHub credential at runtime."""
    github_token = get_credential("GITHUB_TOKEN", "GITHUB_TOKEN_SECRET_ARN")
    if not github_token:
        raise RuntimeError("Set GITHUB_TOKEN in your .env file.")
    return github_token


def _get_runtime_config() -> tuple[str, str]:
    """Read the default single-repository settings at runtime."""
    github_repo = os.getenv("GITHUB_REPO")
    if not github_repo:
        raise RuntimeError("Set GITHUB_REPO to an owner/repository value.")
    return _get_github_token(), github_repo


def run_pipeline(repo: str | None = None) -> str:
    """The core deterministic pipeline: fetch -> enrich -> score -> report."""
    request_id = set_request_id()
    log_event(20, "analysis_started", "Project risk analysis started")
    github_token, configured_repo = _get_runtime_config()
    github_repo = repo or configured_repo

    issues = fetch_open_issues(github_repo, github_token)
    issues = enrich_issues(issues)
    issues = analyze_all(issues)
    project_summary = summarize_project_risk(issues)
    issues = enrich_decision_support(issues, project_summary)
    history_path = os.getenv("RISK_HISTORY_FILE", ".risk_history.json")
    previous = load_latest_history(history_path)
    trajectory = compare_history(previous, issues, project_summary)
    report_md = generate_report(issues, github_repo, trajectory)
    s3_uri = persist_report_to_s3(report_md, github_repo)
    if s3_uri:
        log_event(20, "report_persisted", "Risk report persisted to S3", repository=github_repo, s3_uri=s3_uri)
    save_snapshot(history_path, trajectory["current_snapshot"])
    log_event(20, "analysis_completed", "Project risk analysis completed", repository=github_repo, outcome=project_summary["posture"])
    return report_md



def run_portfolio() -> str:
    """Analyze all repositories configured in PROJECT_REPOS independently."""
    repos = parse_project_repos(os.getenv("PROJECT_REPOS"))
    if not repos:
        raise RuntimeError("Set PROJECT_REPOS to a comma-separated list of owner/repo values.")
    results, _, report = analyze_portfolio(
        repos,
        lambda project_repo: {
            "summary": summarize_project_risk(
                _analyze_project_for_portfolio(project_repo)
            )
        },
    )
    return report


def _analyze_project_for_portfolio(repo: str) -> list[dict]:
    """Run deterministic analysis for one repository without writing its report."""
    github_token = _get_github_token()
    issues = fetch_open_issues(repo, github_token)
    issues = enrich_issues(issues)
    return analyze_all(issues)

def run_local():
    """Run the deterministic pipeline directly and print the report. No Strands agent call."""
    report_md = run_pipeline()
    print(report_md)

    with open("risk_report.md", "w", encoding="utf-8") as f:
        f.write(report_md)
    print("\n\n[Saved to risk_report.md]")


def run_with_agent(repo: str | None = None):
    """
    Run the pipeline through a Strands Agent so the model can add executive
    reasoning on top of the deterministic report.
    """
    from strands import Agent, tool
    from strands.models.gemini import GeminiModel

    @tool
    def get_project_risk_report() -> str:
        """Fetch GitHub issues, analyze deadlines/dependencies, and return a risk report."""
        return run_pipeline(repo)

    gemini_api_key = get_credential("GEMINI_API_KEY", "GEMINI_API_KEY_SECRET_ARN")
    if not gemini_api_key:
        raise RuntimeError("Set GEMINI_API_KEY in your .env file.")

    model = GeminiModel(
        client_args={"api_key": gemini_api_key},
        model_id="gemini-3.6-flash",
        params={"temperature": 0.7, "max_output_tokens": 4096},
    )

    agent = Agent(
        tools=[get_project_risk_report],
        model=model,
    )

    result = agent(
        "Call the project risk report tool. Act as a project risk decision-support "
        "agent: summarize the most important delivery risk, explain the evidence "
        "and downstream impact, describe the consequence of inaction, and state "
        "the single best next decision for the manager. Then include the full "
        "tool-generated report exactly as returned."
    )
    print(result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--local", action="store_true", help="Run without the Strands agent")
    parser.add_argument("--webhook", action="store_true", help="Start the GitHub webhook listener")
    parser.add_argument("--portfolio", action="store_true", help="Analyze repositories listed in PROJECT_REPOS")
    parser.add_argument("--repo", help="Analyze this GitHub repository (owner/repository)")
    parser.add_argument("--host", default="127.0.0.1", help="Webhook bind host")
    parser.add_argument("--port", type=int, default=8080, help="Webhook bind port")
    args = parser.parse_args()

    if args.webhook:
        from .webhook import run_webhook_server
        run_webhook_server(args.host, args.port)
    elif args.portfolio:
        report = run_portfolio()
        print(report)
        with open("portfolio_risk_report.md", "w", encoding="utf-8") as f:
            f.write(report)
    elif args.local:
        run_local()
    else:
        run_with_agent(args.repo)
