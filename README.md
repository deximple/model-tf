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
- `sources/` — collectors run by the weekly refresh workflow.
- `scripts/validate.py` — schema + cross-file checks (run locally too).

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
- Every fact value carries `source` (URL) and `checkedAt` (date). Unknown
  `code` values are ignored by consumers.
- Facts hold specs and prices only — **no rankings, recommendations, or
  strengths**.
- Tier entries are **model × effort**. `effort: "default"` marks models with a
  single configuration. `rank` is per task axis, 1 = best.
- Evidence `kind`: `benchmark` (independent evals), `vendor` (maker claims),
  `community` (forums/social). Community evidence alone cannot set a tier.

## Updating

- `refresh.yml` runs weekly (and on demand): it rewrites AA-benchmark-sourced
  tier entries and opens a PR on `refresh/model-data`. **Tier changes are
  merged by human review** — the PR is the approval step.
- Fact changes are manual: verify against the maker's docs, update the value
  and `checkedAt`, open a PR (see `sources/README.md` for the source list).
- `validate.yml` gates every PR: JSON Schema conformance, unique keys,
  `tiers.key ⊆ facts`, `factsDate == facts.date`, and the community-evidence
  rule.

## Compatibility

`model-facts.json` keeps the POSA `version: 1` field shape (`version`,
`date`, `models[].vendorId/model/name/efforts/values[]`). The `vendorId`
semantics differ from POSA's current bundle (maker here vs. harness there);
whether and how POSA consumes this repo is decided in POSA's own
implementation.
