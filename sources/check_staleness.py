#!/usr/bin/env python3
"""Report refresh diff summary, auto-merge gate status, and stale checkedAt items.

Report-only: always exits 0. The refresh workflow includes the output in commit
summaries, GitHub Actions step summaries, and PR bodies; validate.yml runs it
for visibility. Does not modify any tracked files.

Usage: python3 sources/check_staleness.py [--days N] [--diff-head] [--gate-out PATH]
"""

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Age threshold in days before a checkedAt date is reported as stale (default: 30).",
    )
    parser.add_argument(
        "--diff-head",
        action="store_true",
        help="Include a human-review summary comparing working tree files against HEAD.",
    )
    parser.add_argument(
        "--gate-out",
        type=str,
        default=None,
        help="Write JSON gate decision (whether single-source tier shifts require PR review).",
    )
    return parser.parse_args(argv)


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


def _load_head_json(rel_path: str) -> dict[str, Any] | None:
    try:
        out = subprocess.check_output(
            ["git", "show", f"HEAD:{rel_path}"],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return json.loads(out)
    except Exception:
        return None


def evaluate_tier_gate(
    old_tiers: dict[str, Any] | None, new_tiers: dict[str, Any]
) -> dict[str, Any]:
    """Check whether any existing entry changed tier with fewer than 2 benchmark sources."""
    if old_tiers is None:
        return {"requires_pr": False, "single_source_tier_shifts": [], "confirmed_tier_shifts": []}
    old_t = {(e["key"], e["effort"]): e for e in old_tiers["entries"]}
    new_t = {(e["key"], e["effort"]): e for e in new_tiers["entries"]}
    single_source: list[str] = []
    confirmed: list[str] = []
    for ident in sorted(set(old_t) & set(new_t)):
        oe, ne = old_t[ident], new_t[ident]
        if oe["tier"] != ne["tier"]:
            desc = f"{ident[0]} ({ident[1]}): {oe['tier']} -> {ne['tier']}"
            if len(ne.get("evidence") or []) >= 2:
                confirmed.append(desc)
            else:
                single_source.append(desc)
    return {
        "requires_pr": bool(single_source),
        "single_source_tier_shifts": single_source,
        "confirmed_tier_shifts": confirmed,
    }


def summarize_diff(
    old_facts: dict[str, Any] | None,
    new_facts: dict[str, Any],
    old_tiers: dict[str, Any] | None,
    new_tiers: dict[str, Any],
) -> list[str]:
    lines: list[str] = ["# Refresh Summary"]
    if old_facts is not None:
        old_m = {f"{m['vendorId']}/{m['model']}": m for m in old_facts["models"]}
        new_m = {f"{m['vendorId']}/{m['model']}": m for m in new_facts["models"]}
        added_m = sorted(set(new_m) - set(old_m))
        removed_m = sorted(set(old_m) - set(new_m))
        val_changes: list[str] = []
        for k in sorted(set(old_m) & set(new_m)):
            ov = {v["id"]: (v["number"], v["text"]) for v in old_m[k]["values"]}
            nv = {v["id"]: (v["number"], v["text"]) for v in new_m[k]["values"]}
            for vid in sorted(set(ov) | set(nv)):
                if ov.get(vid) != nv.get(vid):
                    val_changes.append(f"  - `{vid}`: `{ov.get(vid)}` -> `{nv.get(vid)}`")
        lines.append("\n## Facts (`model-facts.json`)")
        if not added_m and not removed_m and not val_changes:
            lines.append("- No model or value changes.")
        else:
            for k in added_m:
                lines.append(f"- Added model: `{k}` ({new_m[k]['name']})")
            for k in removed_m:
                lines.append(f"- Removed model: `{k}`")
            if val_changes:
                lines.append(f"- Value changes ({len(val_changes)}):")
                lines.extend(val_changes[:40])

    if old_tiers is not None:
        old_t = {(e["key"], e["effort"]): e for e in old_tiers["entries"]}
        new_t = {(e["key"], e["effort"]): e for e in new_tiers["entries"]}
        added_t = sorted(set(new_t) - set(old_t))
        removed_t = sorted(set(old_t) - set(new_t))
        tier_shifts: list[str] = []
        rank_shifts: list[str] = []
        for ident in sorted(set(old_t) & set(new_t)):
            oe, ne = old_t[ident], new_t[ident]
            label = f"`{ident[0]}` ({ident[1]})"
            ev_cnt = len(ne.get("evidence") or [])
            if oe["tier"] != ne["tier"]:
                tier_shifts.append(
                    f"  - {label}: **{oe['tier']} -> {ne['tier']}** ({ev_cnt} benchmark sources)"
                )
            elif oe["rank"] != ne["rank"]:
                rank_shifts.append(
                    f"  - {label}: rank `{oe['rank']}` -> `{ne['rank']}`"
                )
        lines.append("\n## Tiers (`model-tiers.json`)")
        if not added_t and not removed_t and not tier_shifts and not rank_shifts:
            lines.append("- No tier or rank changes.")
        else:
            for k, ef in added_t:
                e = new_t[(k, ef)]
                lines.append(f"- Added entry: `{k}` ({ef}) — tier `{e['tier']}`, rank `{e['rank']}`")
            for k, ef in removed_t:
                lines.append(f"- Removed entry: `{k}` ({ef})")
            if tier_shifts:
                lines.append(f"- **Tier changes ({len(tier_shifts)}):**")
                lines.extend(tier_shifts)
            if rank_shifts:
                lines.append(f"- Rank changes ({len(rank_shifts)}):")
                lines.extend(rank_shifts[:30])
    lines.append("")
    return lines


def main(argv: list[str] | None = None) -> None:
    ns = parse_args(argv if argv is not None else sys.argv[1:])
    days = ns.days
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

    old_facts = _load_head_json("model-facts.json") if (ns.diff_head or ns.gate_out) else None
    old_tiers = _load_head_json("model-tiers.json") if (ns.diff_head or ns.gate_out) else None

    if ns.gate_out:
        gate = evaluate_tier_gate(old_tiers, tiers)
        Path(ns.gate_out).write_text(json.dumps(gate, indent=2) + "\n", encoding="utf-8")

    if ns.diff_head:
        for line in summarize_diff(old_facts, facts, old_tiers, tiers):
            print(line)

    stale.sort(reverse=True)
    print(f"# Staleness report (checkedAt older than {days} days, today={today})")
    if not stale:
        print("No stale entries.")
        return
    for age, label in stale:
        print(f"- {age}d  {label}")


if __name__ == "__main__":
    main()
