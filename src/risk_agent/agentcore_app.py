"""Amazon Bedrock AgentCore Runtime entrypoint for Project Risk Agent.

This adapter keeps the existing Strands + Gemini agent intact while exposing it
through the AgentCore Runtime contract. The runtime can therefore host the
same project-risk workflow without moving the deterministic risk engine into
AWS-specific code.
"""
from __future__ import annotations

import os
from typing import Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent, tool
from strands.models.gemini import GeminiModel

from .main import run_pipeline
from .security import get_credential
from .observability import configure_logging, set_request_id, log_event

configure_logging()

app = BedrockAgentCoreApp()


def _build_agent(default_repository: str | None = None) -> Agent:
    """Build the Strands agent from runtime environment configuration."""
    api_key = get_credential("GEMINI_API_KEY", "GEMINI_API_KEY_SECRET_ARN")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured in the AgentCore runtime.")

    model = GeminiModel(
        client_args={"api_key": api_key},
        model_id=os.getenv("GEMINI_MODEL_ID", "gemini-3.6-flash"),
        params={"temperature": 0.7, "max_output_tokens": 2048},
    )

    @tool
    def get_project_risk_report(repository: str | None = None) -> str:
        """Analyze the configured repository or an explicit owner/repository."""
        return run_pipeline(repository or default_repository)

    return Agent(
        tools=[get_project_risk_report],
        model=model,
    )


@app.entrypoint
def project_risk_agent(payload: dict[str, Any]) -> dict[str, Any]:
    """Invoke the Project Risk Agent through AgentCore Runtime."""
    request_id = set_request_id(payload.get("request_id") if isinstance(payload.get("request_id"), str) else None)
    log_event(20, "agent_invocation_started", "AgentCore invocation started")
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        prompt = (
            "Analyze the configured project. Identify the most important delivery risk, "
            "explain the evidence and downstream impact, describe the consequence of "
            "inaction, and state the single best next decision for the manager."
        )

    repository = payload.get("repository")
    if repository is not None and not isinstance(repository, str):
        raise ValueError("repository must be an owner/repository string when provided.")

    agent = _build_agent(repository)
    result = agent(prompt)
    log_event(20, "agent_invocation_completed", "AgentCore invocation completed", outcome="success")
    return {"response": str(result), "request_id": request_id}


if __name__ == "__main__":
    app.run()
