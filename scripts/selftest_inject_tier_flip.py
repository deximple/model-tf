#!/usr/bin/env python3
"""Self-test only: inject one deterministic single-source tier flip.

Used by .github/workflows/selftest.yml (via refresh.yml's inject_tier_flip
input) to force the review-PR path of the refresh workflow on a sandbox
branch. Never runs in production refreshes.

The first entry (entries are sorted by key, effort) that also exists in the
committed HEAD version gets a tier one band away from its HEAD tier
(frontier->mid, mid->low, low->mid) and its evidence trimmed to the first
item, so the gate always sees a tier change backed by a single source, even
when today's real data already moved that entry. The choice is
deterministic, so a second self-test pass injects the same flip.

Usage: python3 scripts/selftest_inject_tier_flip.py
"""

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "sources"))

from check_staleness import load_head_json  # noqa: E402

TIERS_PATH = ROOT / "model-tiers.json"
NEXT_TIER = {"frontier": "mid", "mid": "low", "low": "mid"}


def inject(
    tiers: dict[str, Any], head_tiers: dict[str, Any] | None = None
) -> tuple[str, str, str, str]:
    """Mutate ``tiers`` in place; return (key, effort, head_tier, new_tier)."""
    head = {(e["key"], e["effort"]): e for e in (head_tiers or {}).get("entries", [])}
    candidates = [e for e in tiers["entries"] if not head or (e["key"], e["effort"]) in head]
    if not candidates:
        raise ValueError("model-tiers.json has no entry shared with HEAD to flip")
    entry = candidates[0]
    old = head[(entry["key"], entry["effort"])]["tier"] if head else entry["tier"]
    entry["tier"] = NEXT_TIER[old]
    entry["evidence"] = entry["evidence"][:1]
    return entry["key"], entry["effort"], old, entry["tier"]


def main() -> None:
    tiers = json.loads(TIERS_PATH.read_text(encoding="utf-8"))
    key, effort, old, new = inject(tiers, load_head_json("model-tiers.json"))
    TIERS_PATH.write_text(
        json.dumps(tiers, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(f"selftest: injected single-source tier flip {key} ({effort}): {old} -> {new}")


if __name__ == "__main__":
    sys.exit(main())
