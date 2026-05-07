"""Internal-only HTTP server that exposes ``POST /wake`` on the classifier.

This server is consumed by other classifier-resident processes — primarily
the Noise terminator from Epic #13 — to trigger a GPU wake on demand. It is
**not** externally reachable: the firewall module (#24) only allows
intra-VPC traffic to the wake port, and the systemd unit binds the server
to ``127.0.0.1`` plus the classifier's internal NIC IP, never to a public
interface.

The server is deliberately tiny — Flask + a single route. We use Flask
rather than FastAPI to keep the runtime footprint small (no uvicorn, no
async machinery) and because the issue's implementation notes suggest it.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from tabula_wake.config import load as load_config
from tabula_wake.wake_gpu import (
    GpuInstanceConfig,
    WakeOutcome,
    WakeResult,
    wake_gpu,
)

logger = logging.getLogger("tabula.wake.server")

# Default port — coordinated with firewall issue #24 (variable
# ``wake_signal_port`` defaults to 8088 there too).
DEFAULT_PORT = 8088


def create_app(
    *,
    config: Optional[GpuInstanceConfig] = None,
    client: Optional[Any] = None,
    timeout_s: float = 90.0,
) -> "Any":
    """Build the Flask application.

    Parameters
    ----------
    config:
        Pre-loaded :class:`GpuInstanceConfig`. If ``None``, the server loads
        it lazily on the first request (so a server can boot before the GPU
        VM exists, which simplifies the bring-up order).
    client:
        Pre-built compute client. If ``None``, the server constructs a real
        ``google.cloud.compute_v1.InstancesClient`` lazily. Tests inject a
        fake here.
    timeout_s:
        Default per-request timeout. Callers can override via
        ``?timeout=`` on the request.
    """
    # Lazy import so ``import tabula_wake.server`` works in environments
    # where Flask is not installed (e.g., type-checkers in CI). The server
    # is only ever *run* on the classifier VM, where Flask is installed by
    # cloud-init.
    from flask import Flask, jsonify, request  # type: ignore

    app = Flask(__name__)
    state: dict[str, Any] = {"config": config, "client": client}

    @app.post("/wake")
    def wake() -> Any:
        nonlocal state
        try:
            cfg = state["config"] or load_config()
            state["config"] = cfg
            cli = state["client"] or _make_default_client()
            state["client"] = cli
        except Exception as exc:  # noqa: BLE001
            logger.exception("wake config/client init failed")
            return jsonify({"error": f"init failed: {exc}"}), 500

        try:
            req_timeout = float(request.args.get("timeout", timeout_s))
        except ValueError:
            return jsonify({"error": "invalid 'timeout' query parameter"}), 400

        result = wake_gpu(config=cfg, client=cli, timeout_s=req_timeout)
        _log_result(result, instance_name=cfg.instance_name)

        status = _http_status_for(result)
        body = result.to_log_fields()
        body["instance_name"] = cfg.instance_name
        return jsonify(body), status

    @app.get("/healthz")
    def healthz() -> Any:
        # Liveness only — does not touch GCE. Useful for the systemd unit's
        # ExecStartPost smoke test.
        return jsonify({"ok": True}), 200

    return app


def _make_default_client() -> Any:
    from google.cloud import compute_v1  # type: ignore

    return compute_v1.InstancesClient()


def _http_status_for(result: WakeResult) -> int:
    if result.outcome in (WakeOutcome.ALREADY_RUNNING, WakeOutcome.STARTED):
        return 200
    if result.outcome is WakeOutcome.TIMEOUT:
        return 504
    if result.outcome is WakeOutcome.PERMISSION_DENIED:
        return 403
    return 502


def _log_result(result: WakeResult, *, instance_name: str) -> None:
    """Emit one structured log line per wake call.

    The schema (``requested_at``, ``started_at``, ``ready_at``,
    ``duration_ms``) is the contract the issue specifies for Cloud Logging.
    Cloud Logging picks up the ``extra`` dict via the standard logging
    JSON formatter that the systemd unit configures.
    """
    logger.info(
        "wake call complete",
        extra={
            "tabula_wake": {
                "instance_name": instance_name,
                **result.to_log_fields(),
            }
        },
    )


def main() -> int:  # pragma: no cover — exercised by systemd, not unit tests
    """``python -m tabula_wake.server`` entrypoint for the systemd unit."""
    logging.basicConfig(
        level=logging.INFO,
        format="[wake-server] %(levelname)s %(message)s",
    )
    host = os.environ.get("TABULA_WAKE_HOST", "127.0.0.1")
    port = int(os.environ.get("TABULA_WAKE_PORT", str(DEFAULT_PORT)))
    timeout_s = float(os.environ.get("TABULA_WAKE_TIMEOUT_S", "90"))

    app = create_app(timeout_s=timeout_s)
    # Flask's built-in server is fine here — the wake endpoint is an
    # internal, low-QPS RPC. We are not in any sense serving a web app.
    app.run(host=host, port=port, debug=False, use_reloader=False)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
