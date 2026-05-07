#!/usr/bin/env python3
"""Mimic Terraform's templatefile() for cloud-init.yaml.tftpl so we can
validate the rendered cloud-config locally without a full Terraform apply.

This is a dev-only utility — it intentionally only implements the small
slice of HCL templating used by cloud-init.yaml.tftpl: the `${indent(N, x)}`
function and a single `${var}` interpolation for `bootstrap_script`.

Usage:
    ./render-cloud-init.py > /tmp/cloud-init.rendered.yaml
    cloud-init devel schema --config-file /tmp/cloud-init.rendered.yaml

The output is also valid input for `python3 -c 'import yaml; yaml.safe_load(...)'`
which is what the test harness does on macOS where cloud-init isn't installable.
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE = os.path.dirname(HERE)
TEMPLATE = os.path.join(MODULE, "cloud-init.yaml.tftpl")
BOOTSTRAP = os.path.join(MODULE, "bootstrap.sh")


def hcl_indent(n: int, text: str) -> str:
    """Approximate Terraform's indent(n, s): prepend n spaces to every line
    after the first. cloud-init expects the script body indented under the
    YAML `content: |` literal block."""
    pad = " " * n
    lines = text.split("\n")
    if not lines:
        return text
    head, *tail = lines
    return head + "".join("\n" + (pad + line if line else "") for line in tail)


def render(template: str, bootstrap_script: str) -> str:
    # Match ${indent(6, bootstrap_script)} or ${bootstrap_script}.
    def sub(match: "re.Match[str]") -> str:
        expr = match.group(1).strip()
        if expr == "bootstrap_script":
            return bootstrap_script
        m = re.match(r"indent\((\d+),\s*bootstrap_script\)", expr)
        if m:
            return hcl_indent(int(m.group(1)), bootstrap_script)
        raise ValueError(f"unsupported template expression: {expr!r}")

    return re.sub(r"\$\{([^}]+)\}", sub, template)


def main() -> int:
    with open(TEMPLATE, "r", encoding="utf-8") as f:
        template = f.read()
    with open(BOOTSTRAP, "r", encoding="utf-8") as f:
        script = f.read()
    sys.stdout.write(render(template, script))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
