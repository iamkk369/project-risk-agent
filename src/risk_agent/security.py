"""Runtime security helpers for local and AWS execution.

The application prefers runtime-injected environment variables. In AWS, a
secret ARN can be supplied for a credential and the value is fetched from
Secrets Manager. Secret values are cached only for the lifetime of the process
and are never written to logs.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache


def _secret_arn(env_name: str) -> str | None:
    value = os.getenv(env_name)
    return value.strip() if value and value.strip() else None


@lru_cache(maxsize=16)
def get_secret(secret_arn: str) -> str:
    """Read one plaintext or JSON-wrapped secret from Secrets Manager."""
    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError

        client = boto3.client("secretsmanager", region_name=os.getenv("AWS_REGION"))
        response = client.get_secret_value(SecretId=secret_arn)
    except (BotoCoreError, ClientError) as exc:
        raise RuntimeError("Unable to retrieve configured AWS secret.") from exc

    value = response.get("SecretString")
    if value is None:
        raise RuntimeError("Configured AWS secret does not contain SecretString.")

    # Support either a plain secret or a JSON object containing `value`.
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return value
    if isinstance(decoded, dict) and isinstance(decoded.get("value"), str):
        return decoded["value"]
    return value


def get_credential(env_name: str, secret_arn_env_name: str | None = None) -> str | None:
    """Resolve a credential from an AWS secret when configured, else env."""
    if secret_arn_env_name:
        arn = _secret_arn(secret_arn_env_name)
        if arn:
            return get_secret(arn)
    value = os.getenv(env_name)
    return value.strip() if value and value.strip() else None
