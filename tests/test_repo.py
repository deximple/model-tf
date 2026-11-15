#!/usr/bin/env python3
"""Unit tests for model-tf schemas, validator, vendor facts collector, benchmark collector, and staleness checker."""

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "sources"))

import check_staleness  # noqa: E402
import fetch_artificial_analysis as fetch_aa  # noqa: E402
import fetch_vendor_facts as fetch_vf  # noqa: E402
import hold_tier_shifts  # noqa: E402
import jsonschema  # noqa: E402
import refresh_common  # noqa: E402
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
        self.assertEqual(gate1["single_source_idents"], [["claude/claude-opus-5-5", "max"]])
        self.assertEqual(gate2["single_source_idents"], [])
        # Evidence carried forward from a source that failed this run is not confirmation.
        gate3 = check_staleness.evaluate_tier_gate(old_t, new_multi, unverified_sources={"https://b"})
        self.assertTrue(gate3["requires_pr"])


def _ev(source: str, note: str, checked: str) -> dict:
    return {"kind": "benchmark", "source": source, "checkedAt": checked, "note": note}


class RefreshCommonTests(unittest.TestCase):
    class _Resp:
        def __init__(self, body: bytes) -> None:
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self) -> bytes:
            return self.body

    def test_fetch_url_retries_transient_errors_then_succeeds(self) -> None:
        calls = []

        def opener(req, timeout):
            calls.append(req.full_url)
            if len(calls) < 3:
                raise urllib.error.HTTPError(req.full_url, 503, "busy", {}, None)
            return self._Resp(b"ok")

        sleeps: list[float] = []
        body = refresh_common.fetch_url("https://x.test/a", opener=opener, sleep=sleeps.append)
        self.assertEqual(body, "ok")
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleeps, [3.0, 6.0])

    def test_fetch_url_does_not_retry_permanent_4xx(self) -> None:
        calls = []

        def opener(req, timeout):
            calls.append(1)
            raise urllib.error.HTTPError(req.full_url, 404, "gone", {}, None)

        with self.assertRaises(urllib.error.HTTPError):
            refresh_common.fetch_url("https://x.test/a", opener=opener, sleep=lambda s: None)
        self.assertEqual(len(calls), 1)

    def test_fetch_url_does_not_retry_bad_certificates(self) -> None:
        import ssl

        calls = []

        def opener(req, timeout):
            calls.append(1)
            raise urllib.error.URLError(ssl.SSLCertVerificationError("certificate has expired"))

        with self.assertRaises(urllib.error.URLError):
            refresh_common.fetch_url("https://x.test/a", opener=opener, sleep=lambda s: None)
        self.assertEqual(len(calls), 1)

    def test_describe_error_is_single_line(self) -> None:
        self.assertEqual(
            refresh_common.describe_error(ValueError("bad\n  layout\r\nhere")),
            "ValueError: bad layout here",
        )

    def test_status_roundtrip_of_failed_urls(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        path = str(tmp / "s.json")
        refresh_common.write_status(path, "tiers", {"vals": "error: x"}, failed_urls={"https://v"})
        self.assertEqual(refresh_common.read_failed_urls(path), {"https://v"})
        self.assertEqual(refresh_common.read_failed_urls(str(tmp / "missing.json")), set())
        self.assertEqual(refresh_common.read_failed_urls(None), set())

    def test_option_value(self) -> None:
        self.assertEqual(refresh_common.option_value(["--touch", "--status-out", "a.json"], "--status-out"), "a.json")
        self.assertEqual(refresh_common.option_value(["--status-out=b.json"], "--status-out"), "b.json")
        self.assertIsNone(refresh_common.option_value(["--touch"], "--status-out"))


class VendorIsolationTests(unittest.TestCase):
    CLAUDE = fetch_vf.CLAUDE_OVERVIEW_URL
    GO = fetch_vf.OPENCODE_GO_URL

    def _val(self, key: str, code: str, number: float, source: str, checked: str) -> dict:
        return {
            "id": f"{key}/{code}",
            "code": code,
            "number": number,
            "text": None,
            "source": source,
            "checkedAt": checked,
        }

    def _existing(self) -> list[dict]:
        opus = "claude/claude-opus-5-5"
        haiku = "claude/claude-haiku-5"
        return [
            {
                "vendorId": "claude",
                "model": "claude-haiku-5",
                "name": "Claude Haiku 5",
                "efforts": [],
                "values": [self._val(haiku, "inputPricePerMTok", 1, self.CLAUDE, "2026-11-01")],
            },
            {
                "vendorId": "claude",
                "model": "claude-opus-5-5",
                "name": "Claude Opus 5.5",
                "efforts": ["low", "high", "max"],
                "values": [
                    self._val(opus, "inputPricePerMTok", 5, self.CLAUDE, "2026-11-01"),
                    self._val(opus, "monthlyLimitUSD/go", 20, self.GO, "2026-11-01"),
                ],
            },
        ]

    def _go_batch(self) -> list[dict]:
        opus = "claude/claude-opus-5-5"
        return [
            {
                "vendorId": "claude",
                "model": "claude-opus-5-5",
                "name": "Opus 5.5 (Go catalog)",
                "efforts": [],
                "values": [
                    self._val(opus, "inputPricePerMTok", 6, self.GO, "2026-11-15"),
                    self._val(opus, "monthlyLimitUSD/go", 20, self.GO, "2026-11-15"),
                ],
            }
        ]

    def test_failed_vendor_keeps_previous_values_name_and_efforts(self) -> None:
        merged = fetch_vf.merge_models(
            self._existing(),
            [[], self._go_batch()],
            today="2026-11-15",
            held={0: {self.CLAUDE, fetch_vf.CLAUDE_EFFORT_URL}},
        )
        by_key = {f"{m['vendorId']}/{m['model']}": m for m in merged}
        # Model only the failed vendor knew about is not dropped.
        self.assertIn("claude/claude-haiku-5", by_key)
        self.assertEqual(by_key["claude/claude-haiku-5"]["values"][0]["checkedAt"], "2026-11-01")
        opus = by_key["claude/claude-opus-5-5"]
        self.assertEqual(opus["name"], "Claude Opus 5.5")
        self.assertEqual(opus["efforts"], ["low", "high", "max"])
        vals = {v["code"]: v for v in opus["values"]}
        # 1st-party value is carried forward instead of being replaced by the catalog value.
        self.assertEqual(vals["inputPricePerMTok"]["number"], 5)
        self.assertEqual(vals["inputPricePerMTok"]["source"], self.CLAUDE)
        # Healthy vendor's unchanged value keeps its previous checkedAt.
        self.assertEqual(vals["monthlyLimitUSD/go"]["checkedAt"], "2026-11-01")

    def test_failed_lower_priority_vendor_does_not_override_fresh_first_party_value(self) -> None:
        opus = "claude/claude-opus-5-5"
        existing = self._existing()
        # Yesterday only the Go catalog supplied this id.
        existing[1]["values"].append(self._val(opus, "cachedReadPricePerMTok", 0.6, self.GO, "2026-11-01"))
        claude_batch = [
            {
                "vendorId": "claude",
                "model": "claude-opus-5-5",
                "name": "Claude Opus 5.5",
                "efforts": ["low", "high", "max"],
                "values": [
                    self._val(opus, "inputPricePerMTok", 5, self.CLAUDE, "2026-11-15"),
                    self._val(opus, "cachedReadPricePerMTok", 0.5, self.CLAUDE, "2026-11-15"),
                ],
            }
        ]
        merged = fetch_vf.merge_models(
            existing, [claude_batch, []], today="2026-11-15", held={1: {self.GO}}
        )
        vals = {v["code"]: v for v in merged[-1]["values"]}
        self.assertEqual(vals["cachedReadPricePerMTok"]["source"], self.CLAUDE)
        self.assertEqual(vals["cachedReadPricePerMTok"]["number"], 0.5)
        # Go-only values are still carried forward while Go is down.
        self.assertEqual(vals["monthlyLimitUSD/go"]["checkedAt"], "2026-11-01")

    def test_without_failures_unrefreshed_managed_models_are_dropped(self) -> None:
        merged = fetch_vf.merge_models(self._existing(), [[], self._go_batch()], today="2026-11-15")
        keys = {f"{m['vendorId']}/{m['model']}" for m in merged}
        self.assertEqual(keys, {"claude/claude-opus-5-5"})

    def test_collect_all_isolates_each_vendor(self) -> None:
        def ok(today):
            return self._go_batch()

        def boom(today):
            raise urllib.error.URLError("dns failure")

        def empty(today):
            return []

        batches, held, status = fetch_vf.collect_all(
            "2026-11-15",
            collectors=[("a", {"https://a"}, boom), ("b", {"https://b"}, ok), ("c", {"https://c"}, empty)],
        )
        self.assertEqual(status["b"], "ok")
        self.assertTrue(status["a"].startswith("error:"))
        self.assertIn("0 models", status["c"])
        self.assertEqual(held, {0: {"https://a"}, 2: {"https://c"}})
        self.assertEqual([len(b) for b in batches], [0, 1, 0])

    def test_main_aborts_without_writing_when_every_vendor_fails(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        facts = tmp / "model-facts.json"
        shutil.copyfile(fetch_vf.FACTS_PATH, facts)
        before = facts.read_text(encoding="utf-8")
        orig_path, orig_fetch = fetch_vf.FACTS_PATH, fetch_vf.fetch_url
        fetch_vf.FACTS_PATH = facts

        def down(url):
            raise urllib.error.URLError("offline")

        fetch_vf.fetch_url = down
        try:
            with self.assertRaises(SystemExit):
                fetch_vf.main(["--status-out", str(tmp / "status.json")])
        finally:
            fetch_vf.FACTS_PATH, fetch_vf.fetch_url = orig_path, orig_fetch
        self.assertEqual(facts.read_text(encoding="utf-8"), before)
        status = json.loads((tmp / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["collector"], "facts")
        self.assertTrue(all(v.startswith("error:") for v in status["sources"].values()))


class BenchmarkFailureTests(unittest.TestCase):
    FE = {"claude/claude-opus-5-5": ["high", "max"], "moonshotai/kimi-k3": []}

    def _prev(self) -> dict:
        return {
            ("claude/claude-opus-5-5", "max"): {
                "key": "claude/claude-opus-5-5",
                "effort": "max",
                "tier": "frontier",
                "rank": {"simpleCoding": 1, "reasoning": 1},
                "evidence": [
                    _ev(fetch_aa.AA_URL, "AA 59", "2026-11-01"),
                    _ev(fetch_aa.TBENCH_URL, "TB 64", "2026-11-01"),
                    _ev(fetch_aa.SCALE_URL, "Scale 77", "2026-11-01"),
                ],
            },
            ("moonshotai/kimi-k3", "default"): {
                "key": "moonshotai/kimi-k3",
                "effort": "default",
                "tier": "mid",
                "rank": {"simpleCoding": 2, "reasoning": 2},
                "evidence": [_ev(fetch_aa.VALS_URL, "Vals 53", "2026-11-01")],
            },
            ("openai/gone-model", "default"): {
                "key": "openai/gone-model",
                "effort": "default",
                "tier": "low",
                "rank": {"simpleCoding": 3, "reasoning": 3},
                "evidence": [_ev(fetch_aa.VALS_URL, "Vals 40", "2026-11-01")],
            },
        }

    def test_failed_secondary_sources_are_carried_forward(self) -> None:
        fresh = {
            ("claude/claude-opus-5-5", "max"): {
                "key": "claude/claude-opus-5-5",
                "effort": "max",
                "tier": "frontier",
                "rank": {"simpleCoding": 1, "reasoning": 1},
                "evidence": [
                    _ev(fetch_aa.AA_URL, "AA 60", "2026-11-15"),
                    _ev(fetch_aa.SCALE_URL, "Scale 78", "2026-11-15"),
                ],
            }
        }
        out = fetch_aa.carry_forward_failed_sources(
            fresh, self._prev(), {fetch_aa.TBENCH_URL, fetch_aa.VALS_URL}, self.FE
        )
        opus = out[("claude/claude-opus-5-5", "max")]
        self.assertEqual(
            [e["source"] for e in opus["evidence"]],
            [fetch_aa.AA_URL, fetch_aa.TBENCH_URL, fetch_aa.SCALE_URL],
        )
        self.assertEqual(opus["evidence"][1]["checkedAt"], "2026-11-01")
        # Entry supported only by the failed source survives with its previous tier/rank.
        kimi = out[("moonshotai/kimi-k3", "default")]
        self.assertEqual((kimi["tier"], kimi["rank"]["reasoning"]), ("mid", 2))
        # Entries whose model left model-facts.json are not resurrected.
        self.assertNotIn(("openai/gone-model", "default"), out)

    def test_no_failures_is_a_no_op(self) -> None:
        fresh = {("x/y", "default"): {"evidence": []}}
        self.assertIs(fetch_aa.carry_forward_failed_sources(fresh, self._prev(), set(), self.FE), fresh)

    def test_main_keeps_entries_when_primary_source_is_down(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        facts, tiers = tmp / "model-facts.json", tmp / "model-tiers.json"
        shutil.copyfile(fetch_aa.FACTS_PATH, facts)
        shutil.copyfile(fetch_aa.TIERS_PATH, tiers)
        before = json.loads(tiers.read_text(encoding="utf-8"))
        orig = (fetch_aa.FACTS_PATH, fetch_aa.TIERS_PATH, fetch_aa.fetch_url)

        def down(url):
            raise urllib.error.URLError("offline")

        fetch_aa.FACTS_PATH, fetch_aa.TIERS_PATH, fetch_aa.fetch_url = facts, tiers, down
        try:
            fetch_aa.main(["--status-out", str(tmp / "status.json")])
        finally:
            fetch_aa.FACTS_PATH, fetch_aa.TIERS_PATH, fetch_aa.fetch_url = orig
        after = json.loads(tiers.read_text(encoding="utf-8"))
        self.assertEqual(after, before)
        status = json.loads((tmp / "status.json").read_text(encoding="utf-8"))["sources"]
        self.assertTrue(status["artificialanalysis"].startswith("error:"))
        self.assertTrue(status["scale"].startswith("skipped"))
        self.assertEqual(
            refresh_common.read_failed_urls(str(tmp / "status.json")),
            fetch_aa.MANAGED_BENCHMARK_SOURCES,
        )


class HoldTierShiftTests(unittest.TestCase):
    def _tiers(self, tier: str, note: str, checked: str, date_: str) -> dict:
        return {
            "version": 1,
            "date": date_,
            "factsDate": "2026-11-10",
            "axis": ["simpleCoding", "reasoning"],
            "entries": [
                {
                    "key": "claude/claude-opus-5-5",
                    "effort": "max",
                    "tier": tier,
                    "rank": {"simpleCoding": 1, "reasoning": 1},
                    "evidence": [_ev(fetch_aa.AA_URL, note, checked)],
                }
            ],
        }

    def test_hold_keeps_old_tier_but_refreshes_evidence(self) -> None:
        old = self._tiers("mid", "AA 44", "2026-11-01", "2026-11-01")
        new = self._tiers("frontier", "AA 46", "2026-11-15", "2026-11-15")
        held = hold_tier_shifts.hold_shifts(old, new, [["claude/claude-opus-5-5", "max"]])
        entry = held["entries"][0]
        self.assertEqual(entry["tier"], "mid")
        self.assertEqual(entry["evidence"][0]["note"], "AA 46")
        self.assertEqual(held["date"], "2026-11-15")
        self.assertEqual(new["entries"][0]["tier"], "frontier")  # input untouched

    def test_pending_review_does_not_churn_main(self) -> None:
        # Day 2: main already holds the refreshed evidence; only the flip differs.
        main = self._tiers("mid", "AA 46", "2026-11-15", "2026-11-15")
        rerun = self._tiers("frontier", "AA 46", "2026-11-16", "2026-11-16")
        held = hold_tier_shifts.hold_shifts(main, rerun, [["claude/claude-opus-5-5", "max"]])
        self.assertEqual(held, main)



FAKE_GH = """#!{python}
# Minimal stand-in for the GitHub CLI: logs every call, answers the queries
# refresh_alert.sh makes with canned values from the environment.
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_GH_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(args) + "\\n")
if args[:2] == ["issue", "list"]:
    if os.environ.get("FAKE_GH_LIST_FAIL"):
        sys.exit(1)
    print(os.environ.get("FAKE_GH_OPEN_ISSUE", ""))
elif args[:2] == ["issue", "view"]:
    print(os.environ.get("FAKE_GH_LAST_MARKED_BODY", ""))
"""


@unittest.skipUnless(shutil.which("bash") and shutil.which("jq"), "needs bash and jq")
class RefreshAlertScriptTests(unittest.TestCase):
    SCRIPT = ROOT / "scripts" / "refresh_alert.sh"

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        bindir = self.tmp / "bin"
        bindir.mkdir()
        gh = bindir / "gh"
        gh.write_text(FAKE_GH.format(python=sys.executable), encoding="utf-8")
        gh.chmod(0o755)
        self.bindir = bindir
        self.status_dir = self.tmp / "status"
        self.status_dir.mkdir()

    def _status(self, collector: str, sources: dict) -> None:
        refresh_common.write_status(
            str(self.status_dir / f"status-{collector}.json"), collector, sources
        )

    def _run(self, expect_rc: int = 0, **env_overrides: str) -> tuple[list[list[str]], str, str]:
        log = self.tmp / "gh.log"
        summary = self.tmp / "summary.md"
        log.write_text("", encoding="utf-8")
        summary.write_text("", encoding="utf-8")
        env = dict(os.environ)
        env.update(
            {
                "PATH": f"{self.bindir}{os.pathsep}{env.get('PATH', '')}",
                "FAKE_GH_LOG": str(log),
                "STATUS_DIR": str(self.status_dir),
                "GITHUB_STEP_SUMMARY": str(summary),
                "RUN_URL": "https://github.com/o/r/actions/runs/1",
                "JOB_STATUS": "success",
                "PR_OUTCOME": "skipped",
            }
        )
        env.update(env_overrides)
        proc = subprocess.run(
            ["bash", str(self.SCRIPT)], env=env, capture_output=True, text=True
        )
        self.assertEqual(proc.returncode, expect_rc, proc.stdout + proc.stderr)
        calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]
        return calls, proc.stdout, summary.read_text(encoding="utf-8")

    @staticmethod
    def _verbs(calls: list[list[str]]) -> list[str]:
        return [" ".join(c[:2]) for c in calls]

    def _healthy(self) -> None:
        self._status("facts", {"claude": "ok", "openai": "ok"})
        self._status("tiers", {"artificialanalysis": "ok", "scale": "ok"})

    def _degraded(self) -> None:
        self._status("facts", {"claude": "ok", "openai": "error: HTTPError: 503 | busy"})
        self._status("tiers", {"artificialanalysis": "ok", "scale": "ok"})

    def test_healthy_run_without_alert_does_nothing(self) -> None:
        self._healthy()
        calls, out, summary = self._run()
        self.assertNotIn("issue create", self._verbs(calls))
        self.assertNotIn("issue close", self._verbs(calls))
        self.assertIn("All sources healthy", out)
        self.assertIn("| facts | claude | ok |", summary)

    def test_healthy_run_closes_open_alert(self) -> None:
        self._healthy()
        calls, _, _ = self._run(FAKE_GH_OPEN_ISSUE="7")
        close = [c for c in calls if c[:2] == ["issue", "close"]]
        self.assertEqual(len(close), 1)
        self.assertEqual(close[0][2], "7")

    def test_degraded_source_opens_labelled_alert(self) -> None:
        self._degraded()
        calls, out, summary = self._run()
        create = [c for c in calls if c[:2] == ["issue", "create"]]
        self.assertEqual(len(create), 1)
        self.assertIn("refresh-alert", create[0])
        body = Path(create[0][create[0].index("--body-file") + 1])
        self.assertFalse(body.exists())  # temp dir cleaned up after the run
        self.assertIn("::warning::`facts/openai`", out)
        self.assertIn("| facts | openai | error: HTTPError: 503 / busy |", summary)

    def test_same_problems_do_not_comment_again(self) -> None:
        self._degraded()
        # Same fingerprint recipe as refresh_alert.sh: failing labels only.
        line = "- `facts/openai`"
        fp = subprocess.run(
            ["bash", "-c", f"printf '%s\\n' '{line}' | sort | sha256sum | cut -c1-16"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        calls, out, _ = self._run(
            FAKE_GH_OPEN_ISSUE="7",
            FAKE_GH_LAST_MARKED_BODY=f"<!-- refresh-alert-fp:{fp} -->",
        )
        self.assertNotIn("issue comment", self._verbs(calls))
        self.assertIn("already describes these problems", out)

    def test_changed_problems_comment_on_open_alert(self) -> None:
        self._degraded()
        calls, _, _ = self._run(
            FAKE_GH_OPEN_ISSUE="7",
            FAKE_GH_LAST_MARKED_BODY="<!-- refresh-alert-fp:0000000000000000 -->",
        )
        comment = [c for c in calls if c[:2] == ["issue", "comment"]]
        self.assertEqual(len(comment), 1)
        self.assertEqual(comment[0][2], "7")
        self.assertNotIn("issue create", self._verbs(calls))

    def test_unanswerable_issue_lookup_does_not_file_duplicate(self) -> None:
        self._degraded()
        calls, out, _ = self._run(expect_rc=1, FAKE_GH_LIST_FAIL="1", ALERT_RETRY_DELAY="0")
        self.assertNotIn("issue create", self._verbs(calls))
        self.assertIn("avoid duplicates", out)

    def test_failed_job_and_pr_failure_are_reported(self) -> None:
        calls, out, summary = self._run(JOB_STATUS="failure", PR_OUTCOME="failure")
        self.assertIn("issue create", self._verbs(calls))
        self.assertIn("Refresh job status: **failure**", out)
        self.assertIn("Workflow permissions", out)
        self.assertIn("no status files", summary)


if __name__ == "__main__":
    unittest.main()
