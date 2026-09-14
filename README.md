# 🚨 Project Risk Agent

An AI agent built with **Strands Agents SDK** that investigates a project's open GitHub issues — deadlines, dependencies, and status — and produces a prioritized, action-oriented risk report for the team.

Built for the **AWS Agents for Humans Hackathon 2026** — Professional Agents track.

---

## The Problem

Project teams already have all the information they need buried in tools like GitHub: tasks, deadlines, and dependencies. But nobody has time to manually trace how one delayed task cascades into missed milestones. A small delay in one issue can silently block two, three, or more downstream tasks — and by the time someone notices, the damage is done.

## Who It's For

Engineering managers, tech leads, and small dev teams who track work in GitHub Issues and want an early warning system for delivery risk — without adding another dashboard to check manually.

## Why It Matters

The agent doesn't just summarize the project — it **investigates** it. It reads every open issue, traces dependency relationships in both directions, measures downstream impact and dependency-chain depth, identifies cascading risk, determines overall project health and the primary bottleneck, and reasons about *why* a delay matters before recommending what to do next. That's the difference between a status report and a risk report.

---

## How It Works

```
GitHub Issues (open, with due dates + "blocks #N" / "blocked by #N" references)
        │
        ▼
┌───────────────────┐
│   GitHub Tool      │  fetches issues, parses due dates & dependencies
└─────────┬──────────┘
          ▼
┌───────────────────┐
│   Risk Engine      │  explainable, rule-based scoring (not a black box)
└─────────┬──────────┘
          ▼
┌───────────────────┐
│  Strands Agent     │  wraps the pipeline as a tool, adds an executive
│  (Google Gemini)   │  summary on top for the reader
└─────────┬──────────┘
          ▼
┌───────────────────┐
│  Markdown Report   │  project health + bottleneck + prioritized
│                    │  issues, evidence + recommended action
└───────────────────┘
```

See [`docs/architecture.md`](./docs/architecture.md) for the full diagram.

### Risk Scoring Logic

Every issue gets a risk level with a **plain-English reason** — no opaque ML model:

| Level | Condition |
|---|---|
| 🔴 High | Overdue and has downstream impact on other open issues |
| 🟡 Medium | Overdue but isolated, **or** has multi-issue downstream impact, **or** due very soon and blocks something |
| 🟢 Low | On track, no meaningful dependency risk |

---

## Setup

### 1. Clone and install

```bash
git clone https://github.com/iamkk369/risk-agent-demo.git
cd risk-agent-demo
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux

pip install -r requirements.txt
```

### 2. Configure environment

Copy `config/env.example` to `.env` and fill in:

```
GITHUB_TOKEN=your_github_personal_access_token
GITHUB_REPO=owner/repo-name
GEMINI_API_KEY=your_gemini_api_key
```

Your GitHub token needs `repo` scope to read issues.

### 3. Gemini API key (for the full agent run)

Set your Gemini API key in the environment as `GEMINI_API_KEY` so the Strands agent can call the Gemini model for the executive summary step.

### 4. Run it

```bash
# Full run — Strands Agent + Gemini (adds an executive summary)
python -m src.risk_agent.main

# Local run — pipeline only, no Gemini needed (useful for testing)
python -m src.risk_agent.main --local
```

The report prints to the terminal and saves to `risk_report.md`.

---

## Demo Repo

This project ships with a small demo repository (`risk-agent-demo`) containing 8 issues that simulate a realistic "Payment Gateway Migration" project, deliberately seeded with:
- One overdue task blocking two others (→ High Risk cascade)
- One near-deadline task blocking downstream QA (→ Medium Risk)
- Several on-track, independent tasks (→ Low Risk)

This makes the demo reproducible: run it against the same repo and you'll see the same risk chain every time.

---

## Tech Stack

- **Python 3.14**
- **Strands Agents SDK** — agent framework and tool orchestration
- **Google Gemini** — model provider for the agent's reasoning layer
- **GitHub REST API** — project data source
- Deterministic Python risk-scoring engine (no ML — every score is explainable)

## Project Structure

```
├── src/risk_agent/    # Application package
│   ├── main.py           # Entry point — manual, local, and webhook modes
│   ├── github_tool.py    # Fetches + parses GitHub issues
│   ├── risk_engine.py    # Dependency graph + explainable risk scoring
│   ├── history.py        # Risk trajectory comparison
│   ├── decision_support.py # Manager decision prioritization
│   ├── event_handler.py  # Event filtering and repository validation
│   ├── webhook.py        # Signed GitHub webhook listener
│   ├── report.py         # Markdown report generation
│   └── storage.py        # Optional S3 report persistence
├── config/
│   └── env.example    # Safe configuration template
├── docs/
│   └── architecture.md
├── requirements.txt
└── .gitignore
```

## AWS Runtime Deployment

The project can be hosted as a Strands agent on **Amazon Bedrock AgentCore Runtime** while continuing to use Google Gemini as the model provider. AgentCore supports Strands and external foundation models, including Gemini, so AWS runtime adoption does not require a model-provider rewrite.

The AgentCore entrypoint is `src/risk_agent/agentcore_app.py`. Deployment instructions are in [`deploy/AGENTCORE.md`](./deploy/AGENTCORE.md). The P6 deployment path uses CodeZip rather than adding Docker, Lambda, Fargate, RDS, Redis, or API Gateway without a concrete need.

## What's Out of Scope (for this MVP)

Multi-agent orchestration, Jira/Linear integration, Slack notifications, automatic task modification, and a web dashboard were deliberately left out to keep the agent's core reasoning solid and demo-ready within the hackathon timeline. See `architecture.md` for future directions.

## License

MIT

### Risk trajectory
Each successful analysis can retain a local, bounded history in `.risk_history.json` (configurable with `RISK_HISTORY_FILE`). The report compares the current state with the previous snapshot and identifies new, escalating, de-escalating, persistent, and resolved risks. This is intentionally provider-neutral so the history store can later move to durable AWS storage without changing the risk-analysis contract.

## Event-Driven Execution

The agent can also run from GitHub repository events instead of only a manual command. A signed GitHub webhook for issue, push, or pull-request changes triggers a fresh repository analysis in a background worker. The webhook layer acknowledges the delivery quickly and the analysis re-fetches the repository as the source of truth rather than trusting partial event payloads.

```bash
python -m src.risk_agent.main --webhook
```

Configure `GITHUB_WEBHOOK_SECRET` and `GITHUB_REPO`. The local listener exposes `POST /webhook/github` and `GET /healthz`. The event handler is intentionally separated from the HTTP server so the same trigger contract can be reused by a future AWS event/runtime deployment.

## Multi-project portfolio mode

Set `PROJECT_REPOS` to a comma-separated list of GitHub `owner/repository` values and run:

```bash
python -m src.risk_agent.main --portfolio
```

Each repository is analyzed independently using the same deterministic risk engine. The portfolio layer then ranks projects by overall posture and delivery exposure without mixing issue IDs between repositories. The generated `portfolio_risk_report.md` is local and ignored by Git.

## S3 Report Persistence

S3 persistence is optional. When `S3_REPORT_BUCKET` is configured, each successful project risk report is stored as a Markdown object under `S3_REPORT_PREFIX/<owner-repo>/` using server-side encryption (AES-256). Local report generation continues to work when no bucket is configured.

The S3 layer is intentionally isolated in `src/risk_agent/storage.py`, so cloud storage does not change the deterministic risk-analysis contract.
