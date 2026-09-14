"""
Project Risk Agent - main entry point.

Two ways to run this:
1. python main.py --local   -> runs the pipeline directly (no Bedrock needed,
                                useful while AWS account is still activating)
2. python main.py           -> runs it through the Strands Agent + Bedrock,
                                so the LLM does the final reasoning/report pass
"""
import os
import sys
import argparse
from dotenv import load_dotenv

from .github_tool import fetch_open_issues, enrich_issues
from .risk_engine import analyze_all
from .report import generate_report

load_dotenv()


def _get_runtime_config() -> tuple[str, str]:
    """Read GitHub connection settings at runtime so tests and callers can update env vars."""
    github_token = os.getenv("GITHUB_TOKEN")
    github_repo = os.getenv("GITHUB_REPO")
    if not github_token or not github_repo:
        raise RuntimeError("Set GITHUB_TOKEN and GITHUB_REPO in your .env file.")
    return github_token, github_repo


def run_pipeline() -> str:
    """The core deterministic pipeline: fetch -> enrich -> score -> report."""
    github_token, github_repo = _get_runtime_config()

    issues = fetch_open_issues(github_repo, github_token)
    issues = enrich_issues(issues)
    issues = analyze_all(issues)
    report_md = generate_report(issues, github_repo)
    return report_md


def run_local():
    """Run the pipeline directly and print the report. No Bedrock/Strands agent call."""
    report_md = run_pipeline()
    print(report_md)

    with open("risk_report.md", "w", encoding="utf-8") as f:
        f.write(report_md)
    print("\n\n[Saved to risk_report.md]")


def run_with_agent():
    """
    Run the pipeline through a Strands Agent so the model can add narrative
    reasoning on top of the deterministic report (e.g. an executive summary).
    """
    from strands import Agent, tool
    from strands.models.gemini import GeminiModel

    @tool
    def get_project_risk_report() -> str:
        """Fetch GitHub issues, analyze deadlines/dependencies, and return a risk report."""
        return run_pipeline()

    gemini_api_key = os.getenv("GEMINI_API_KEY")
    if not gemini_api_key:
        raise RuntimeError("Set GEMINI_API_KEY in your .env file.")

    model = GeminiModel(
        client_args={"api_key": gemini_api_key},
        model_id="gemini-3.6-flash",
        params={"temperature": 0.7, "max_output_tokens": 2048},
    )

    agent = Agent(
        tools=[get_project_risk_report],
        model=model,
    )

    result = agent(
        "Call the tool to get the project risk report, then write a short "
        "2-3 sentence executive summary at the top for a manager, followed "
        "by the full report exactly as returned by the tool."
    )
    print(result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--local", action="store_true", help="Run without Bedrock/Strands agent")
    args = parser.parse_args()

    if args.local:
        run_local()
    else:
        run_with_agent()
