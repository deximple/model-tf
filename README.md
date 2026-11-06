# model-tf

Model facts and evaluation tiers for AI coding models — the shared data layer
behind [UsageSwitch](https://github.com/deximple/usageswitch)'s Suggestion
feature (and usable by other tools, e.g. POSA).

Three layers exist across the ecosystem; this repo owns the first two:

| Layer | Content | Where |
|---|---|---|
| Facts | Model specs, capabilities, prices, limits — each with source URL and check date | `model-facts.json` (this repo) |
| Tiers | Evaluation tier and relative rank per model × effort, with evidence | `model-tiers.json` (this repo) |
| State | Per-account window usage, deltas, resets | stays local in each app |

## Files

- `model-facts.json` — `version: 1`, same shape as POSA's facts bundle.
- `model-tiers.json` — `version: 1`; see `schema/` for both formats.
- `schema/` — JSON Schemas; CI validates every change.
- `sources/` — automated collectors for vendor docs and Artificial Analysis.
- `scripts/validate.py` — schema + cross-file checks (run locally too).
- `tests/` — unit tests for schemas, validator, collectors, and staleness checks.

## Conventions

- **`vendorId` is the model maker, not a harness or subscription.** A Claude
  model reached through Claude Code, Cursor, or Devin is still
  `claude/claude-opus-5-5`. Harness → model mapping lives in each app, not
  here. Maker ids follow models.dev provider naming (`openai`, `google`,
  `xai`, `zhipuai`, `moonshotai`, `deepseek`, `alibaba`, `minimax`, `xiaomi`,
  `stepfun`, `tencent`, `meta`, `meituan`, `opencode` for unattributed
  community models).
- **Model key** = `vendorId/model`. **Value id** = `vendorId/model/code`,
  optionally suffixed with a plan qualifier (e.g. `monthlyLimitUSD/go`).
- Every fact value carries `source` (`https://` URL) and `checkedAt` (date).
  Unknown `code` values are ignored by consumers.
- Facts hold specs and prices only — **no rankings, recommendations, or
  strengths**, and no Korean or sentence text in `value.text`.
- Tier entries are **model × effort**. `effort: "default"` is used when the
  model's `efforts` in `model-facts.json` is empty (`[]`); otherwise `effort`
  must be a member of the model's `efforts`.
- `rank` is per task axis (`simpleCoding` from AA Terminal-Bench 4.0 +
  FrontierCode 2.0 composite; `reasoning` from AA Intelligence Index), 1 = best.
- Evidence `kind`: `benchmark` (independent evals), `vendor` (maker claims),
  `community` (forums/social). Community evidence alone cannot set a tier.

## Updating

- `refresh.yml` runs weekly (and on demand):
  1. `sources/fetch_vendor_facts.py` refreshes `model-facts.json` from official
     Anthropic, OpenAI/ChatGPT, and OpenCode Go documentation (including new
     models and price/limit changes).
  2. `sources/fetch_artificial_analysis.py` refreshes `model-tiers.json` from
     the Artificial Analysis leaderboard (splitting `simpleCoding` vs
     `reasoning` ranks and auto-matching new models in `model-facts.json`).
  3. `scripts/validate.py` and `tests/` gate the result before opening a PR on
     `refresh/model-data` with a human-review diff summary.
- `validate.yml` gates every push and PR.

## Compatibility

`model-facts.json` keeps the POSA `version: 1` field shape (`version`,
`date`, `models[].vendorId/model/name/efforts/values[]`). The `vendorId`
semantics differ from POSA's current bundle (maker here vs. harness there);
whether and how POSA consumes this repo is decided in POSA's own
implementation.
