#!/usr/bin/env python3
"""Refresh benchmark-sourced entries in model-tiers.json from multiple reputable leaderboards.

Benchmark sources:
  1. Artificial Analysis (https://artificialanalysis.ai/leaderboards/models)
     - Intelligence Index (reasoning) + Terminal-Bench 4.0 & FrontierCode 2.0 (coding)
  2. Terminal-Bench Official Leaderboard (https://www.tbench.ai/leaderboard)
     - Agentic terminal coding accuracy across 330 trials per (model, effort)
  3. Vals AI Enterprise & Coding Benchmarks (https://www.vals.ai/benchmarks)
     - Vals Index composite across enterprise coding, legal, finance, multimodal
  4. Scale AI SEAL Leaderboards (https://scale.com/leaderboard)
     - SWE Atlas, SWE-Bench Pro V2, MCP Atlas, HLE Diamond, MultiChallenge

Rules:
  - Entries with hand-curated vendor or community evidence are preserved.
  - Benchmark evidence from all 4 sources is combined per (key, effort) entry,
    and simpleCoding vs reasoning ranks are computed from the combined coding
    and reasoning scores across tracked setups.
  - Preserves existing checkedAt and bundle date when no tier, rank, or evidence
    note changed (pass --touch to force-bump).

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
TBENCH_URL = "https://www.tbench.ai/leaderboard"
VALS_URL = "https://www.vals.ai/benchmarks"
SCALE_URL = "https://scale.com/leaderboard"

MANAGED_BENCHMARK_SOURCES = {AA_URL, TBENCH_URL, VALS_URL, SCALE_URL}

# Provisional bands on the 0-100 Intelligence Index / normalized score scale.
FRONTIER_MIN = 50
MID_MIN = 25

# Explicit display name aliases -> (vendorId, model).
# Models not listed here are matched automatically against model-facts.json by normalized name/id.
NAME_MAP: dict[str, tuple[str, str]] = {
    "Claude Opus 5.5": ("claude", "claude-opus-5-5"),
    "Opus 5.5": ("claude", "claude-opus-5-5"),
    "Claude Sonnet 5.5": ("claude", "claude-sonnet-5-5"),
    "Sonnet 5.5": ("claude", "claude-sonnet-5-5"),
    "Claude Haiku 5.5": ("claude", "claude-haiku-5-5"),
    "Haiku 5.5": ("claude", "claude-haiku-5-5"),
    "Claude Fable 5.1": ("claude", "claude-fable-5-1"),
    "Fable 5.1": ("claude", "claude-fable-5-1"),
    "Fable-5.1": ("claude", "claude-fable-5-1"),
    "GPT-6 Astra": ("openai", "gpt-6-astra"),
    "GPT 6 Astra": ("openai", "gpt-6-astra"),
    "GPT-6-Astra": ("openai", "gpt-6-astra"),
    "GPT-6.1 Sol": ("openai", "gpt-6.1-sol"),
    "GPT 6.1 Sol": ("openai", "gpt-6.1-sol"),
    "GPT-6.1-Sol": ("openai", "gpt-6.1-sol"),
    "GPT-6.1 Terra": ("openai", "gpt-6.1-terra"),
    "GPT 6.1 Terra": ("openai", "gpt-6.1-terra"),
    "GPT-6 Luna": ("openai", "gpt-6-luna"),
    "GPT 6 Luna": ("openai", "gpt-6-luna"),
    "GPT-5.6 Luna": ("openai", "gpt-5.6-luna"),
    "GPT 5.6 Luna": ("openai", "gpt-5.6-luna"),
    "Gemini 4 Argon": ("google", "gemini-4-argon"),
    "Gemini-4-Argon": ("google", "gemini-4-argon"),
    "Gemini 3.8 Flash": ("google", "gemini-3.8-flash"),
    "Gemini 3.6 Flash": ("google", "gemini-3.6-flash"),
    "Gemini 3.5 Flash-Lite": ("google", "gemini-3.5-flash-lite"),
    "Gemini 3.1 Pro Preview": ("google", "gemini-3.1-pro-preview"),
    "Gemini 3.1 Flash-Lite": ("google", "gemini-3.1-flash-lite"),
    "Grok 4.7": ("xai", "grok-4.7"),
    "Grok 4.6": ("xai", "grok-4.6"),
    "Grok 4.5": ("xai", "grok-4.5"),
    "Grok 4.3": ("xai", "grok-4.3"),
    "GLM-5.3-Flash": ("zhipuai", "glm-5.3-flash"),
    "GLM-5.3": ("zhipuai", "glm-5.3"),
    "GLM 5.3": ("zhipuai", "glm-5.3"),
    "GLM-5.2": ("zhipuai", "glm-5.2"),
    "GLM-5.1": ("zhipuai", "glm-5.1"),
    "GLM-5": ("zhipuai", "glm-5"),
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
    "MiniMax M3.1 Flash": ("minimax", "minimax-m3.1-flash"),
    "MiniMax-M3": ("minimax", "minimax-m3"),
    "Muse Spark 1.3": ("meta", "muse-spark-1.3-contributor"),
    "Muse Spark 1.2": ("meta", "muse-spark-1.2-contributor"),
    "muse-spark-1.2": ("meta", "muse-spark-1.2-contributor"),
    "Qwen3.8 Max": ("alibaba", "qwen3.8-max"),
    "Qwen3.8-Max-0902": ("alibaba", "qwen3.8-max"),
    "Qwen 3.8 Max": ("alibaba", "qwen3.8-max"),
    "Qwen3.8 Flash": ("alibaba", "qwen3.8-flash"),
    "Qwen3.7 Plus": ("alibaba", "qwen3.7-plus"),
    "DeepSeek V4.1 Flash": ("deepseek", "deepseek-v4.1-flash"),
    "DeepSeek V4 Pro": ("deepseek", "deepseek-v4-pro"),
    "Tencent Hy4": ("tencent", "hy4"),
    "Tencent Hy3": ("tencent", "hy3"),
    "Hy4": ("tencent", "hy4"),
    "Hy4 Preview": ("tencent", "hy4-preview"),
    "Hy3": ("tencent", "hy3"),
}

EFFORT_WORDS = {"low", "medium", "high", "xhigh", "max", "none", "non-reasoning", "minimal"}
CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
NORM_RE = re.compile(r"[^a-z0-9]+")


def fetch_url(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "model-tf-refresh/1.0"
            )
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_leaderboard() -> str:
    return fetch_url(AA_URL)


def _cell_text(fragment: str) -> str:
    return WS_RE.sub(" ", html.unescape(TAG_RE.sub(" ", fragment))).strip()


def _extract_rsc_text(page: str) -> str:
    pushes = [
        json.loads(f'"{m}"')
        for m in re.findall(
            r'self\.__next_f\.push\(\[1,"((?:\\.|[^"\\])*)"\]\)', page, re.S
        )
    ]
    return "".join(pushes)


def _extract_json_array_at(text: str, start_bracket: int) -> list[Any]:
    depth = 0
    for i in range(start_bracket, len(text)):
        if text[i] == "[":
            depth += 1
        elif text[i] == "]":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start_bracket : i + 1])
                except json.JSONDecodeError:
                    return []
    return []


def parse_rsc_models(page: str) -> list[tuple[str, float, float | None]]:
    """Extract [(short_name, intelligence_index, coding_score)] from AA RSC payload."""
    full = _extract_rsc_text(page)
    if not full:
        return []
    marker = '"initialModels":['
    idx = full.find(marker)
    if idx == -1:
        return []
    arr = _extract_json_array_at(full, idx + len(marker) - 1)
    if not arr:
        return []

    out: list[tuple[str, float, float | None]] = []
    for item in arr:
        ii = item.get("intelligenceIndex")
        if ii is None:
            continue
        name = item.get("shortName") or item.get("name") or ""
        if not name:
            continue
        scores = item.get("scores") or {}
        tb4 = scores.get("terminalbench-4-0")
        fc2 = scores.get("frontiercode-2-0")
        coding = (
            round(((float(tb4) + float(fc2)) / 2.0) * 100.0, 1)
            if tb4 is not None and fc2 is not None
            else None
        )
        out.append((name, round(float(ii), 1), coding))
    out.sort(key=lambda r: -r[1])
    return out


def parse_rows(page: str) -> list[tuple[str, float]]:
    """Return [(model_cell, score)] for every scored leaderboard row in HTML."""
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


def parse_tbench_leaderboard(page: str) -> list[dict[str, Any]]:
    """Parse Stanford/Laude Terminal-Bench leaderboard entries from RSC payload."""
    full = _extract_rsc_text(page)
    if not full:
        return []
    idx = full.find('"leaderboard_id":')
    if idx == -1:
        return []
    start = full.rfind("[", 0, idx)
    if start == -1:
        return []
    arr = _extract_json_array_at(full, start)
    out: list[dict[str, Any]] = []
    for item in arr:
        if not isinstance(item, dict):
            continue
        meta = item.get("metadata") or {}
        met = item.get("metrics") or {}
        md = meta.get("model_display") or {}
        ad = meta.get("agent_display") or {}
        label = md.get("label")
        acc = met.get("accuracy")
        if not label or acc is None:
            continue
        out.append(
            {
                "model_label": label.strip(),
                "effort": (meta.get("reasoning_effort") or "default").strip().lower(),
                "agent": (ad.get("label") or "").strip(),
                "accuracy": round(float(acc), 2),
            }
        )
    return out


def parse_vals_benchmarks(page: str) -> list[dict[str, Any]]:
    """Parse Vals AI Index model scores from Astro props on https://www.vals.ai/benchmarks."""
    out: list[dict[str, Any]] = []
    for m in re.finditer(r'props="([^"]*points[^"]*)"', page):
        try:
            props = json.loads(html.unescape(m.group(1)))
        except json.JSONDecodeError:
            continue
        companies = props.get("companies")
        if not (isinstance(companies, list) and len(companies) == 2 and companies[0] == 1):
            continue
        for elem in companies[1]:
            if not (isinstance(elem, list) and len(elem) == 2):
                continue
            obj = elem[1]
            pts = obj.get("points", [1, []])[1]
            for pt in pts:
                pt_obj = pt[1]
                label = pt_obj.get("label", [0, ""])[1]
                score = pt_obj.get("score", [0, None])[1]
                if label and score is not None:
                    out.append(
                        {
                            "model_label": str(label).strip(),
                            "score": round(float(score), 1),
                        }
                    )
    return out


def parse_scale_leaderboard(page: str) -> list[dict[str, Any]]:
    """Parse Scale AI SEAL leaderboard scores from https://scale.com/leaderboard."""
    full = _extract_rsc_text(page)
    if not full:
        return []
    target_ids = {
        "swe-atlas": "SWE Atlas",
        "hle-diamond": "HLE Diamond",
        "hss": "Humanity's Sixth Sense",
        "multichallenge": "MultiChallenge",
        "mcp_atlas": "MCP Atlas",
    }
    seen_Bench: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for m in re.finditer(
        r'"category":"([^"]+)","name":"([^"]+)","id":"([^"]+)"[^[]*"scores":(\[.*?\])\s*[,}]',
        full,
    ):
        cat, bname, bid, scores_s = m.group(1), m.group(2), m.group(3), m.group(4)
        if bid not in target_ids:
            continue
        if bid == "swe-atlas" and bname != "SWE Atlas - Overall":
            continue
        if bid == "multichallenge" and "Small" in bname:
            continue
        try:
            scores = json.loads(scores_s)
        except json.JSONDecodeError:
            continue
        for item in scores:
            raw_m = (item.get("model") or "").replace("*", "").strip()
            sc = item.get("score")
            if not raw_m or sc is None:
                continue
            key_pair = (bname, raw_m)
            if key_pair in seen_Bench:
                continue
            seen_Bench.add(key_pair)
            out.append(
                {
                    "benchmark": target_ids[bid],
                    "category": cat,
                    "raw_model": raw_m,
                    "score": round(float(sc), 1),
                }
            )
    return out


def parse_name(name: str) -> tuple[str, str]:
    """Split 'Model (effort)' into (base, effort). Date tags like (0902) are dropped."""
    effort, base = "default", name.strip()
    m_trail = re.match(
        r"^(.*?)\s*\([^()]*(?:agent|Claude Code|Codex)[^()]*\)\s*(low|medium|high|xhigh|max|none)\s*$",
        base,
        re.I,
    )
    if m_trail:
        return m_trail.group(1).strip(), m_trail.group(2).lower()
    while True:
        m = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", base)
        if not m:
            break
        inner = m.group(2).strip()
        lower_inner = inner.lower()
        if lower_inner in EFFORT_WORDS:
            effort = "none" if lower_inner == "non-reasoning" else lower_inner
            base = m.group(1).strip()
        elif re.fullmatch(r"\d{4}", inner):
            base = m.group(1).strip()
        else:
            break
    base = re.sub(r"\s+\d{4}$", "", base)
    base = re.sub(r"-0902$", "", base)
    return base, effort


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
    rounded = round(score)
    if rounded >= FRONTIER_MIN:
        return "frontier"
    if rounded >= MID_MIN:
        return "mid"
    return "low"


def is_aa_managed(entry: dict[str, Any]) -> bool:
    ev = entry.get("evidence") or []
    return bool(ev) and all(
        e.get("kind") == "benchmark" and e.get("source") in MANAGED_BENCHMARK_SOURCES
        for e in ev
    )


def _valid_effort(effort: str, model_efforts: list[str]) -> bool:
    if not model_efforts:
        return effort == "default"
    return effort in model_efforts


def _primary_effort(model_efforts: list[str], preferred: str = "default") -> str:
    if not model_efforts:
        return "default"
    if preferred in model_efforts:
        return preferred
    for cand in ("max", "xhigh", "high", "medium", "low"):
        if cand in model_efforts:
            return cand
    return model_efforts[-1]


def _norm_token(text: str) -> str:
    return NORM_RE.sub("", text.lower())


def build_auto_name_index(
    facts_models: list[dict[str, Any]] | None,
) -> dict[str, tuple[str, str]]:
    """Build normalized-name -> (vendorId, model) lookup from model-facts.json."""
    idx: dict[str, tuple[str, str]] = {}
    if not facts_models:
        return idx
    for m in facts_models:
        vid, mid, name = m["vendorId"], m["model"], m.get("name", "")
        for candidate in (name, mid, f"{vid} {name}", f"{vid} {mid}"):
            norm = _norm_token(candidate)
            if norm:
                idx.setdefault(norm, (vid, mid))
    return idx


def resolve_model(
    base: str, auto_index: dict[str, tuple[str, str]]
) -> tuple[str, str] | None:
    hit = NAME_MAP.get(base)
    if hit:
        return hit
    return auto_index.get(_norm_token(base))


def build_aa_entries(
    rows: list[tuple[str, float]] | list[tuple[str, float, float | None]],
    fact_efforts: dict[str, list[str]],
    today: str,
    existing_aa: dict[tuple[str, str], dict[str, Any]] | None = None,
    touch: bool = False,
    facts_models: list[dict[str, Any]] | None = None,
    tbench_rows: list[dict[str, Any]] | None = None,
    vals_rows: list[dict[str, Any]] | None = None,
    scale_rows: list[dict[str, Any]] | None = None,
) -> dict[tuple[str, str], dict[str, Any]]:
    auto_index = build_auto_name_index(facts_models)
    existing = existing_aa or {}

    # ident -> {"reasoning": float, "coding": float, "evidence": list[dict]}
    records: dict[tuple[str, str], dict[str, Any]] = {}

    # 1. Primary source: Artificial Analysis
    for row in rows:
        name = row[0]
        score = float(row[1])
        coding_score: float | None = (
            float(row[2]) if len(row) > 2 and row[2] is not None else None
        )
        base, effort = parse_name(name)
        hit = resolve_model(base, auto_index)
        if not hit:
            continue
        vid, mid = hit
        key = f"{vid}/{mid}"
        if key not in fact_efforts:
            continue
        if not _valid_effort(effort, fact_efforts[key]):
            continue
        ident = (key, effort)
        if ident in records:
            continue
        if coding_score is not None:
            note = (
                f"Artificial Analysis Intelligence Index {score:g}, "
                f"Coding (Terminal-Bench 4.0 + FrontierCode 2.0) {coding_score:g}"
            )
        else:
            note = (
                f"Artificial Analysis Intelligence Index {score:g} "
                "(composite; axis ranks provisional)"
            )
        records[ident] = {
            "reasoning": score,
            "coding": coding_score if coding_score is not None else score,
            "evidence": [
                {
                    "kind": "benchmark",
                    "source": AA_SOURCE,
                    "checkedAt": today,
                    "note": note,
                }
            ],
        }

    # 2. Secondary source: Stanford / Laude Institute Terminal-Bench (tbench.ai)
    for item in tbench_rows or []:
        base, _ = parse_name(item["model_label"])
        hit = resolve_model(base, auto_index)
        if not hit:
            continue
        key = f"{hit[0]}/{hit[1]}"
        if key not in fact_efforts:
            continue
        eff = _primary_effort(fact_efforts[key], item["effort"])
        if not _valid_effort(eff, fact_efforts[key]):
            continue
        ident = (key, eff)
        acc = float(item["accuracy"])
        agent_sfx = f" ({item['agent']})" if item.get("agent") else ""
        ev_item = {
            "kind": "benchmark",
            "source": TBENCH_URL,
            "checkedAt": today,
            "note": f"Terminal-Bench accuracy {acc:g}%{agent_sfx}",
        }
        if ident in records:
            if not any(e["source"] == TBENCH_URL for e in records[ident]["evidence"]):
                records[ident]["evidence"].append(ev_item)
                records[ident]["coding"] = round(
                    (records[ident]["coding"] + acc) / 2.0, 1
                )
        else:
            records[ident] = {
                "reasoning": round(acc * 0.85, 1),
                "coding": acc,
                "evidence": [ev_item],
            }

    # 3. Secondary source: Vals AI Enterprise & Coding Benchmarks (vals.ai)
    for item in vals_rows or []:
        base, eff_parsed = parse_name(item["model_label"])
        hit = resolve_model(base, auto_index)
        if not hit:
            continue
        key = f"{hit[0]}/{hit[1]}"
        if key not in fact_efforts:
            continue
        eff = _primary_effort(fact_efforts[key], eff_parsed)
        if not _valid_effort(eff, fact_efforts[key]):
            continue
        ident = (key, eff)
        vscore = float(item["score"])
        ev_item = {
            "kind": "benchmark",
            "source": VALS_URL,
            "checkedAt": today,
            "note": f"Vals AI Index {vscore:g}%",
        }
        if ident in records:
            if not any(e["source"] == VALS_URL for e in records[ident]["evidence"]):
                records[ident]["evidence"].append(ev_item)
        else:
            # Map Vals Index (~50-71 scale) onto the ~20-60 AA scale for tiering/ranking
            norm_score = round(vscore * 0.65, 1)
            records[ident] = {
                "reasoning": norm_score,
                "coding": norm_score,
                "evidence": [ev_item],
            }

    # 4. Secondary source: Scale AI SEAL Leaderboards (scale.com/leaderboard)
    scale_notes_by_ident: dict[tuple[str, str], list[str]] = {}
    for item in scale_rows or []:
        base, eff_parsed = parse_name(item["raw_model"])
        hit = resolve_model(base, auto_index)
        if not hit:
            continue
        key = f"{hit[0]}/{hit[1]}"
        if key not in fact_efforts:
            continue
        eff = _primary_effort(fact_efforts[key], eff_parsed)
        if not _valid_effort(eff, fact_efforts[key]):
            continue
        ident = (key, eff)
        scale_notes_by_ident.setdefault(ident, []).append(
            f"{item['benchmark']} {item['score']:g}"
        )
    for ident, notes in scale_notes_by_ident.items():
        if ident in records:
            records[ident]["evidence"].append(
                {
                    "kind": "benchmark",
                    "source": SCALE_URL,
                    "checkedAt": today,
                    "note": "Scale SEAL: " + ", ".join(notes),
                }
            )

    r_ranks = competition_ranks(
        [(ident, rec["reasoning"]) for ident, rec in records.items()]
    )
    c_ranks = competition_ranks(
        [(ident, rec["coding"]) for ident, rec in records.items()]
    )

    entries: dict[tuple[str, str], dict[str, Any]] = {}
    for ident, rec in records.items():
        key, effort = ident
        tier = tier_of(rec["reasoning"])
        rank = {"simpleCoding": c_ranks[ident], "reasoning": r_ranks[ident]}
        prev = existing.get(ident)
        prev_ev_by_src = {
            e.get("source"): e for e in (prev.get("evidence") or [])
        } if prev else {}
        same_tier_rank = (
            not touch
            and prev is not None
            and prev.get("tier") == tier
            and prev.get("rank") == rank
        )
        final_ev: list[dict[str, Any]] = []
        for ev in rec["evidence"]:
            old_ev = prev_ev_by_src.get(ev["source"])
            checked_at = (
                old_ev.get("checkedAt", today)
                if (same_tier_rank and old_ev and old_ev.get("note") == ev["note"])
                else today
            )
            final_ev.append(
                {
                    "kind": ev["kind"],
                    "source": ev["source"],
                    "checkedAt": checked_at,
                    "note": ev["note"],
                }
            )
        entries[ident] = {
            "key": key,
            "effort": effort,
            "tier": tier,
            "rank": rank,
            "evidence": final_ev,
        }
    return entries


def _safe_fetch_list(label: str, fn: Any, url: str) -> list[dict[str, Any]]:
    try:
        return fn(fetch_url(url))
    except Exception as exc:
        print(f"WARN: {label} fetch failed ({exc}); continuing with remaining sources")
        return []


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
    rsc_rows = parse_rsc_models(page)
    rows: list[tuple[str, float]] | list[tuple[str, float, float | None]] = (
        rsc_rows if rsc_rows else parse_rows(page)
    )
    if not rows:
        sys.exit("no AA leaderboard rows parsed; aborting without changes")

    tbench_rows = _safe_fetch_list("Terminal-Bench", parse_tbench_leaderboard, TBENCH_URL)
    vals_rows = _safe_fetch_list("Vals AI", parse_vals_benchmarks, VALS_URL)
    scale_rows = _safe_fetch_list("Scale SEAL", parse_scale_leaderboard, SCALE_URL)

    existing_aa = {
        (e["key"], e["effort"]): e for e in tiers["entries"] if is_aa_managed(e)
    }
    aa_entries = build_aa_entries(
        rows,
        fact_efforts,
        today,
        existing_aa=existing_aa,
        touch=touch,
        facts_models=facts["models"],
        tbench_rows=tbench_rows,
        vals_rows=vals_rows,
        scale_rows=scale_rows,
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
        f"{len(regenerated)} benchmark-managed = {len(merged)} entries "
        f"(aa={len(rows)}, tbench={len(tbench_rows)}, vals={len(vals_rows)}, "
        f"scale={len(scale_rows)}, changed={changed})"
    )


if __name__ == "__main__":
    main()
