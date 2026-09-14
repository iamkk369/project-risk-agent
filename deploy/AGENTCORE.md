# AgentCore Runtime Support

This repository includes an optional **Amazon Bedrock AgentCore Runtime** entrypoint for Project Risk Agent.
The agent continues to use Google Gemini; AgentCore is a supported runtime layer, not a deployed AWS runtime in this repository.

AWS documents that AgentCore Runtime is framework-agnostic, supports Strands, and supports models including Google Gemini. It also provides a managed serverless runtime and CloudWatch observability.

## Prerequisites

- AWS account with credentials configured
- Node.js 20+
- Python 3.10+
- AgentCore CLI
- Appropriate IAM permissions

Install the CLI:

```bash
npm install -g @aws/agentcore
agentcore --version
```

## Project registration

From the project root, use the AgentCore CLI to register the existing Python entrypoint as a Strands agent using the **Gemini** model provider and **CodeZip** build when you later provision AWS resources. CodeZip is intentionally selected because it does not require Docker.

The AgentCore CLI supports a CodeZip packaging flow and can synthesize the required AWS resources through CDK when credentials are configured; no AWS deployment is claimed for this repository.

The runtime entrypoint in this repository is AgentCore-compatible and ready for local validation or future deployment:

```text
src/risk_agent/agentcore_app.py
```

Its contract is:

```text
payload.repository (optional)
payload.prompt
      ↓
Strands Agent
      ↓
get_project_risk_report tool
      ↓
existing GitHub → risk engine → history → decision support pipeline
```

## Environment / secrets

Do **not** commit `.env` or real API tokens.

The runtime needs:

- `GITHUB_TOKEN`
- `GITHUB_REPO`
- `GEMINI_API_KEY`
- `GEMINI_MODEL_ID`

`GITHUB_WEBHOOK_SECRET` is retained for the webhook boundary.

Secrets must be injected into the runtime rather than stored in source control.

## Local AgentCore validation

Before deploying:

```bash
agentcore dev
```

Then invoke the local runtime using the CLI tooling.

## Supported deployment flow

Use a CodeZip AgentCore deployment flow when the target AWS environment is configured:

```bash
agentcore deploy --dry-run
agentcore deploy
agentcore status
```

Then invoke the runtime in the configured environment, if it has been deployed:

```bash
agentcore invoke --prompt "Analyze the configured project and identify the highest priority delivery risk."
```

## Important architecture boundary

This deployment path does **not** add Lambda, Fargate, RDS, Redis, or API Gateway merely to increase service count.

The architecture is intentionally:

```text
GitHub event / invocation
          ↓
AgentCore Runtime
          ↓
Strands Agent
          ↓
GitHub Tool
          ↓
Deterministic Risk Engine
          ↓
History + Decision Support
          ↓
Risk Report
```

The repository does not claim that AgentCore has been deployed or verified in AWS.
Deployment requires environment-specific AWS configuration and validation. S3
report persistence support is implemented separately and is optional when
`S3_REPORT_BUCKET` is configured in a provisioned environment; it is not claimed
as an active AWS deployment.
