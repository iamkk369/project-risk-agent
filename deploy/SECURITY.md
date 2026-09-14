# Security, IAM and Observability

P7 hardens the AWS runtime without changing the risk-analysis model.

## Credential boundary

Credentials are never committed to source control. The runtime supports either:

1. environment-injected values for local development, or
2. AWS Secrets Manager ARNs for deployed execution.

Configure these optional ARN variables when using Secrets Manager:

```text
GITHUB_TOKEN_SECRET_ARN
GEMINI_API_KEY_SECRET_ARN
```

The application caches a retrieved secret only in process memory and never logs
secret values.

## IAM

`deploy/iam-policy.json` is a deliberately narrow starting policy. Replace
`ACCOUNT_ID` with the deployment account and scope each secret ARN to the exact
secret(s) used by the runtime. The runtime's normal AWS service permissions
should remain those provisioned by AgentCore; do not grant broad `*` actions to
this application role.

## Observability

The application emits structured JSON events designed for CloudWatch:

- `analysis_started`
- `analysis_completed`
- `agent_invocation_started`
- `agent_invocation_completed`

Logs contain request correlation IDs and operational metadata, but not prompts,
GitHub tokens, Gemini API keys, webhook bodies, or generated credentials.

## Webhook security

GitHub webhook requests continue to require `X-Hub-Signature-256` HMAC
verification and a configured `GITHUB_WEBHOOK_SECRET`.

## Production boundary

P7 does not add a database, cache, public API gateway, or broad network access.
The agent remains read-only against GitHub for risk analysis and does not
silently mutate project data.
