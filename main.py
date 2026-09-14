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

from github_tool import fetch_open_issues, enrich_issues
from risk_engine import analyze_all
from report import generate_report

load_dotenv()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")


def run_pipeline() -> str:
    """The core deterministic pipeline: fetch -> enrich -> score -> report."""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        raise RuntimeError("Set GITHUB_TOKEN and GITHUB_REPO in your .env file.")

    issues = fetch_open_issues(GITHUB_REPO, GITHUB_TOKEN)
    issues = enrich_issues(issues)
    issues = analyze_all(issues)
    report_md = generate_report(issues, GITHUB_REPO)
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

    @tool
    def get_project_risk_report() -> str:
        """Fetch GitHub issues, analyze deadlines/dependencies, and return a risk report."""
        return run_pipeline()

    agent = Agent(
        tools=[get_project_risk_report],
        model="us.anthropic.claude-3-5-sonnet-20241022-v2:0",
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
