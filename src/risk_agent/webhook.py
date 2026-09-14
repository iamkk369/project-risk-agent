"""Minimal GitHub webhook server for event-driven local execution.

This is intentionally dependency-light so it can run with the existing Python
runtime. The handler boundary is reusable by a future AWS Lambda/API entry
point; this local server is the testable event-driven layer, not the final
cloud deployment.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any

from .event_handler import (
    append_event_log,
    event_should_trigger,
    handle_github_event,
    repository_matches,
)
from .main import run_pipeline


def verify_signature(secret: str, body: bytes, signature: str | None) -> bool:
    """Verify GitHub's HMAC SHA-256 webhook signature in constant time."""
    if not secret or not signature or not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


class _WebhookHandler(BaseHTTPRequestHandler):
    server_version = "ProjectRiskAgentWebhook/1.0"

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path == "/healthz":
            self._send_json(200, {"status": "ok", "service": "project-risk-agent"})
            return
        self._send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path != "/webhook/github":
            self._send_json(404, {"error": "not_found"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1_000_000:
                self._send_json(400, {"error": "invalid_payload_size"})
                return
            body = self.rfile.read(length)
        except (ValueError, OSError):
            self._send_json(400, {"error": "invalid_request"})
            return

        secret = os.getenv("GITHUB_WEBHOOK_SECRET")
        signature = self.headers.get("X-Hub-Signature-256")
        if not verify_signature(secret or "", body, signature):
            self._send_json(401, {"error": "invalid_signature"})
            return

        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "invalid_json"})
            return
        if not isinstance(payload, dict):
            self._send_json(400, {"error": "invalid_payload"})
            return

        event_name = self.headers.get("X-GitHub-Event", "")
        delivery_id = self.headers.get("X-GitHub-Delivery")
        configured_repo = os.getenv("GITHUB_REPO")
        log_path = os.getenv("WEBHOOK_EVENT_LOG", ".webhook_events.jsonl")

        def trigger() -> str:
            return run_pipeline()

        # Execute analysis in a background thread so GitHub receives a quick
        # acknowledgement rather than waiting for the LLM/GitHub API call.
        result: dict[str, Any]
        try:
            if event_name in {"issues", "push", "pull_request"}:
                if not repository_matches(payload, configured_repo):
                    result = {"status": "rejected", "triggered": False, "reason": "repository_mismatch"}
                elif not event_should_trigger(event_name, payload):
                    result = {"status": "ignored", "triggered": False, "reason": "event_action_not_triggering"}
                else:
                    Thread(target=trigger, daemon=True).start()
                    result = {"status": "accepted", "triggered": True, "event": event_name}
            else:
                result = handle_github_event(event_name, payload, lambda: None, configured_repo)
        except Exception as exc:  # Keep webhook response safe; do not expose secrets.
            result = {"status": "error", "triggered": False, "reason": type(exc).__name__}

        append_event_log(
            log_path,
            {
                "delivery_id": delivery_id,
                "event": event_name,
                "repository": configured_repo,
                "result": result,
            },
        )
        status = 202 if result.get("triggered") else 200
        if result.get("status") == "rejected":
            status = 403
        self._send_json(status, result)

    def log_message(self, format: str, *args: Any) -> None:
        # Keep default HTTP logging but never print request bodies/secrets.
        super().log_message(format, *args)


def run_webhook_server(host: str = "127.0.0.1", port: int = 8080) -> None:
    """Start the local GitHub webhook listener."""
    if not os.getenv("GITHUB_WEBHOOK_SECRET"):
        raise RuntimeError("Set GITHUB_WEBHOOK_SECRET before starting the webhook server.")
    if not os.getenv("GITHUB_REPO"):
        raise RuntimeError("Set GITHUB_REPO before starting the webhook server.")

    server = ThreadingHTTPServer((host, port), _WebhookHandler)
    print(f"Webhook server listening on http://{host}:{port}")
    print("GitHub endpoint: POST /webhook/github")
    print("Health endpoint: GET  /healthz")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping webhook server...")
    finally:
        server.server_close()
