"""``tabula enclave`` Typer subcommand group.

Subcommands:

- ``up``     -- provision an enclave (issue #26, see :mod:`.up`)
- ``down``   -- tear an enclave down (issue #28, see :mod:`.down`)
- ``ssh``    -- IAP-tunneled shell into a role VM (issue #33, see :mod:`.ssh`)
- ``status`` -- read-only health report (issue #30, see :mod:`.status`)

The package re-exports the down-side public API (``DownOptions``,
``Leftover``, ``TerraformResult``, ``enclave_down``, the ``EXIT_*``
constants) at the package root so existing test imports of the form
``from tabula_cli.enclave import enclave_down`` keep working after the
``enclave`` module was promoted to a multi-file package.

The legacy argparse entry points (``add_subparser`` / ``run``) are also
re-exported because the ``ssh`` subcommand and its tests still rely on the
shared argparse plumbing; the top-level :mod:`tabula_cli.main` Typer app is
the user-facing entry point but the argparse wrappers remain importable for
internal consumers and migration glue.
"""

from __future__ import annotations

from typing import Optional

import typer

from tabula_cli.enclave import down as down_mod
from tabula_cli.enclave import ssh as ssh_mod
from tabula_cli.enclave import status as status_mod
from tabula_cli.enclave import up as up_mod
from tabula_cli.enclave.down import (
    DESTROY_WARNING,
    DownOptions,
    EXIT_LEFTOVERS,
    EXIT_OK,
    EXIT_TERRAFORM_FAILURE,
    EXIT_USER_ERROR,
    Leftover,
    TerraformResult,
    enclave_down,
)

# Typer subapp registered by the top-level ``tabula`` CLI.
app = typer.Typer(
    name="enclave",
    help="Provision and manage Tabula enclaves on GCP.",
    no_args_is_help=True,
)

# Mount the ``up`` and ``down`` Typer-native commands.
app.command("up", help="Provision (or re-apply) an enclave.")(up_mod.up)
app.command("down", help="Tear an enclave down to zero residual cost.")(
    down_mod.down
)


# ssh.py exposes its argparse-style ``enclave_ssh(opts)`` orchestrator and an
# ``SshOptions`` dataclass; we wrap it in a small Typer command so the user
# surface is uniform.

@app.command(
    "ssh",
    help="IAP-tunneled SSH into a Tabula enclave VM.",
    no_args_is_help=True,
)
def _ssh_cmd(
    name: str = typer.Argument(..., help="Enclave name (DNS-safe)."),
    role: str = typer.Argument(
        ...,
        help="Role VM to SSH into. One of: classifier, gpu, gitea.",
    ),
    command: Optional[str] = typer.Option(
        None,
        "--command",
        help="Run COMMAND on the remote host and exit (no interactive shell).",
    ),
    forward_agent: bool = typer.Option(
        False,
        "--forward-agent",
        help="Enable SSH agent forwarding (passes -A to gcloud).",
    ),
    no_start: bool = typer.Option(
        False,
        "--no-start",
        help=(
            "If the target VM is STOPPED, do not prompt to start it; "
            "fail with exit 2 instead. Useful for scripted runs."
        ),
    ),
) -> None:
    """SSH into ``classifier`` / ``gpu`` / ``gitea`` for an enclave via IAP."""
    opts = ssh_mod.SshOptions(
        name=name,
        role=role,
        command=command,
        forward_agent=bool(forward_agent),
        no_start=bool(no_start),
    )
    rc = ssh_mod.enclave_ssh(opts)
    raise typer.Exit(code=rc)


# Mount the ``status`` Typer-native command (issue #30).
app.command(
    "status",
    help="Report enclave health and per-VM state (read-only).",
)(status_mod.status)


# --------------------------------------------------------------------------- #
# Legacy argparse wrappers retained for ssh.py and any callers still using    #
# the argparse parser API. These are not part of the user-facing CLI surface  #
# (the Typer ``app`` above is); they exist for backwards compatibility with   #
# tooling and tests authored against the pre-rename layout.                   #
# --------------------------------------------------------------------------- #

import argparse  # noqa: E402  -- intentional: keep argparse import opt-in


def add_subparser(subparsers: "argparse._SubParsersAction") -> None:
    """Register the ``enclave`` argparse subcommand group.

    Retained so callers that previously did
    ``from tabula_cli.enclave import add_subparser`` keep working. New code
    should target the Typer ``app`` instead.
    """
    p = subparsers.add_parser(
        "enclave",
        help="Manage Tabula enclaves (one-command lifecycle).",
        description=(
            "Manage Tabula enclaves. An enclave is one GCP project's worth "
            "of isolated infrastructure (classifier + GPU + Gitea + network)."
        ),
    )
    sub = p.add_subparsers(dest="enclave_cmd", required=True)
    down_mod.add_subparser(sub)
    ssh_mod.add_subparser(sub)


def run(args: "argparse.Namespace") -> int:
    """Dispatch the parsed argparse ``enclave`` subcommand."""
    if args.enclave_cmd == "down":
        return down_mod.run(args)
    if args.enclave_cmd == "ssh":
        return ssh_mod.run(args)
    print(
        f"error: unknown enclave subcommand: {args.enclave_cmd}",
    )
    return EXIT_USER_ERROR


__all__ = [
    "DESTROY_WARNING",
    "DownOptions",
    "EXIT_LEFTOVERS",
    "EXIT_OK",
    "EXIT_TERRAFORM_FAILURE",
    "EXIT_USER_ERROR",
    "Leftover",
    "TerraformResult",
    "add_subparser",
    "app",
    "enclave_down",
    "run",
]
