#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Render Pleochroic Search's final settings.yml.

SearXNG itself only ever loads ONE user settings file (see
searx/settings_loader.py): whatever SEARXNG_SETTINGS_PATH points at, merged
once onto searx/settings.yml via `use_default_settings`. There's no built-in
second layer, so Pleochroic Search adds one at deploy time: this script
deep-merges the public settings template with a private, root-owned overlay
that carries the residential proxy credentials, and writes the result to the
path SearXNG actually loads.

    merge_settings.py <public.yml> <private.yml> <output.yml>

- <public.yml>  pleochroic/settings.yml, committed to the fork, no secrets.
- <private.yml> the root-owned overlay (see settings.private.example.yml).
                If it doesn't exist yet, the public template is written out
                unchanged — this keeps `install.sh` idempotent and lets the
                service start (without a working proxy) before the operator
                has filled in real credentials.
- <output.yml>  the generated file SEARXNG_SETTINGS_PATH points at.

Run by install.sh once at install time, and again by
pleochroic-search.service's ExecStartPre on every (re)start — so editing the
private file and running `systemctl restart pleochroic-search` is enough to
pick up rotated proxy credentials, no reinstall needed.

Only PyYAML is used, which SearXNG already depends on (requirements.txt).
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml


def deep_merge(base: dict, overlay: dict) -> dict:
    """Merge `overlay` into `base` in place, recursing into nested dicts.

    Mirrors the semantics of searx.settings_loader.update_dict (used by
    SearXNG itself for use_default_settings merging): dict values are merged
    key by key, everything else is a straight overwrite.
    """
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
