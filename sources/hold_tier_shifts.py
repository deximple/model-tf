#!/usr/bin/env python3
"""Hold back single-source tier flips so the rest of a refresh can land on main.

Without this, one tier flip backed by a single benchmark source sends the whole
refresh (facts + every tier/rank update) to a review PR, and main stops
updating until a human merges it. With it, the refresh workflow:

  1. saves the full refreshed model-tiers.json to --full-out,
  2. rewrites model-tiers.json so every gated entry keeps its previous tier
     (rank and evidence still refresh), and the workflow commits that to main,
  3. restores the full file, and the workflow opens a PR that contains only
     the held tier flips.

The held version also restores previous evidence checkedAt (when the note is
unchanged) and the previous bundle date when nothing else changed, so a
pending review PR does not cause a new commit on main every day.

Usage: python3 sources/hold_tier_shifts.py --gate GATE.json --full-out PATH
"""

import argparse
import copy
import json
import shutil
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_staleness import _load_head_json  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TIERS_PATH = ROOT / "model-tiers.json"


def hold_shifts(
    old_tiers: dict[str, Any],
    new_tiers: dict[str, Any],
    idents: list[list[str]] | list[tuple[str, str]],
) -> dict[str, Any]:
    """Return a copy of new_tiers whose gated entries keep their old tier."""
    held = copy.deepcopy(new_tiers)
    gated = {(k, e) for k, e in idents}
    old_t = {(e["key"], e["effort"]): e for e in old_tiers["entries"]}
    for entry in held["entries"]:
        ident = (entry["key"], entry["effort"])
        if ident not in gated or ident not in old_t:
            continue
        old_entry = old_t[ident]
        entry["tier"] = old_entry["tier"]
        old_ev = {(e["source"], e["note"]): e["checkedAt"] for e in old_entry["evidence"]}
        for ev in entry["evidence"]:
            prev_checked = old_ev.get((ev["source"], ev["note"]))
            if prev_checked is not None:
                ev["checkedAt"] = prev_checked
    if (
        held["entries"] == old_tiers["entries"]
        and held.get("factsDate") == old_tiers.get("factsDate")
    ):
        held["date"] = old_tiers["date"]
    return held


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate", required=True, help="gate JSON written by check_staleness.py --gate-out")
    parser.add_argument("--full-out", required=True, help="where to save the full (unheld) model-tiers.json")
    ns = parser.parse_args(argv if argv is not None else sys.argv[1:])

    gate = json.loads(Path(ns.gate).read_text(encoding="utf-8"))
    idents = gate.get("single_source_idents") or []
    shutil.copyfile(TIERS_PATH, ns.full_out)
    if not idents:
        print("hold_tier_shifts: no single-source tier shifts to hold")
        return

    old_tiers = _load_head_json("model-tiers.json")
    if old_tiers is None:
        sys.exit("hold_tier_shifts: cannot read HEAD:model-tiers.json")
    new_tiers = json.loads(TIERS_PATH.read_text(encoding="utf-8"))
    held = hold_shifts(old_tiers, new_tiers, idents)
    TIERS_PATH.write_text(
        json.dumps(held, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    for key, effort in idents:
        print(f"hold_tier_shifts: kept previous tier for {key} ({effort}) pending review")


if __name__ == "__main__":
    main()
