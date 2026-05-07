"""CLI entrypoint installed at ``/opt/tabula/wake-gpu`` on the classifier VM.

Usage::

    /opt/tabula/wake-gpu               # default 90s timeout
    /opt/tabula/wake-gpu --timeout 60  # override

Exit codes:

* ``0`` — agent ready (whether already-running or freshly started)
* ``1`` — generic API error
* ``2`` — timed out waiting for VM RUNNING and/or agent health
* ``3`` — permission denied (IAM contract drift; see #15)

The CLI exists separately from the HTTP server so operators can wake the
GPU manually for debugging without going through the wake server. The two
share the same underlying :func:`tabula_wake.wake_gpu.wake_gpu` and the same
config-loading code, so behavior is identical.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Optional, Sequence

from tabula_wake.config import load as load_config
from tabula_wake.wake_gpu import WakeOutcome, wake_gpu

_EXIT_OK = 0
_EXIT_API_ERROR = 1
_EXIT_TIMEOUT = 2
_EXIT_PERMISSION_DENIED = 3


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wake-gpu",
        description=(
            "Wake the Tabula enclave's GPU VM and wait until its agent is "
            "ready. Uses the classifier service account's narrowly scoped "
            "compute.instances.start permission (see issue #15)."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=90.0,
        help="Total wall-clock seconds to wait for VM-RUNNING + agent-ready (default: 90s)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="Seconds between GCE state polls and health checks (default: 2.0)",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to JSON config file (overrides $TABULA_WAKE_CONFIG and metadata server)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the wake result as one line of JSON to stdout (for scripting).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress informational logging on stderr.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="[wake-gpu] %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    # Lazy import so missing google-cloud-compute does not break ``--help``.
    try:
        from google.cloud import compute_v1  # type: ignore
    except ImportError as exc:  # pragma: no cover — production install issue
        print(
            f"google-cloud-compute is not installed: {exc}", file=sys.stderr
        )
        return _EXIT_API_ERROR

    config = load_config(
        config_path=None if args.config is None else _as_path(args.config),
    )
    client = compute_v1.InstancesClient()

    result = wake_gpu(
        config=config,
        client=client,
        timeout_s=args.timeout,
        poll_interval_s=args.poll_interval,
    )

    if args.json:
        print(json.dumps(result.to_log_fields(), separators=(",", ":")))
    else:
        logging.info(
            "wake outcome=%s duration_ms=%d detail=%s",
            result.outcome.value,
            result.duration_ms,
            result.detail or "-",
        )

    return _exit_code_for(result.outcome)


def _as_path(s: str):
    from pathlib import Path

    return Path(s)


def _exit_code_for(outcome: WakeOutcome) -> int:
    if outcome in (WakeOutcome.ALREADY_RUNNING, WakeOutcome.STARTED):
        return _EXIT_OK
    if outcome is WakeOutcome.TIMEOUT:
        return _EXIT_TIMEOUT
    if outcome is WakeOutcome.PERMISSION_DENIED:
        return _EXIT_PERMISSION_DENIED
    return _EXIT_API_ERROR


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
