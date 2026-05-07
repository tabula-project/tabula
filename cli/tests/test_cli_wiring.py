"""Smoke tests for the top-level argparse wiring."""

from __future__ import annotations

import pytest

from tabula_cli.cli import _make_parser


def test_parser_accepts_enclave_down() -> None:
    parser = _make_parser()
    ns = parser.parse_args(["enclave", "down", "demo", "--yes"])
    assert ns.cmd == "enclave"
    assert ns.enclave_cmd == "down"
    assert ns.name == "demo"
    assert ns.yes is True
    assert ns.force is False
    assert ns.project is None


def test_parser_accepts_force_recovery() -> None:
    parser = _make_parser()
    ns = parser.parse_args(
        ["enclave", "down", "demo", "--yes", "--force", "--project", "p1"]
    )
    assert ns.force is True
    assert ns.project == "p1"


def test_parser_rejects_missing_enclave_subcommand() -> None:
    parser = _make_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["enclave"])
