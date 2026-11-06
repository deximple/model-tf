#!/usr/bin/env python3
"""Unit tests for model-tf schemas, validator, vendor facts collector, benchmark collector, and staleness checker."""

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
import fetch_vendor_facts as fetch_vf  # noqa: E402
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


class FetchVendorFactsTests(unittest.TestCase):
    def test_infer_maker_handles_known_and_prefix_models(self) -> None:
        self.assertEqual(fetch_vf.infer_maker("claude-opus-5-5"), "claude")
        self.assertEqual(fetch_vf.infer_maker("glm-5.9-future"), "zhipuai")
        self.assertEqual(fetch_vf.infer_maker("qwen4.0-max"), "alibaba")
        self.assertEqual(fetch_vf.infer_maker("unknown-community-free"), "opencode")

    def test_parse_claude_docs_extracts_specs_and_cache_rates(self) -> None:
        md = (
            "All current models support text and image input.\n"
            "| Feature | Claude Opus 5.5 |\n"
            "| :--- | :--- |\n"
            "| Comparative latency | Moderate |\n"
            "| Pricing | $4 / input MTok, $20 / output MTok |\n"
            "| Claude API ID | `claude-opus-5-5` |\n"
            "| Default effort | `medium` |\n"
            "| Context window | 1M tokens |\n"
            "| Max output | 128K tokens |\n"
            "* **Pricing:** prompt cache reads cost 10% (5% on Claude Opus 5.5).\n"
        )
        models = fetch_vf.parse_claude_docs(md, "`low` `medium` `high`", "2026-11-07")
        self.assertEqual(len(models), 1)
        vals = {v["code"]: (v["number"], v["text"]) for v in models[0]["values"]}
        self.assertEqual(vals["contextTokens"], (1000000, None))
        self.assertEqual(vals["inputPricePerMTok"], (4, None))
        self.assertEqual(vals["cachedReadPricePerMTok"], (0.2, None))
        self.assertEqual(vals["defaultEffort"], (None, "medium"))

    def test_parse_xai_and_zai_docs(self) -> None:
        xai_md = (
            "| Model | Context | Input / 1M tokens | Cached input / 1M tokens | Output / 1M tokens |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| grok-4.7 (< 200k prompt tokens) | 500k | $2.00 | $0.50 | $6.00 |\n"
            "| grok-4.7 (≥ 200k prompt tokens) | 500k | $4.00 | $1.00 | $12.00 |\n"
        )
        xai_models = fetch_vf.parse_xai_docs(xai_md, "2026-11-07")
        self.assertEqual(len(xai_models), 1)
        xvals = {v["code"]: v["number"] for v in xai_models[0]["values"]}
        self.assertEqual(xvals["contextTokens"], 500000)
        self.assertEqual(xvals["inputPricePerMTok"], 2)

        zai_md = (
            "| Model | Input | Cached Input | Cached Input Storage | Output |\n"
            "| :- | :- | :- | :- | :- |\n"
            "| GLM-5.3 | \\$1.4 | \\$0.26 | Limited-time Free | \\$4.4 |\n"
        )
        glm53_md = "GLM-5.3 has a 1M-token context window and a maximum output length of 128K tokens."
        zai_models = fetch_vf.parse_zai_docs(zai_md, glm53_md, "2026-11-07")
        self.assertEqual(len(zai_models), 1)
        zvals = {v["code"]: v["number"] for v in zai_models[0]["values"]}
        self.assertEqual(zvals["contextTokens"], 1000000)
        self.assertEqual(zvals["maxOutputTokens"], 128000)
        self.assertEqual(zvals["inputPricePerMTok"], 1.4)


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
            fetch_aa.parse_name("Gemini 4 Argon (mini-swe-agent) high"),
            ("Gemini 4 Argon", "high"),
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

    def test_build_aa_entries_combines_multi_benchmark_sources(self) -> None:
        rows = [
            ("Claude Opus 5.5 (max)", 59.1, 51.0),
            ("Claude Sonnet 5.5 (max)", 54.5, 53.0),
        ]
        facts_models = [
            {
                "vendorId": "claude",
                "model": "claude-opus-5-5",
                "name": "Claude Opus 5.5",
                "efforts": ["low", "medium", "high", "xhigh", "max"],
            },
            {
                "vendorId": "claude",
                "model": "claude-sonnet-5-5",
                "name": "Claude Sonnet 5.5",
                "efforts": ["low", "medium", "high", "xhigh", "max"],
            },
            {
                "vendorId": "moonshotai",
                "model": "kimi-k3",
                "name": "Kimi K3",
                "efforts": [],
            },
        ]
        fact_efforts = {
            f"{m['vendorId']}/{m['model']}": m["efforts"] for m in facts_models
        }
        tbench_rows = [
            {
                "model_label": "Opus 5.5",
                "effort": "max",
                "agent": "Claude Code",
                "accuracy": 64.85,
            }
        ]
        vals_rows = [
            {"model_label": "Claude Opus 5.5", "score": 70.2},
            {"model_label": "Kimi K3", "score": 53.0},
        ]
        scale_rows = [
            {
                "benchmark": "SWE Atlas",
                "category": "agentic",
                "raw_model": "Opus 5.5",
                "score": 77.4,
            }
        ]
        entries = fetch_aa.build_aa_entries(
            rows,
            fact_efforts,
            "2026-11-01",
            facts_models=facts_models,
            tbench_rows=tbench_rows,
            vals_rows=vals_rows,
            scale_rows=scale_rows,
        )
        opus = entries[("claude/claude-opus-5-5", "max")]
        self.assertEqual(len(opus["evidence"]), 4)
        self.assertIn(("moonshotai/kimi-k3", "default"), entries)


class StalenessTests(unittest.TestCase):
    def test_parse_args_and_tier_gate(self) -> None:
        self.assertEqual(check_staleness.parse_args([]).days, 30)
        old_t = {
            "entries": [
                {
                    "key": "claude/claude-opus-5-5",
                    "effort": "max",
                    "tier": "frontier",
                    "rank": {"simpleCoding": 1, "reasoning": 1},
                    "evidence": [{"kind": "benchmark", "source": "https://a", "checkedAt": "2026-11-01"}],
                }
            ]
        }
        new_single = copy.deepcopy(old_t)
        new_single["entries"][0]["tier"] = "mid"
        gate1 = check_staleness.evaluate_tier_gate(old_t, new_single)
        self.assertTrue(gate1["requires_pr"])

        new_multi = copy.deepcopy(new_single)
        new_multi["entries"][0]["evidence"].append(
            {"kind": "benchmark", "source": "https://b", "checkedAt": "2026-11-07"}
        )
        gate2 = check_staleness.evaluate_tier_gate(old_t, new_multi)
        self.assertFalse(gate2["requires_pr"])


if __name__ == "__main__":
    unittest.main()
