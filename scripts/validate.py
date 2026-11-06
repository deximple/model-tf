#!/usr/bin/env python3
"""Validate model-facts.json and model-tiers.json against their schemas and cross-file rules.

Checks:
  1. Both files parse as JSON and match schema/*.schema.json.
  2. All bundle and checkedAt dates are valid ISO-8601 calendar dates (YYYY-MM-DD).
  3. Facts bundle holds no Korean Hangul syllables and no ranking/recommendation
     words (recommend, rank, best, strength), and value.text contains no spaces.
  4. Every facts value id is globally unique and matches "vendorId/model/code"
     (optionally suffixed with "/<qualifier>").
  5. No duplicate (vendorId, model) pairs in facts; models are sorted by (vendorId, model);
     efforts are ordered low-to-high.
  6. No duplicate (key, effort) in tiers; entries are sorted by (key, effort).
  7. Every tiers entry key exists in facts, its effort matches the model's efforts
     ('default' when efforts is empty, or a member of efforts when non-empty), and
     its rank keys equal the declared tiers.axis set.
  8. tiers.factsDate equals facts.date.
  9. Every tiers entry has at least one benchmark or vendor evidence item
     (community evidence alone cannot set a tier).

Requires: jsonschema (pip install jsonschema). Exits non-zero on any failure.
"""

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

try:
    import jsonschema
except ImportError:
    sys.exit("jsonschema is required: pip install jsonschema")

ROOT = Path(__file__).resolve().parent.parent
FACTS_PATH = ROOT / "model-facts.json"
TIERS_PATH = ROOT / "model-tiers.json"
SCHEMA_DIR = ROOT / "schema"

EFFORT_ORDER = ["none", "minimal", "low", "medium", "high", "xhigh", "max"]
FORBIDDEN_FACTS_WORDS = ("recommend", "rank", "best", "strength")


def load_json(path: Path) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_text(encoding="utf-8")
        return json.loads(raw), raw
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path.name}: invalid JSON: {exc}")


def validate_schema(data: dict[str, Any], schema_path: Path) -> None:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.validate(data, schema)


def _valid_iso_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def check_facts(
    facts: dict[str, Any], raw_text: str | None = None
) -> tuple[list[str], dict[str, list[str]]]:
    errors: list[str] = []
    if not _valid_iso_date(facts["date"]):
        errors.append(f"facts: invalid date {facts['date']!r}")

    if raw_text is not None:
        if any(0xAC00 <= ord(ch) <= 0xD7A3 for ch in raw_text):
            errors.append("facts: contains Korean Hangul characters")
        lowered = raw_text.lower()
        for word in FORBIDDEN_FACTS_WORDS:
            if word in lowered:
                errors.append(f"facts: contains forbidden evaluation word {word!r}")

    fact_efforts: dict[str, list[str]] = {}
    seen_models: list[tuple[str, str]] = []
    seen_value_ids: set[str] = set()

    for model in facts["models"]:
        vid, mid = model["vendorId"], model["model"]
        pair = (vid, mid)
        key = f"{vid}/{mid}"
        if key in fact_efforts:
            errors.append(f"facts: duplicate model {key}")
        fact_efforts[key] = list(model["efforts"])
        seen_models.append(pair)

        effs = model["efforts"]
        unknown_effs = [e for e in effs if e not in EFFORT_ORDER]
        if unknown_effs:
            errors.append(f"facts: {key} has unknown efforts {unknown_effs}")
        else:
            indices = [EFFORT_ORDER.index(e) for e in effs]
            if indices != sorted(indices):
                errors.append(
                    f"facts: {key} efforts {effs} are not ordered low-to-high"
                )

        for value in model["values"]:
            vid_str = value["id"]
            if vid_str in seen_value_ids:
                errors.append(f"facts: duplicate value id {vid_str!r}")
            seen_value_ids.add(vid_str)

            code = value["code"]
            expected = f"{vid}/{mid}/{code}"
            if vid_str != expected and not vid_str.startswith(expected + "/"):
                errors.append(
                    f"facts: value id {vid_str!r} does not match {expected!r}"
                )
            if value["number"] is None and value["text"] is None:
                errors.append(f"facts: {vid_str!r} has both number and text null")
            if value["text"] is not None and " " in value["text"]:
                errors.append(f"facts: {vid_str!r} text contains whitespace")
            if not value["source"].startswith("https://"):
                errors.append(f"facts: {vid_str!r} source must start with https://")
            if not _valid_iso_date(value["checkedAt"]):
                errors.append(
                    f"facts: {vid_str!r} invalid checkedAt {value['checkedAt']!r}"
                )

    if seen_models != sorted(seen_models):
        errors.append("facts: models array is not sorted by (vendorId, model)")

    return errors, fact_efforts


def check_tiers(
    tiers: dict[str, Any],
    fact_efforts: dict[str, list[str]],
    facts_date: str,
) -> list[str]:
    errors: list[str] = []
    if not _valid_iso_date(tiers["date"]):
        errors.append(f"tiers: invalid date {tiers['date']!r}")
    if not _valid_iso_date(tiers["factsDate"]):
        errors.append(f"tiers: invalid factsDate {tiers['factsDate']!r}")
    if tiers["factsDate"] != facts_date:
        errors.append(
            f"tiers.factsDate {tiers['factsDate']!r} != facts.date {facts_date!r}"
        )

    axis = set(tiers["axis"])
    seen_pairs: list[tuple[str, str]] = []
    seen_set: set[tuple[str, str]] = set()

    for entry in tiers["entries"]:
        key, effort = entry["key"], entry["effort"]
        pair = (key, effort)
        if pair in seen_set:
            errors.append(f"tiers: duplicate entry {key} effort={effort}")
        seen_set.add(pair)
        seen_pairs.append(pair)

        if key not in fact_efforts:
            errors.append(f"tiers: key {key!r} not present in model-facts.json")
        else:
            allowed = fact_efforts[key]
            if not allowed and effort != "default":
                errors.append(
                    f"tiers: {key} has empty efforts in facts so effort must be 'default', got {effort!r}"
                )
            elif allowed and effort not in allowed:
                errors.append(
                    f"tiers: {key} effort {effort!r} not in facts efforts {allowed}"
                )

        rank_keys = set(entry["rank"].keys())
        if rank_keys != axis:
            errors.append(
                f"tiers: {key} effort={effort} rank keys {sorted(rank_keys)} != axis {sorted(axis)}"
            )

        kinds = {e["kind"] for e in entry["evidence"]}
        if kinds <= {"community"}:
            errors.append(
                f"tiers: {key} effort={effort} has only community evidence; "
                "needs benchmark or vendor"
            )
        for ev in entry["evidence"]:
            if not ev["source"].startswith("https://"):
                errors.append(
                    f"tiers: {key} effort={effort} evidence source must start with https://"
                )
            if not _valid_iso_date(ev["checkedAt"]):
                errors.append(
                    f"tiers: {key} effort={effort} invalid evidence checkedAt {ev['checkedAt']!r}"
                )

    if seen_pairs != sorted(seen_pairs):
        errors.append("tiers: entries array is not sorted by (key, effort)")

    return errors


def main() -> None:
    facts, facts_raw = load_json(FACTS_PATH)
    tiers, _ = load_json(TIERS_PATH)

    validate_schema(facts, SCHEMA_DIR / "model-facts.schema.json")
    validate_schema(tiers, SCHEMA_DIR / "model-tiers.schema.json")

    errors, fact_efforts = check_facts(facts, facts_raw)
    errors += check_tiers(tiers, fact_efforts, facts["date"])

    if errors:
        for error in errors:
            print(f"FAIL {error}")
        sys.exit(1)
    print(
        f"OK: {len(facts['models'])} facts models, "
        f"{len(tiers['entries'])} tier entries"
    )


if __name__ == "__main__":
    main()
