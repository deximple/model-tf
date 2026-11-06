#!/usr/bin/env python3
"""Refresh AA-sourced entries in model-tiers.json from the Artificial Analysis leaderboard.

Fetches https://artificialanalysis.ai/leaderboards/models, parses the model table,
and rewrites every tiers entry whose evidence is exclusively the AA Intelligence
Index benchmark. Entries with other evidence (vendor claims, community notes,
human curation) are preserved untouched. New model x effort rows that map to a
model in model-facts.json are appended; AA-managed entries whose row disappears
are removed.

If no entry's tier, rank, or score note changed and factsDate already matches
model-facts.json, existing checkedAt and bundle date values are preserved so
scheduled runs produce zero diff when leaderboard data has not changed. Pass
--touch to force-update checkedAt and bundle date to today.

Tier bands and rank are provisional composites of the Intelligence Index; both
task axes currently share the same rank. Human review happens in the PR this
script feeds (see .github/workflows/refresh.yml).

Stdlib only. Run from anywhere: python3 sources/fetch_artificial_analysis.py [--touch]
"""

import html
import json
import re
import sys
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
FACTS_PATH = ROOT / "model-facts.json"
TIERS_PATH = ROOT / "model-tiers.json"
AA_URL = "https://artificialanalysis.ai/leaderboards/models"
AA_SOURCE = AA_URL

# Provisional bands on the Intelligence Index score. Human curation may retier.
FRONTIER_MIN = 50
MID_MIN = 25

# AA leaderboard display name -> (vendorId, model) in model-facts.json.
NAME_MAP: dict[str, tuple[str, str]] = {
    "Claude Opus 5.5": ("claude", "claude-opus-5-5"),
    "Claude Sonnet 5.5": ("claude", "claude-sonnet-5-5"),
    "Claude Haiku 5.5": ("claude", "claude-haiku-5-5"),
    "Claude Fable 5.1": ("claude", "claude-fable-5-1"),
    "GPT-6 Astra": ("openai", "gpt-6-astra"),
    "GPT-6.1 Sol": ("openai", "gpt-6.1-sol"),
    "GPT-6.1 Terra": ("openai", "gpt-6.1-terra"),
    "GPT-6 Luna": ("openai", "gpt-6-luna"),
    "GPT-5.6 Luna": ("openai", "gpt-5.6-luna"),
    "Gemini 4 Argon": ("google", "gemini-4-argon"),
    "Grok 4.7": ("xai", "grok-4.7"),
    "GLM-5.3-Flash": ("zhipuai", "glm-5.3-flash"),
    "GLM-5.3": ("zhipuai", "glm-5.3"),
    "GLM-5.2": ("zhipuai", "glm-5.2"),
    "Kimi K3.1 Pro": ("moonshotai", "kimi-k3.1-pro"),
    "Kimi K3": ("moonshotai", "kimi-k3"),
    "Kimi K2.7 Code": ("moonshotai", "kimi-k2.7-code"),
    "LongCat 2.0": ("meituan", "longcat-2.0"),
    "Step 5 Preview": ("stepfun", "step-5-preview"),
    "MiMo-V2.6-Pro": ("xiaomi", "mimo-v2.6-pro"),
    "MiMo-V2.6-Flash": ("xiaomi", "mimo-v2.6-flash"),
    "MiMo-V2.5-Pro": ("xiaomi", "mimo-v2.5-pro"),
    "MiMo-V2.5": ("xiaomi", "mimo-v2.5"),
    "MiniMax-M3.1-Flash": ("minimax", "minimax-m3.1-flash"),
    "MiniMax-M3": ("minimax", "minimax-m3"),
    "Qwen3.8 Max": ("alibaba", "qwen3.8-max"),
    "Qwen3.8 Flash": ("alibaba", "qwen3.8-flash"),
    "Qwen3.7 Plus": ("alibaba", "qwen3.7-plus"),
    "DeepSeek V4.1 Flash": ("deepseek", "deepseek-v4.1-flash"),
    "DeepSeek V4 Pro": ("deepseek", "deepseek-v4-pro"),
    "Tencent Hy4": ("tencent", "hy4-preview"),
    "Tencent Hy3": ("tencent", "hy3"),
    "Hy4": ("tencent", "hy4-preview"),
    "Hy3": ("tencent", "hy3"),
}

EFFORT_WORDS = {"low", "medium", "high", "xhigh", "max", "none", "non-reasoning", "minimal"}
CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def fetch_leaderboard() -> str:
    req = urllib.request.Request(AA_URL, headers={"User-Agent": "model-tf-refresh/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _cell_text(fragment: str) -> str:
    return WS_RE.sub(" ", html.unescape(TAG_RE.sub(" ", fragment))).strip()


def parse_rows(page: str) -> list[tuple[str, float]]:
    """Return [(model_cell, score)] for every scored leaderboard row."""
    rows: list[tuple[str, float]] = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
        cells = CELL_RE.findall(tr)
        if len(cells) != 8:
            continue
        texts = [_cell_text(c) for c in cells]
        if not texts[0] or texts[0] == "Model":
            continue
        try:
            score = float(texts[3])
        except ValueError:
            continue
        rows.append((texts[0], score))
    return rows


def parse_name(name: str) -> tuple[str, str]:
    """Split 'Model (effort)' into (base, effort). Date tags like (0902) are dropped."""
    effort, base = "default", name
    while True:
        m = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", base)
        if not m:
            break
        inner = m.group(2).strip()
        if inner in EFFORT_WORDS:
            effort = "none" if inner == "non-reasoning" else inner
            base = m.group(1).strip()
        elif re.fullmatch(r"\d{4}", inner):
            base = m.group(1).strip()
        else:
            break
    return re.sub(r"\s+\d{4}$", "", base), effort


def competition_ranks(
    scored_items: list[tuple[tuple[str, str], float]]
) -> dict[tuple[str, str], int]:
    """Standard competition rank 1,2,2,4... over ((key, effort), score) sorted descending."""
    ordered = sorted(scored_items, key=lambda item: (-item[1], item[0][0], item[0][1]))
    ranks: dict[tuple[str, str], int] = {}
    prev_score: float | None = None
    rank = 0
    for i, (ident, score) in enumerate(ordered):
        if score != prev_score:
            rank = i + 1
            prev_score = score
        ranks[ident] = rank
    return ranks


def tier_of(score: float) -> str:
    if score >= FRONTIER_MIN:
        return "frontier"
    if score >= MID_MIN:
        return "mid"
    return "low"


def is_aa_managed(entry: dict[str, Any]) -> bool:
    ev = entry.get("evidence") or []
    return bool(ev) and all(
        e.get("kind") == "benchmark" and e.get("source") == AA_SOURCE for e in ev
    )


def _valid_effort(effort: str, model_efforts: list[str]) -> bool:
    if not model_efforts:
        return effort == "default"
    return effort in model_efforts


def build_aa_entries(
    rows: list[tuple[str, float]],
    fact_efforts: dict[str, list[str]],
    today: str,
    existing_aa: dict[tuple[str, str], dict[str, Any]] | None = None,
    touch: bool = False,
) -> dict[tuple[str, str], dict[str, Any]]:
    matched: list[tuple[tuple[str, str], float]] = []
    seen: set[tuple[str, str]] = set()
    for name, score in rows:
        base, effort = parse_name(name)
        hit = NAME_MAP.get(base)
        if not hit:
            continue
        vid, mid = hit
        key = f"{vid}/{mid}"
        if key not in fact_efforts:
            continue
        if not _valid_effort(effort, fact_efforts[key]):
            continue
        ident = (key, effort)
        if ident in seen:
            continue
        seen.add(ident)
        matched.append((ident, score))

    ranks = competition_ranks(matched)
    existing = existing_aa or {}
    entries: dict[tuple[str, str], dict[str, Any]] = {}
    for ident, score in matched:
        key, effort = ident
        tier = tier_of(score)
        rank = {"simpleCoding": ranks[ident], "reasoning": ranks[ident]}
        note = (
            f"Artificial Analysis Intelligence Index {score:g} "
            "(composite; axis ranks provisional)"
        )
        checked_at = today
        prev = existing.get(ident)
        if (
            not touch
            and prev is not None
            and prev.get("tier") == tier
            and prev.get("rank") == rank
            and prev.get("evidence")
            and prev["evidence"][0].get("note") == note
        ):
            checked_at = prev["evidence"][0].get("checkedAt", today)
        entries[ident] = {
            "key": key,
            "effort": effort,
            "tier": tier,
            "rank": rank,
            "evidence": [
                {
                    "kind": "benchmark",
                    "source": AA_SOURCE,
                    "checkedAt": checked_at,
                    "note": note,
                }
            ],
        }
    return entries


def main(argv: list[str] | None = None) -> None:
    args = argv if argv is not None else sys.argv[1:]
    touch = "--touch" in args

    facts: dict[str, Any] = json.loads(FACTS_PATH.read_text(encoding="utf-8"))
    tiers: dict[str, Any] = json.loads(TIERS_PATH.read_text(encoding="utf-8"))
    fact_efforts = {
        f"{m['vendorId']}/{m['model']}": list(m["efforts"]) for m in facts["models"]
    }
    today = date.today().isoformat()

    page = fetch_leaderboard()
    rows = parse_rows(page)
    if not rows:
        sys.exit("no leaderboard rows parsed; aborting without changes")

    existing_aa = {
        (e["key"], e["effort"]): e for e in tiers["entries"] if is_aa_managed(e)
    }
    aa_entries = build_aa_entries(
        rows, fact_efforts, today, existing_aa=existing_aa, touch=touch
    )

    preserved = [e for e in tiers["entries"] if not is_aa_managed(e)]
    regenerated = list(aa_entries.values())
    merged = preserved + regenerated
    merged.sort(key=lambda e: (e["key"], e["effort"]))

    changed = (
        touch
        or merged != tiers["entries"]
        or tiers.get("factsDate") != facts["date"]
    )
    if changed:
        tiers["date"] = today
    tiers["factsDate"] = facts["date"]
    tiers["entries"] = merged

    TIERS_PATH.write_text(
        json.dumps(tiers, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(
        f"model-tiers.json: {len(preserved)} preserved + "
        f"{len(regenerated)} AA-managed = {len(merged)} entries"
    )


if __name__ == "__main__":
    main()
