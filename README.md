# Project Risk Agent

Project Risk Agent analyzes authorized GitHub repositories and turns open issue
data into an explainable delivery-risk report. It helps engineering managers,
tech leads, and delivery teams find bottlenecks before a delayed task cascades
into missed milestones.

The product is read-only: it fetches GitHub issues, evaluates risk, and
recommends decisions. It does not modify issues or reassign work.

## What it does

- Fetches all open GitHub issues with pagination and excludes pull requests.
- Parses due dates plus `blocks #N` and `blocked by #N` relationships.
- Scores issues with deterministic, auditable rules rather than opaque ML.
- Propagates dependency risk through direct and transitive downstream impact.
- Produces project posture, bottleneck, affected-issue, and dependency-chain summaries.
- Adds decision support with evidence, consequence of inaction, and a next step.
- Compares the current report with bounded local history to show trajectory.
- Analyzes one repository or an independent portfolio of repositories.
- Supports signed GitHub webhooks for event-driven re-analysis.
- Emits structured logs and supports environment variables or AWS Secrets Manager.
- Supports an AgentCore Runtime entrypoint and optional encrypted S3 report persistence; both are supported features, not yet deployed or verified in AWS.

## How the agent works

The deterministic pipeline is the source of truth:

```text
GitHub repository
      |
      v
GitHub REST API -> issue/dependency parsing -> deterministic risk engine
                                                   |
                                                   v
                                  history + decision support + Markdown report
                                                   ^
                                                   |
                                  Strands tool call / Google Gemini reasoning
```

**Strands** orchestrates the agent and exposes the report pipeline as a tool.
**Gemini** adds the executive explanation; it does not replace the scoring
rules. GitHub is the system of record. This separation keeps results
reproducible and makes each risk understandable to a manager.

Risk levels are based on overdue status, dependency exposure, urgency, and
propagated upstream risk:

| Level | Meaning |
| --- | --- |
| High | Overdue work with downstream impact, or equivalent propagated risk |
| Medium | Isolated overdue work, meaningful downstream exposure, or an urgent blocker |
| Low | On track with no meaningful dependency risk |

## Single-project and portfolio modes

Set `GITHUB_REPO=owner/repository` for the default target, or provide an
explicit repository to the agent-backed CLI:

```bash
python -m src.risk_agent.main --local --repo owner/repository
python -m src.risk_agent.main --repo owner/repository
```

For independent portfolio analysis, set `PROJECT_REPOS` to a comma-separated
list and run:

```bash
python -m src.risk_agent.main --portfolio
```

Issue numbers and history remain scoped to each repository.

## Event-driven execution

The local webhook listener verifies GitHub's HMAC SHA-256 signature, accepts
issue, push, and pull-request change events for the configured repository, and
starts a fresh analysis in the background:

```bash
python -m src.risk_agent.main --webhook
```

The listener exposes `POST /webhook/github` and `GET /healthz`.

## AgentCore and S3

`src/risk_agent/agentcore_app.py` is an optional Amazon Bedrock AgentCore
Runtime entrypoint for the same Strands + Gemini workflow. It accepts an
optional `repository` invocation field and is AgentCore-compatible for future
AWS deployment, but this repository does not claim that it has been deployed
or verified in AWS.

When `S3_REPORT_BUCKET` is configured in a running AWS environment, the
code path supports persisting encrypted Markdown objects under
`S3_REPORT_PREFIX/<owner-repository>/`. S3 support is implemented, but no
AWS deployment or live verification is claimed.

See [`deploy/AGENTCORE.md`](./deploy/AGENTCORE.md) and
[`deploy/SECURITY.md`](./deploy/SECURITY.md) for deployment and credential
guidance.

## Local setup

```bash
git clone <repository-url>
cd <repository-directory>
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux
pip install -r requirements.txt
```

Copy `config/env.example` to `.env` and set:

```text
GITHUB_TOKEN=your_github_token
GITHUB_REPO=owner/repository
GEMINI_API_KEY=your_gemini_api_key
```

The token must be authorized to read issues in the selected repositories.
`GEMINI_API_KEY` is required only for the Strands/Gemini run; `--local` runs
the deterministic pipeline without it. Optional settings cover webhooks,
history, logging, Secrets Manager, portfolio mode, and S3.

## Testing

```bash
python -m pytest -q
python -m compileall src tests
git diff --check
```

## Project structure

```text
project-risk-agent/
├── README.md
├── LICENSE
├── requirements.txt
├── requirements-agentcore.txt
├── pytest.ini
├── config/
├── src/risk_agent/
├── tests/
├── docs/
└── deploy/
```

The controlled payment-gateway repository `iamkk369/risk-agent-demo` is
retained only as a reproducible example/test data source. Project Risk Agent
itself is repository-agnostic.

## Current deployment status

Local deterministic, Strands/Gemini, portfolio, webhook, security, logging,
AgentCore-compatible entrypoint, and optional S3 persistence paths are implemented.
AgentCore and S3 support are optional capabilities and remain not yet deployed
or verified in AWS; they require environment-specific AWS configuration before
use in a live environment.

## License

MIT
