#!/usr/bin/env python3
"""Report facts values and tier entries whose checkedAt is older than a threshold.

Report-only: always exits 0. The refresh workflow includes the output in PR
bodies; validate.yml may run it for visibility. Does not modify any files.

Usage: python3 sources/check_staleness.py [--days N]   (default 30)
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


def parse_args(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Age threshold in days before a checkedAt date is reported as stale (default: 30).",
    )
    return parser.parse_args(argv).days


def age_days(checked_at: str, today: date) -> int | None:
    try:
        return (today - date.fromisoformat(checked_at)).days
    except ValueError:
        return None


def stale_items(data: dict[str, Any], today: date, days: int) -> list[tuple[int, str]]:
    stale: list[tuple[int, str]] = []
    for model in data["models"]:
        for value in model["values"]:
            age = age_days(value["checkedAt"], today)
            if age is not None and age > days:
                stale.append((age, f"facts {value['id']} ({value['code']})"))
    return stale


def stale_tier_items(tiers: dict[str, Any], today: date, days: int) -> list[tuple[int, str]]:
    stale: list[tuple[int, str]] = []
    for entry in tiers["entries"]:
        for evidence in entry["evidence"]:
            age = age_days(evidence["checkedAt"], today)
            if age is not None and age > days:
                stale.append(
                    (age, f"tiers {entry['key']} effort={entry['effort']} ({evidence['kind']})")
                )
    return stale


def main(argv: list[str] | None = None) -> None:
    days = parse_args(argv if argv is not None else sys.argv[1:])
    today = date.today()
    stale: list[tuple[int, str]] = []

    facts: dict[str, Any] = json.loads(
        (ROOT / "model-facts.json").read_text(encoding="utf-8")
    )
    stale += stale_items(facts, today, days)

    tiers: dict[str, Any] = json.loads(
        (ROOT / "model-tiers.json").read_text(encoding="utf-8")
    )
    stale += stale_tier_items(tiers, today, days)

    stale.sort(reverse=True)
    print(f"# Staleness report (checkedAt older than {days} days, today={today})")
    if not stale:
        print("No stale entries.")
        return
    for age, label in stale:
        print(f"- {age}d  {label}")


if __name__ == "__main__":
    main()
