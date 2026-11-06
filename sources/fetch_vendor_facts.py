#!/usr/bin/env python3
"""Refresh model-facts.json from official vendor documentation pages.

Automated sources:
  1. Anthropic Claude:
     - https://platform.claude.com/docs/en/models/overview (.md)
     - https://platform.claude.com/docs/en/build-with-claude/effort (.md)
  2. OpenAI / Codex:
     - https://developers.openai.com/api/docs/models (+ per-model pages)
     - https://learn.chatgpt.com/docs/models
  3. OpenCode Go:
     - https://opencode.ai/docs/go/

Rules:
  - Preserves existing checkedAt on unchanged values and keeps facts.date
    unchanged when no model or value changed (pass --touch to force-bump).
  - Preserves any models or values whose source URL is outside the managed
    vendor documentation URLs so hand-curated facts are never lost.
  - Fails fast without writing if any automated source returns zero models.

Stdlib only. Run from anywhere: python3 sources/fetch_vendor_facts.py [--touch]
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

CLAUDE_OVERVIEW_URL = "https://platform.claude.com/docs/en/models/overview"
CLAUDE_OVERVIEW_MD_URL = "https://platform.claude.com/docs/en/models/overview.md"
CLAUDE_EFFORT_URL = "https://platform.claude.com/docs/en/build-with-claude/effort"
CLAUDE_EFFORT_MD_URL = "https://platform.claude.com/docs/en/build-with-claude/effort.md"
OPENAI_MODELS_URL = "https://developers.openai.com/api/docs/models"
CHATGPT_MODELS_URL = "https://learn.chatgpt.com/docs/models"
OPENCODE_GO_URL = "https://opencode.ai/docs/go/"

MANAGED_SOURCES = {
    CLAUDE_OVERVIEW_URL,
    CLAUDE_EFFORT_URL,
    OPENAI_MODELS_URL,
    CHATGPT_MODELS_URL,
    OPENCODE_GO_URL,
}

EFFORT_ORDER = ["none", "minimal", "low", "medium", "high", "xhigh", "max"]

# Explicit maker vendorId overrides for models hosted on OpenCode Go.
MAKER_BY_MODEL: dict[str, str] = {
    "claude-fable-5-1": "claude",
    "claude-opus-5-5": "claude",
    "claude-sonnet-5-5": "claude",
    "claude-haiku-5-5": "claude",
    "gpt-6-astra": "openai",
    "gpt-6.1-sol": "openai",
    "gpt-6.1-terra": "openai",
    "gpt-6-luna": "openai",
    "gpt-5.6-luna": "openai",
    "glm-5.3-flash": "zhipuai",
    "glm-5.3": "zhipuai",
    "glm-5.2": "zhipuai",
    "kimi-k3.1-pro": "moonshotai",
    "kimi-k3": "moonshotai",
    "kimi-k2.7-code": "moonshotai",
    "longcat-2.0": "meituan",
    "step-5-preview": "stepfun",
    "beam-free": "opencode",
    "crouton-alpha-free": "opencode",
    "iceman-contributor-free": "opencode",
    "mimo-v2.6-flash": "xiaomi",
    "mimo-v2.6-pro": "xiaomi",
    "mimo-v2.5": "xiaomi",
    "mimo-v2.5-pro": "xiaomi",
    "minimax-m3.1-flash": "minimax",
    "minimax-m3": "minimax",
    "muse-spark-1.3-contributor": "meta",
    "muse-spark-1.2-contributor": "meta",
    "qwen3.8-max": "alibaba",
    "qwen3.8-flash": "alibaba",
    "qwen3.7-plus": "alibaba",
    "deepseek-v4.1-flash": "deepseek",
    "deepseek-v4-pro": "deepseek",
    "hy4": "tencent",
    "hy4-preview": "tencent",
    "hy3": "tencent",
    "grok-4.7": "xai",
    "gemini-4-argon": "google",
}

MAKER_PREFIX_RULES: list[tuple[str, str]] = [
    ("claude-", "claude"),
    ("gpt-", "openai"),
    ("o1-", "openai"),
    ("o3-", "openai"),
    ("o4-", "openai"),
    ("gemini-", "google"),
    ("grok-", "xai"),
    ("glm-", "zhipuai"),
    ("kimi-", "moonshotai"),
    ("longcat-", "meituan"),
    ("step-", "stepfun"),
    ("mimo-", "xiaomi"),
    ("minimax-", "minimax"),
    ("muse-", "meta"),
    ("llama-", "meta"),
    ("qwen", "alibaba"),
    ("deepseek-", "deepseek"),
    ("hy", "tencent"),
    ("hunyuan", "tencent"),
    ("mistral-", "mistral"),
]

# Verified multi-effort capabilities for models whose primary fact table does not list efforts.
KNOWN_EFFORTS: dict[str, list[str]] = {
    "gpt-5.6-luna": ["none", "low", "medium", "high", "xhigh", "max"],
    "deepseek-v4-pro": ["none", "low", "high", "max"],
    "deepseek-v4.1-flash": ["none", "low", "high", "max"],
    "gemini-4-argon": ["low", "medium", "high"],
    "grok-4.7": ["low", "medium", "high", "xhigh"],
    "glm-5.3": ["low", "high", "max"],
}

NAME_OVERRIDES: dict[str, str] = {
    "gpt-5.6-luna": "GPT-5.6 Luna",
    "gpt-6-luna": "GPT-6 Luna",
    "hy4-preview": "Hy4 Preview",
}

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)


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
    with urllib.request.urlopen(req, timeout=45) as resp:
        return resp.read().decode("utf-8", errors="replace")


def infer_maker(model_id: str) -> str:
    if model_id in MAKER_BY_MODEL:
        return MAKER_BY_MODEL[model_id]
    lower = model_id.lower()
    for prefix, maker in MAKER_PREFIX_RULES:
        if lower.startswith(prefix):
            return maker
    return "opencode"


def _clean_num(val: float) -> int | float:
    return int(val) if val.is_integer() else val


def _parse_token_count(text: str) -> int | None:
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([MmKk])\s*tokens", text)
    if m:
        num = float(m.group(1))
        mult = 1_000_000 if m.group(2).upper() == "M" else 1_000
        return int(num * mult)
    m_raw = re.search(r"([0-9,]+)\s+tokens", text)
    if m_raw:
        return int(m_raw.group(1).replace(",", ""))
    return None


def parse_claude_docs(overview_md: str, effort_md: str, today: str) -> list[dict[str, Any]]:
    """Parse Claude model specs from overview.md and effort.md."""
    table_lines = [
        line.strip()
        for line in overview_md.splitlines()
        if line.strip().startswith("|")
    ]
    if len(table_lines) < 3:
        return []

    headers = [c.strip() for c in table_lines[0].strip("|").split("|")]
    col_count = len(headers) - 1
    if col_count <= 0:
        return []

    rows: dict[str, list[str]] = {}
    for line in table_lines[2:]:
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != len(headers):
            break
        rows[cells[0]] = cells[1:]

    def find_row(substr: str) -> list[str]:
        for k, v in rows.items():
            if substr.lower() in k.lower():
                return v
        return [""] * col_count

    api_ids = [re.sub(r"`", "", c).strip() for c in find_row("Claude API ID")]
    latencies = find_row("Comparative latency")
    pricings = find_row("Pricing")
    def_efforts = [re.sub(r"`", "", c).strip() for c in find_row("Default effort")]
    contexts = find_row("Context window")
    max_outputs = find_row("Max output")

    supports_text_image = (
        "text and image input" in overview_md.lower()
    )
    efforts_list = ["low", "medium", "high", "xhigh", "max"]
    for w in EFFORT_ORDER:
        if f"`{w}`" in effort_md and w not in efforts_list:
            efforts_list.append(w)
    efforts_list = [e for e in EFFORT_ORDER if e in efforts_list]

    models: list[dict[str, Any]] = []
    for i in range(col_count):
        mid = api_ids[i]
        name = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", headers[i + 1]).strip()
        if not mid:
            continue
        vid = "claude"
        values: list[dict[str, Any]] = []

        ctx = _parse_token_count(contexts[i])
        if ctx is not None:
            values.append(
                {
                    "id": f"{vid}/{mid}/contextTokens",
                    "code": "contextTokens",
                    "number": ctx,
                    "text": None,
                    "source": CLAUDE_OVERVIEW_URL,
                    "checkedAt": today,
                }
            )
        max_out = _parse_token_count(max_outputs[i])
        if max_out is not None:
            values.append(
                {
                    "id": f"{vid}/{mid}/maxOutputTokens",
                    "code": "maxOutputTokens",
                    "number": max_out,
                    "text": None,
                    "source": CLAUDE_OVERVIEW_URL,
                    "checkedAt": today,
                }
            )
        if supports_text_image:
            values.append(
                {
                    "id": f"{vid}/{mid}/inputKinds",
                    "code": "inputKinds",
                    "number": None,
                    "text": "text,image",
                    "source": CLAUDE_OVERVIEW_URL,
                    "checkedAt": today,
                }
            )
        m_in = re.search(r"\$([0-9]+(?:\.[0-9]+)?)\s*/\s*input", pricings[i])
        m_out = re.search(r"\$([0-9]+(?:\.[0-9]+)?)\s*/\s*output", pricings[i])
        in_price_val: float | None = None
        if m_in:
            in_price_val = float(m_in.group(1))
            values.append(
                {
                    "id": f"{vid}/{mid}/inputPricePerMTok",
                    "code": "inputPricePerMTok",
                    "number": _clean_num(in_price_val),
                    "text": None,
                    "source": CLAUDE_OVERVIEW_URL,
                    "checkedAt": today,
                }
            )
        if m_out:
            values.append(
                {
                    "id": f"{vid}/{mid}/outputPricePerMTok",
                    "code": "outputPricePerMTok",
                    "number": _clean_num(float(m_out.group(1))),
                    "text": None,
                    "source": CLAUDE_OVERVIEW_URL,
                    "checkedAt": today,
                }
            )
        if in_price_val is not None:
            plain_md = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", overview_md)
            cache_ratio = 0.10
            for m_pct in re.finditer(
                r"([0-9]+(?:\.[0-9]+)?)%\s+on\s+([^,\)\n;]+)", plain_md
            ):
                if name.lower() in m_pct.group(2).lower():
                    cache_ratio = float(m_pct.group(1)) / 100.0
                    break
            values.append(
                {
                    "id": f"{vid}/{mid}/cachedReadPricePerMTok",
                    "code": "cachedReadPricePerMTok",
                    "number": _clean_num(round(in_price_val * cache_ratio, 6)),
                    "text": None,
                    "source": CLAUDE_OVERVIEW_URL,
                    "checkedAt": today,
                }
            )
        if def_efforts[i]:
            values.append(
                {
                    "id": f"{vid}/{mid}/defaultEffort",
                    "code": "defaultEffort",
                    "number": None,
                    "text": def_efforts[i],
                    "source": CLAUDE_EFFORT_URL,
                    "checkedAt": today,
                }
            )
        if latencies[i] and " " not in latencies[i]:
            values.append(
                {
                    "id": f"{vid}/{mid}/comparativeLatency",
                    "code": "comparativeLatency",
                    "number": None,
                    "text": latencies[i],
                    "source": CLAUDE_OVERVIEW_URL,
                    "checkedAt": today,
                }
            )
        models.append(
            {
                "vendorId": vid,
                "model": mid,
                "name": name,
                "efforts": list(efforts_list),
                "values": values,
            }
        )
    return models


def discover_openai_flagship_slugs(index_html: str) -> list[str]:
    cutoff = index_html.find("gpt-6-cyber")
    region = index_html[: cutoff if cutoff != -1 else len(index_html)]
    slugs: list[str] = []
    excluded = ("cyber", "image", "realtime", "live", "transcribe", "audio", "tts")
    for m in re.finditer(r'href="/api/docs/models/(gpt-[a-z0-9.-]+)"', region):
        slug = m.group(1)
        if slug not in slugs and not any(tok in slug for tok in excluded):
            slugs.append(slug)
    return slugs


def parse_openai_model_page(
    slug: str, page_html: str, codex_ctx_by_name: dict[str, int], today: str
) -> dict[str, Any]:
    txt = WS_RE.sub(" ", html.unescape(TAG_RE.sub(" ", page_html)))
    m_title = re.search(r"<title[^>]*>\s*([^<|]+?)\s+Model\s*\|", page_html)
    name = m_title.group(1).strip() if m_title else NAME_OVERRIDES.get(slug, slug)
    name = NAME_OVERRIDES.get(slug, name)

    efforts: list[str] = []
    default_effort: str | None = None
    m_eff = re.search(r"reasoning\.effort\s+supports\s+([^\.]+)\.", txt)
    if m_eff:
        eff_clause = m_eff.group(1)
        found = set(re.findall(r"\b(none|minimal|low|medium|high|xhigh|max)\b", eff_clause))
        efforts = [e for e in EFFORT_ORDER if e in found]
        m_def = re.search(
            r"\b(none|minimal|low|medium|high|xhigh|max)\s*\(default\)", eff_clause
        )
        if m_def:
            default_effort = m_def.group(1)

    vid = "openai"
    values: list[dict[str, Any]] = []

    if default_effort:
        values.append(
            {
                "id": f"{vid}/{slug}/defaultEffort",
                "code": "defaultEffort",
                "number": None,
                "text": default_effort,
                "source": OPENAI_MODELS_URL,
                "checkedAt": today,
            }
        )

    m_ctx = re.search(r"([0-9,]+)\s+context window", txt)
    if m_ctx:
        values.append(
            {
                "id": f"{vid}/{slug}/contextTokens",
                "code": "contextTokens",
                "number": int(m_ctx.group(1).replace(",", "")),
                "text": None,
                "source": OPENAI_MODELS_URL,
                "checkedAt": today,
            }
        )
    if name in codex_ctx_by_name:
        values.append(
            {
                "id": f"{vid}/{slug}/codexContextTokens",
                "code": "codexContextTokens",
                "number": codex_ctx_by_name[name],
                "text": None,
                "source": CHATGPT_MODELS_URL,
                "checkedAt": today,
            }
        )
    m_out = re.search(r"([0-9,]+)\s+max output tokens", txt)
    if m_out:
        values.append(
            {
                "id": f"{vid}/{slug}/maxOutputTokens",
                "code": "maxOutputTokens",
                "number": int(m_out.group(1).replace(",", "")),
                "text": None,
                "source": OPENAI_MODELS_URL,
                "checkedAt": today,
            }
        )
    m_in = re.search(r"Input\s+(Text(?:,\s*Image)?)\s+Output\s+Text", txt)
    if m_in:
        values.append(
            {
                "id": f"{vid}/{slug}/inputKinds",
                "code": "inputKinds",
                "number": None,
                "text": m_in.group(1).lower().replace(" ", ""),
                "source": OPENAI_MODELS_URL,
                "checkedAt": today,
            }
        )
    m_price = re.search(
        r"Text tokens\s+Per 1M tokens\s+Input\s+\$([0-9.]+)"
        r"(?:.*?Cached input\s+\$([0-9.]+))?.*?Output\s+\$([0-9.]+)",
        txt,
    )
    if m_price:
        values.append(
            {
                "id": f"{vid}/{slug}/inputPricePerMTok",
                "code": "inputPricePerMTok",
                "number": _clean_num(float(m_price.group(1))),
                "text": None,
                "source": OPENAI_MODELS_URL,
                "checkedAt": today,
            }
        )
        if m_price.group(2):
            values.append(
                {
                    "id": f"{vid}/{slug}/cachedReadPricePerMTok",
                    "code": "cachedReadPricePerMTok",
                    "number": _clean_num(float(m_price.group(2))),
                    "text": None,
                    "source": OPENAI_MODELS_URL,
                    "checkedAt": today,
                }
            )
        values.append(
            {
                "id": f"{vid}/{slug}/outputPricePerMTok",
                "code": "outputPricePerMTok",
                "number": _clean_num(float(m_price.group(3))),
                "text": None,
                "source": OPENAI_MODELS_URL,
                "checkedAt": today,
            }
        )
    return {
        "vendorId": vid,
        "model": slug,
        "name": name,
        "efforts": efforts,
        "values": values,
    }


def parse_chatgpt_codex_contexts(chatgpt_html: str) -> dict[str, int]:
    txt = WS_RE.sub(" ", html.unescape(TAG_RE.sub(" ", chatgpt_html)))
    out: dict[str, int] = {}
    for m in re.finditer(
        r"(GPT-[0-9.]+\s+[A-Za-z]+)\s+uses a default context window of\s+([0-9,]+)\s+tokens in Codex",
        txt,
    ):
        out[m.group(1).strip()] = int(m.group(2).replace(",", ""))
    return out


def _parse_html_table(table_html: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, re.S):
        cells = [
            WS_RE.sub(" ", html.unescape(TAG_RE.sub(" ", c))).strip()
            for c in CELL_RE.findall(tr)
        ]
        if cells:
            rows.append(cells)
    return rows


def _parse_dollar(cell: str) -> int | float | None:
    if cell.strip() == "Free":
        return 0
    nums = re.findall(r"\$([0-9]+(?:\.[0-9]+)?)", cell)
    if not nums:
        return None
    return _clean_num(float(nums[-1]))


def parse_opencode_go_docs(oc_html: str, today: str) -> list[dict[str, Any]]:
    tables = [
        _parse_html_table(t)
        for t in re.findall(r"<table[^>]*>(.*?)</table>", oc_html, re.S)
    ]
    id_tables = [t for t in tables if t and t[0][:2] == ["Model", "Model ID"]]
    price_tables = [
        t for t in tables if t and t[0][:3] == ["Model", "Input", "Output"]
    ]
    if not id_tables or len(price_tables) < 3:
        return []

    name_to_id = {
        row[0]: row[1] for row in id_tables[0][1:] if len(row) >= 2 and row[0] and row[1]
    }

    def resolve_row_model(raw_name: str) -> tuple[str | None, str | None, bool]:
        if raw_name in name_to_id:
            return raw_name, name_to_id[raw_name], True
        m = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", raw_name)
        if not m:
            return None, None, False
        base, qual = m.group(1).strip(), m.group(2).strip()
        if base not in name_to_id:
            return None, None, False
        if qual.startswith(">") or qual.lower() == "off-peak":
            return base, name_to_id[base], False
        return base, name_to_id[base], True

    plans = ["go", "go-plus", "go-teams"]
    by_model: dict[str, dict[str, Any]] = {}
    for plan, pt in zip(plans, price_tables[:3]):
        for row in pt[1:]:
            if len(row) < 6:
                continue
            base, mid, keep = resolve_row_model(row[0])
            if not mid or not keep or not base:
                continue
            entry = by_model.setdefault(
                mid,
                {
                    "name": NAME_OVERRIDES.get(mid, base),
                    "input": _parse_dollar(row[1]),
                    "output": _parse_dollar(row[2]),
                    "cachedRead": _parse_dollar(row[3]),
                    "limits": {},
                },
            )
            lim = _parse_dollar(row[5])
            if lim is not None:
                entry["limits"][plan] = lim

    models: list[dict[str, Any]] = []
    for mid, data in by_model.items():
        vid = infer_maker(mid)
        values: list[dict[str, Any]] = []
        if data["input"] is not None:
            values.append(
                {
                    "id": f"{vid}/{mid}/inputPricePerMTok",
                    "code": "inputPricePerMTok",
                    "number": data["input"],
                    "text": None,
                    "source": OPENCODE_GO_URL,
                    "checkedAt": today,
                }
            )
        if data["output"] is not None:
            values.append(
                {
                    "id": f"{vid}/{mid}/outputPricePerMTok",
                    "code": "outputPricePerMTok",
                    "number": data["output"],
                    "text": None,
                    "source": OPENCODE_GO_URL,
                    "checkedAt": today,
                }
            )
        if data["cachedRead"] is not None:
            values.append(
                {
                    "id": f"{vid}/{mid}/cachedReadPricePerMTok",
                    "code": "cachedReadPricePerMTok",
                    "number": data["cachedRead"],
                    "text": None,
                    "source": OPENCODE_GO_URL,
                    "checkedAt": today,
                }
            )
        for plan in plans:
            if plan in data["limits"]:
                values.append(
                    {
                        "id": f"{vid}/{mid}/monthlyLimitUSD/{plan}",
                        "code": "monthlyLimitUSD",
                        "number": data["limits"][plan],
                        "text": plan,
                        "source": OPENCODE_GO_URL,
                        "checkedAt": today,
                    }
                )
        models.append(
            {
                "vendorId": vid,
                "model": mid,
                "name": data["name"],
                "efforts": list(KNOWN_EFFORTS.get(mid, [])),
                "values": values,
            }
        )
    return models


def merge_models(
    existing_models: list[dict[str, Any]],
    collected_batches: list[list[dict[str, Any]]],
    today: str,
    touch: bool = False,
) -> list[dict[str, Any]]:
    """Merge collected models with existing models, preserving checkedAt when unchanged."""
    existing_by_key = {
        (m["vendorId"], m["model"]): m for m in existing_models
    }
    existing_values: dict[str, dict[str, Any]] = {}
    for m in existing_models:
        for v in m["values"]:
            existing_values[v["id"]] = v

    merged: dict[tuple[str, str], dict[str, Any]] = {}

    # Seed from collected batches in priority order (1st-party makers first, OpenCode Go second).
    for batch in collected_batches:
        for m in batch:
            key = (m["vendorId"], m["model"])
            if key not in merged:
                merged[key] = {
                    "vendorId": m["vendorId"],
                    "model": m["model"],
                    "name": m["name"],
                    "efforts": list(m["efforts"]),
                    "values": {},
                }
            else:
                cur = merged[key]
                for ef in m["efforts"]:
                    if ef not in cur["efforts"]:
                        cur["efforts"].append(ef)
                cur["efforts"] = [e for e in EFFORT_ORDER if e in cur["efforts"]]
            for v in m["values"]:
                vid_str = v["id"]
                if vid_str in merged[key]["values"]:
                    continue
                new_v = dict(v)
                old_v = existing_values.get(vid_str)
                if (
                    not touch
                    and old_v is not None
                    and old_v.get("code") == new_v.get("code")
                    and old_v.get("number") == new_v.get("number")
                    and old_v.get("text") == new_v.get("text")
                    and old_v.get("source") == new_v.get("source")
                ):
                    new_v["checkedAt"] = old_v.get("checkedAt", today)
                merged[key]["values"][vid_str] = new_v

    # Preserve any hand-curated models or unmanaged values from existing_models.
    for key, old_m in existing_by_key.items():
        unmanaged_vals = [
            v for v in old_m["values"] if v["source"] not in MANAGED_SOURCES
        ]
        if key not in merged and unmanaged_vals:
            merged[key] = {
                "vendorId": old_m["vendorId"],
                "model": old_m["model"],
                "name": old_m["name"],
                "efforts": list(old_m["efforts"]),
                "values": {v["id"]: dict(v) for v in unmanaged_vals},
            }
        elif key in merged:
            for v in unmanaged_vals:
                merged[key]["values"].setdefault(v["id"], dict(v))

    out: list[dict[str, Any]] = []
    for key in sorted(merged):
        entry = merged[key]
        vals = [entry["values"][vid] for vid in sorted(entry["values"])]
        out.append(
            {
                "vendorId": entry["vendorId"],
                "model": entry["model"],
                "name": entry["name"],
                "efforts": entry["efforts"],
                "values": vals,
            }
        )
    return out


def main(argv: list[str] | None = None) -> None:
    args = argv if argv is not None else sys.argv[1:]
    touch = "--touch" in args
    today = date.today().isoformat()

    existing: dict[str, Any] = json.loads(FACTS_PATH.read_text(encoding="utf-8"))

    claude_overview = fetch_url(CLAUDE_OVERVIEW_MD_URL)
    claude_effort = fetch_url(CLAUDE_EFFORT_MD_URL)
    claude_models = parse_claude_docs(claude_overview, claude_effort, today)
    if not claude_models:
        sys.exit("Claude docs parser returned 0 models; aborting")

    oai_index = fetch_url(OPENAI_MODELS_URL)
    chatgpt_html = fetch_url(CHATGPT_MODELS_URL)
    codex_ctx = parse_chatgpt_codex_contexts(chatgpt_html)
    oai_slugs = discover_openai_flagship_slugs(oai_index)
    if not oai_slugs:
        sys.exit("OpenAI docs parser discovered 0 flagship models; aborting")
    openai_models = [
        parse_openai_model_page(
            slug, fetch_url(f"{OPENAI_MODELS_URL}/{slug}"), codex_ctx, today
        )
        for slug in oai_slugs
    ]

    oc_html = fetch_url(OPENCODE_GO_URL)
    opencode_models = parse_opencode_go_docs(oc_html, today)
    if not opencode_models:
        sys.exit("OpenCode Go docs parser returned 0 models; aborting")

    merged_models = merge_models(
        existing["models"],
        [claude_models, openai_models, opencode_models],
        today=today,
        touch=touch,
    )

    changed = touch or merged_models != existing["models"]
    new_date = today if changed else existing["date"]
    bundle = {
        "version": 1,
        "date": new_date,
        "models": merged_models,
    }
    FACTS_PATH.write_text(
        json.dumps(bundle, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(
        f"model-facts.json: {len(merged_models)} models "
        f"(claude={len(claude_models)}, openai={len(openai_models)}, "
        f"opencode-go={len(opencode_models)}, changed={changed})"
    )


if __name__ == "__main__":
    main()
