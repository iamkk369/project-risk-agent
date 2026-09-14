"""Optional durable report storage on Amazon S3.

Local execution remains the default. When S3_REPORT_BUCKET is configured,
reports are persisted to S3 using boto3 with server-side encryption.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

try:
    import boto3
except ModuleNotFoundError:
    class _MissingBoto3:
        def client(self, *_args, **_kwargs):
            raise RuntimeError("S3 persistence requires boto3.")

    boto3 = _MissingBoto3()

try:
    from botocore.exceptions import BotoCoreError, ClientError
except ModuleNotFoundError:
    class _MissingBotocoreError(Exception):
        pass

    BotoCoreError = ClientError = _MissingBotocoreError


def _safe_repo_key(repo: str) -> str:
    return repo.strip().replace("/", "-").replace("\\", "-")


def persist_report_to_s3(report: str, repo: str, *, bucket: str | None = None) -> str | None:
    """Persist a Markdown report to S3 when a bucket is configured.

    Returns the S3 URI, or None when S3 persistence is not configured.
    Storage errors are surfaced so a configured cloud deployment cannot
    silently report success when durable persistence failed.
    """
    bucket = bucket or os.getenv("S3_REPORT_BUCKET")
    if not bucket:
        return None

    prefix = os.getenv("S3_REPORT_PREFIX", "risk-reports").strip("/")
    timestamp = datetime.now(timezone.utc).strftime("%Y/%m/%d/%H%M%S-%f")
    key_parts = [part for part in (prefix, _safe_repo_key(repo), f"risk-report-{timestamp}.md") if part]
    key = "/".join(key_parts)

    client = boto3.client("s3", region_name=os.getenv("AWS_REGION") or None)
    try:
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=report.encode("utf-8"),
            ContentType="text/markdown; charset=utf-8",
            ServerSideEncryption="AES256",
        )
    except (BotoCoreError, ClientError) as exc:
        raise RuntimeError(f"Failed to persist risk report to S3 bucket '{bucket}': {exc}") from exc

    return f"s3://{bucket}/{key}"
