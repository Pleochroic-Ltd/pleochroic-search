#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deep-merges the private overlay onto the public settings template.

    merge_settings.py <public.yml> <private.yml> <output.yml>

A missing <private.yml> writes the public template unchanged.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml


def deep_merge(base: dict, overlay: dict) -> dict:
    """Merge `overlay` into `base` in place, recursing into nested dicts."""
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(f"usage: {argv[0]} <public.yml> <private.yml> <output.yml>", file=sys.stderr)
        return 2

    public_path, private_path, output_path = (Path(p) for p in argv[1:4])

    with public_path.open(encoding="utf-8") as f:
        merged = yaml.safe_load(f) or {}

    if private_path.is_file():
        with private_path.open(encoding="utf-8") as f:
            private = yaml.safe_load(f) or {}
        deep_merge(merged, private)
    else:
        print(
            f"note: {private_path} not found yet, writing public settings only "
            "(outgoing.proxies will be unset — see settings.private.example.yml)",
            file=sys.stderr,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(merged, f, sort_keys=False, allow_unicode=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
