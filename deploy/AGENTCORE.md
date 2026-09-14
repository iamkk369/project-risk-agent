# AgentCore Runtime Deployment

P6 moves the existing Strands Project Risk Agent into **Amazon Bedrock AgentCore Runtime**.
The agent continues to use Google Gemini; AgentCore is the runtime/deployment layer, not a forced model-provider change.

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

From the project root, use the AgentCore CLI to register the existing Python entrypoint as a Strands agent using the **Gemini** model provider and **CodeZip** build. CodeZip is intentionally selected because it does not require Docker.

The AgentCore CLI supports CodeZip deployment and synthesizes/deploys the required AWS resources through CDK.

The runtime entrypoint in this repository is:

```text
src/risk_agent/agentcore_app.py
```

Its contract is:

```text
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

`GITHUB_WEBHOOK_SECRET` is retained for the P5 webhook boundary.

For P6, secrets must be injected into the runtime rather than stored in source control.

## Local AgentCore validation

Before deploying:

```bash
agentcore dev
```

Then invoke the local runtime using the CLI tooling.

## Deploy

Use a CodeZip AgentCore deployment:

```bash
agentcore deploy --dry-run
agentcore deploy
agentcore status
```

Then invoke the deployed runtime:

```bash
agentcore invoke --prompt "Analyze the configured project and identify the highest priority delivery risk."
```

## Important architecture boundary

P6 does **not** add Lambda, Fargate, RDS, Redis, or API Gateway merely to increase service count.

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

Durable history storage is deliberately deferred to P9, where S3 can be introduced if it is still justified.
