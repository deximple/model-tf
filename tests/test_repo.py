#!/usr/bin/env python3
"""Unit tests for model-tf schemas, validator, AA collector, and staleness checker."""

import copy
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "sources"))

import check_staleness  # noqa: E402
import fetch_artificial_analysis as fetch_aa  # noqa: E402
import jsonschema  # noqa: E402
import validate  # noqa: E402


class ValidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.facts, self.facts_raw = validate.load_json(validate.FACTS_PATH)
        self.tiers, _ = validate.load_json(validate.TIERS_PATH)

    def test_shipped_files_pass_all_checks(self) -> None:
        validate.validate_schema(
            self.facts, validate.SCHEMA_DIR / "model-facts.schema.json"
        )
        validate.validate_schema(
            self.tiers, validate.SCHEMA_DIR / "model-tiers.schema.json"
        )
        errors, fact_efforts = validate.check_facts(self.facts, self.facts_raw)
        errors += validate.check_tiers(self.tiers, fact_efforts, self.facts["date"])
        self.assertEqual(errors, [])

    def test_schema_rejects_both_null_number_and_text(self) -> None:
        bad = copy.deepcopy(self.facts)
        bad["models"][0]["values"][0]["number"] = None
        bad["models"][0]["values"][0]["text"] = None
        with self.assertRaises(jsonschema.ValidationError):
            validate.validate_schema(
                bad, validate.SCHEMA_DIR / "model-facts.schema.json"
            )

    def test_check_facts_catches_korean_and_forbidden_words(self) -> None:
        errors, _ = validate.check_facts(self.facts, '{"note": "한글 recommend"}')
        self.assertTrue(any("Korean" in e for e in errors))
        self.assertTrue(any("recommend" in e for e in errors))

    def test_check_facts_catches_unsorted_efforts_and_bad_value_id(self) -> None:
        bad = copy.deepcopy(self.facts)
        for m in bad["models"]:
            if m["model"] == "claude-opus-5-5":
                m["efforts"] = ["high", "low"]
                m["values"][0]["id"] = "claude/claude-opus-5-5/wrongCode"
        errors, _ = validate.check_facts(bad)
        self.assertTrue(any("not ordered low-to-high" in e for e in errors))
        self.assertTrue(any("does not match" in e for e in errors))

    def test_check_tiers_catches_effort_mismatch_and_community_only(self) -> None:
        _, fact_efforts = validate.check_facts(self.facts)
        bad = copy.deepcopy(self.tiers)
        bad["entries"][0]["effort"] = "high"  # alibaba/qwen3.8-max has efforts=[]
        bad["entries"][1]["evidence"] = [
            {
                "kind": "community",
                "source": "https://x.com/example/status/1",
                "checkedAt": "2026-11-07",
            }
        ]
        del bad["entries"][2]["rank"]["simpleCoding"]
        errors = validate.check_tiers(bad, fact_efforts, self.facts["date"])
        self.assertTrue(any("effort must be 'default'" in e for e in errors))
        self.assertTrue(any("only community evidence" in e for e in errors))
        self.assertTrue(any("rank keys" in e for e in errors))


class FetchArtificialAnalysisTests(unittest.TestCase):
    def test_parse_name_handles_efforts_and_date_tags(self) -> None:
        self.assertEqual(
            fetch_aa.parse_name("Claude Opus 5.5 (max)"),
            ("Claude Opus 5.5", "max"),
        )
        self.assertEqual(
            fetch_aa.parse_name("DeepSeek V4.1 Flash 1017 (non-reasoning)"),
            ("DeepSeek V4.1 Flash", "none"),
        )
        self.assertEqual(
            fetch_aa.parse_name("Qwen3.8 Max (0902)"),
            ("Qwen3.8 Max", "default"),
        )
        self.assertEqual(
            fetch_aa.parse_name("Kimi K3.1 Pro"),
            ("Kimi K3.1 Pro", "default"),
        )

    def test_competition_ranks_start_at_one_and_handle_ties(self) -> None:
        items = [
            (("claude/claude-opus-5-5", "max"), 59.0),
            (("claude/claude-opus-5-5", "xhigh"), 57.0),
            (("openai/gpt-6-astra", "max"), 57.0),
            (("openai/gpt-6.1-sol", "max"), 55.0),
        ]
        ranks = fetch_aa.competition_ranks(items)
        self.assertEqual(ranks[("claude/claude-opus-5-5", "max")], 1)
        self.assertEqual(ranks[("claude/claude-opus-5-5", "xhigh")], 2)
        self.assertEqual(ranks[("openai/gpt-6-astra", "max")], 2)
        self.assertEqual(ranks[("openai/gpt-6.1-sol", "max")], 4)

    def test_build_aa_entries_preserves_checked_at_when_unchanged(self) -> None:
        rows = [("Claude Opus 5.5 (max)", 59.0)]
        fact_efforts = {"claude/claude-opus-5-5": ["low", "medium", "high", "xhigh", "max"]}
        first = fetch_aa.build_aa_entries(rows, fact_efforts, "2026-11-01")
        second = fetch_aa.build_aa_entries(
            rows, fact_efforts, "2026-11-08", existing_aa=first, touch=False
        )
        self.assertEqual(
            second[("claude/claude-opus-5-5", "max")]["evidence"][0]["checkedAt"],
            "2026-11-01",
        )
        touched = fetch_aa.build_aa_entries(
            rows, fact_efforts, "2026-11-08", existing_aa=first, touch=True
        )
        self.assertEqual(
            touched[("claude/claude-opus-5-5", "max")]["evidence"][0]["checkedAt"],
            "2026-11-08",
        )


class StalenessTests(unittest.TestCase):
    def test_parse_args_and_stale_detection(self) -> None:
        self.assertEqual(check_staleness.parse_args([]), 30)
        self.assertEqual(check_staleness.parse_args(["--days", "14"]), 14)
        self.assertEqual(check_staleness.parse_args(["--days=7"]), 7)
        today = date(2026, 12, 15)
        facts = {
            "models": [
                {
                    "values": [
                        {
                            "id": "claude/claude-opus-5-5/inputPricePerMTok",
                            "code": "inputPricePerMTok",
                            "checkedAt": "2026-11-02",
                        }
                    ]
                }
            ]
        }
        stale = check_staleness.stale_items(facts, today, 30)
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0][0], 43)


if __name__ == "__main__":
    unittest.main()
