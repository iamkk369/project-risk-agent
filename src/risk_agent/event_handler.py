"""Event-driven GitHub webhook handling for the Project Risk Agent.

The webhook layer deliberately triggers a fresh repository analysis instead of
trusting partial webhook payloads as the source of truth. This keeps the risk
engine deterministic and means the same pipeline is used for manual and event
triggered runs.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

SUPPORTED_EVENTS = {"issues", "push", "pull_request"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def event_should_trigger(event_name: str, payload: dict[str, Any]) -> bool:
    """Return True when a GitHub event represents a repository change worth scanning."""
    if event_name == "issues":
        return payload.get("action") in {
            "opened",
            "edited",
            "closed",
            "reopened",
            "labeled",
            "unlabeled",
            "milestoned",
            "demilestoned",
        }
    if event_name == "push":
        return bool(payload.get("ref"))
    if event_name == "pull_request":
        return payload.get("action") in {
            "opened",
            "edited",
            "closed",
            "reopened",
            "synchronize",
        }
    return False


def repository_matches(payload: dict[str, Any], configured_repo: str | None) -> bool:
    """Ensure a webhook cannot trigger analysis for an unrelated repository."""
    if not configured_repo:
        return False
    repository = payload.get("repository") or {}
    full_name = repository.get("full_name")
    return isinstance(full_name, str) and full_name.lower() == configured_repo.lower()


def handle_github_event(
    event_name: str,
    payload: dict[str, Any],
    run_analysis: Callable[[], str],
    configured_repo: str | None = None,
) -> dict[str, Any]:
    """Validate an event semantically and enqueue/run the analysis callback.

    The callback is intentionally injected so the same handler can be used by
    a local webhook server now and an AWS event handler later.
    """
    if event_name == "ping":
        return {"status": "accepted", "triggered": False, "reason": "ping"}

    if event_name not in SUPPORTED_EVENTS:
        return {"status": "ignored", "triggered": False, "reason": "unsupported_event"}

    if not repository_matches(payload, configured_repo):
        return {"status": "rejected", "triggered": False, "reason": "repository_mismatch"}

    if not event_should_trigger(event_name, payload):
        return {"status": "ignored", "triggered": False, "reason": "event_action_not_triggering"}

    run_analysis()
    return {
        "status": "accepted",
        "triggered": True,
        "event": event_name,
        "delivery_action": payload.get("action"),
    }


def append_event_log(path: str | os.PathLike[str], event: dict[str, Any]) -> None:
    """Append a small JSONL audit record for webhook deliveries."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    record = {"timestamp": _utc_now(), **event}
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
