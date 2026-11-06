#!/usr/bin/env python3
"""Validate model-facts.json and model-tiers.json against their schemas and cross-file rules.

Checks:
  1. Both files parse as JSON and match schema/*.schema.json.
  2. Every facts value id starts with its model's "vendorId/model/" prefix.
  3. No duplicate (vendorId, model) pairs in facts; no duplicate (key, effort) in tiers.
  4. Every tiers entry key exists in facts; every rank axis is declared in tiers.axis.
  5. tiers.factsDate equals facts.date.
  6. Every tiers entry has at least one benchmark or vendor evidence item
     (community evidence alone cannot set a tier).

Requires: jsonschema (pip install jsonschema). Exits non-zero on any failure.
"""

import json
import sys
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


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path.name}: invalid JSON: {exc}")


def validate_schema(data: dict[str, Any], schema_path: Path) -> None:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.validate(data, schema)


def check_facts(facts: dict[str, Any]) -> tuple[list[str], set[str]]:
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    for model in facts["models"]:
        vid, mid = model["vendorId"], model["model"]
        if (vid, mid) in seen:
            errors.append(f"facts: duplicate model {vid}/{mid}")
        seen.add((vid, mid))
        prefix = f"{vid}/{mid}/"
        for value in model["values"]:
            if not value["id"].startswith(prefix):
                errors.append(
                    f"facts: value id {value['id']!r} does not start with {prefix!r}"
                )
    return errors, {f"{v}/{m}" for v, m in seen}


def check_tiers(
    tiers: dict[str, Any], fact_keys: set[str], facts_date: str
) -> list[str]:
    errors: list[str] = []
    if tiers["factsDate"] != facts_date:
        errors.append(
            f"tiers.factsDate {tiers['factsDate']!r} != facts.date {facts_date!r}"
        )
    axis = set(tiers["axis"])
    seen: set[tuple[str, str]] = set()
    for entry in tiers["entries"]:
        key, effort = entry["key"], entry["effort"]
        if (key, effort) in seen:
            errors.append(f"tiers: duplicate entry {key} effort={effort}")
        seen.add((key, effort))
        if key not in fact_keys:
            errors.append(f"tiers: key {key!r} not present in model-facts.json")
        for axis_name in entry["rank"]:
            if axis_name not in axis:
                errors.append(
                    f"tiers: rank axis {axis_name!r} not declared in axis {sorted(axis)}"
                )
        kinds = {e["kind"] for e in entry["evidence"]}
        if kinds == {"community"}:
            errors.append(
                f"tiers: {key} effort={effort} has only community evidence; "
                "needs benchmark or vendor"
            )
    return errors


def main() -> None:
    facts = load_json(FACTS_PATH)
    tiers = load_json(TIERS_PATH)

    validate_schema(facts, SCHEMA_DIR / "model-facts.schema.json")
    validate_schema(tiers, SCHEMA_DIR / "model-tiers.schema.json")

    errors, fact_keys = check_facts(facts)
    errors += check_tiers(tiers, fact_keys, facts["date"])

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
