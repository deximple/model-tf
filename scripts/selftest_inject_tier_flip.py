#!/usr/bin/env python3
"""Self-test only: inject one deterministic single-source tier flip.

Used by .github/workflows/selftest.yml (via refresh.yml's inject_tier_flip
input) to force the review-PR path of the refresh workflow on a sandbox
branch. Never runs in production refreshes.

The first entry (entries are sorted by key, effort) gets its tier moved one
band (frontier->mid, mid->low, low->mid) and its evidence trimmed to the
first item, so the gate sees a tier change backed by a single source. The
choice is deterministic so a second self-test pass injects the same flip.

Usage: python3 scripts/selftest_inject_tier_flip.py
"""

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TIERS_PATH = ROOT / "model-tiers.json"
NEXT_TIER = {"frontier": "mid", "mid": "low", "low": "mid"}


def inject(tiers: dict[str, Any]) -> tuple[str, str, str, str]:
    """Mutate ``tiers`` in place; return (key, effort, old_tier, new_tier)."""
    if not tiers["entries"]:
        raise ValueError("model-tiers.json has no entries to flip")
    entry = tiers["entries"][0]
    old = entry["tier"]
    entry["tier"] = NEXT_TIER[old]
    entry["evidence"] = entry["evidence"][:1]
    return entry["key"], entry["effort"], old, entry["tier"]


def main() -> None:
    tiers = json.loads(TIERS_PATH.read_text(encoding="utf-8"))
    key, effort, old, new = inject(tiers)
    TIERS_PATH.write_text(
        json.dumps(tiers, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(f"selftest: injected single-source tier flip {key} ({effort}): {old} -> {new}")


if __name__ == "__main__":
    sys.exit(main())
