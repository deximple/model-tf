# model-tf

**English** · [한국어](./README.ko.md)

Model facts and evaluation tiers for AI coding models — the shared data layer
behind [UsageSwitch](https://github.com/deximple/usageswitch)'s Suggestion
feature (and usable by other tools, e.g. POSA).

Three layers exist across the ecosystem; this repo owns the first two:

| Layer | Content | Where |
|---|---|---|
| Facts | Model specs, capabilities, prices, limits — each with source URL and check date | `model-facts.json` (this repo) |
| Tiers | Evaluation tier and relative rank per model × effort, with multi-benchmark evidence | `model-tiers.json` (this repo) |
| State | Per-account window usage, deltas, resets | stays local in each app |

## Files

- `model-facts.json` — `version: 1`, same shape as POSA's facts bundle (49 models across 15 makers).
- `model-tiers.json` — `version: 1`, 71 model × effort entries backed by up to 4 independent benchmark sources.
- `schema/` — JSON Schemas; CI validates every change.
- `sources/` — automated collectors for official vendor docs and 4 benchmark leaderboards.
- `scripts/validate.py` — schema + cross-file checks (run locally too).
- `tests/` — unit tests for schemas, validator, collectors, source-failure handling, and auto-merge gating.

## Conventions

- **`vendorId` is the model maker, not a harness or subscription.** A Claude
  model reached through Claude Code, Cursor, or Devin is still
  `claude/claude-opus-5-5`. Harness → model mapping lives in each app, not
  here. Maker ids follow models.dev provider naming (`claude`, `openai`,
  `google`, `xai`, `zhipuai`, `moonshotai`, `deepseek`, `alibaba`, `minimax`,
  `xiaomi`, `stepfun`, `tencent`, `meta`, `meituan`, `opencode` for
  unattributed community models).
- **Model key** = `vendorId/model`. **Value id** = `vendorId/model/code`,
  optionally suffixed with a plan qualifier (e.g. `monthlyLimitUSD/go`).
- Every fact value carries `source` (`https://` URL) and `checkedAt` (date).
  Unknown `code` values are ignored by consumers.
- Facts hold specs and prices only — **no rankings, recommendations, or
  strengths**, and no Korean or sentence text in `value.text`.
- Tier entries are **model × effort**. `effort: "default"` is used when the
  model's `efforts` in `model-facts.json` is empty (`[]`); otherwise `effort`
  must be a member of the model's `efforts`.
- `rank` is per task axis (`simpleCoding` vs `reasoning`), 1 = best, computed
  across tracked setups from combined benchmark evidence:
  1. **Artificial Analysis** (`artificialanalysis.ai/leaderboards/models`) — Intelligence Index + Terminal-Bench 4.0 & FrontierCode 2.0
  2. **Stanford / Laude Terminal-Bench** (`tbench.ai/leaderboard`) — agentic terminal coding accuracy per (model, effort)
  3. **Vals AI** (`vals.ai/benchmarks/vals_index`) — enterprise & coding Vals Index
  4. **Scale AI SEAL** (`scale.com/leaderboard`) — SWE Atlas, MCP Atlas, HLE Diamond, MultiChallenge, Humanity's Sixth Sense
- Evidence `kind`: `benchmark` (independent evals), `vendor` (maker claims),
  `community` (forums/social). Community evidence alone cannot set a tier.

## Hands-Free Updating (`refresh.yml`)

- Runs **daily at 21:37 UTC (06:37 KST)** and via `workflow_dispatch`. The
  minute is deliberately off the hour: GitHub delays (and under load drops)
  scheduled runs queued at `:00`.
  1. `sources/fetch_vendor_facts.py` refreshes `model-facts.json` from official
     Anthropic, OpenAI/ChatGPT, Google Gemini, xAI Grok, Z.ai GLM, and OpenCode
     Go documentation.
  2. `sources/fetch_artificial_analysis.py` refreshes `model-tiers.json` from
     Artificial Analysis, Terminal-Bench, Vals AI, and Scale AI SEAL.
  3. `scripts/validate.py` and `tests/` gate the result.
  4. **Conditional apply (`mode=conditional`, default):**
     - Fact updates, new models, rank updates, and tier changes confirmed by
       **≥2 independent benchmark sources** are pushed **directly to `main`**.
     - If an existing entry changes `tier` based on a **single** benchmark
       source, only that tier value is held back: everything else still lands
       on `main`, and a review PR on `refresh/model-data` contains just the
       held tier flips. A pending PR therefore never freezes the data. The PR
       is closed automatically once the shift disappears or gets confirmed.
     - `mode=direct` commits everything to `main`; `mode=pr` puts everything
       in the review PR.
- **Source failures never stop the refresh.** Every fetch retries transient
  errors (5xx/429/timeouts) with backoff. If one vendor page or leaderboard
  still fails, or parses to zero rows, its previous values and evidence are
  kept (with their old `checkedAt`) and every healthy source still refreshes.
- **Never fails silently.** A failed step, a degraded source, or a PR that
  cannot be opened creates (or updates) one issue labelled `refresh-alert`.
  The next fully healthy run closes it automatically.
- **CI on bot commits.** Pushes and PRs made with `GITHUB_TOKEN` do not trigger
  `push`/`pull_request` workflows, so the refresh explicitly dispatches
  `validate.yml` for the commits and PRs it creates.
- **Schedule keepalive.** GitHub disables schedules in public repos after 60
  days without activity. Three independent guards: the **45-day `--touch`**
  refresh (bumps `checkedAt` after live verification), a **heartbeat commit**
  to `.github/refresh-heartbeat` whenever `main` has been idle for 50 days
  (even if every source is failing), and an API re-enable on every run.
- One-time setting for the review-PR path: *Settings → Actions → General →
  Workflow permissions → "Allow GitHub Actions to create and approve pull
  requests"*. If it is off, the PR step fails and the alert issue says so;
  all `main` updates still land.

## Auto-Repair with Claude Code (`auto-repair.yml`)

When a refresh finds a source failing in a way a code change can fix, Claude
Code proposes the fix as a review PR. That covers a parser that returns nothing
and a page that has moved, but not outages, throttling, or TLS errors.

- **The agent never holds write access.** The `propose` job runs
  [Claude Code Action](https://github.com/anthropics/claude-code-action) with
  Claude Opus 5.5 under a read-only token and no git credentials. Its tools are
  limited to reading and editing files plus running `python3`.
- **Deterministic checks decide, not the agent.** A proposal is kept only if:
  - it changes files under `sources/` and `tests/` only;
  - the unit tests and `validate.py` pass;
  - a fresh collector run shows every target source `ok` again;
  - the patch passes a secret scan.
- **Publishing runs no proposed code.** The `publish` job applies the checked
  patch and opens a PR labelled `auto-repair`. Nothing is merged automatically.
- **Each failure gets one attempt.** The outcome is commented on the
  `refresh-alert` issue, and an open PR or an earlier attempt at the same
  failure skips the agent.
- **Setup:** add the repository secret `ANTHROPIC_API_KEY` (from the
  [Claude Console](https://platform.claude.com)) or `CLAUDE_CODE_OAUTH_TOKEN`
  (from `claude setup-token`) under *Settings → Secrets and variables →
  Actions*. Without either secret the job skips with a notice.
- **Manual run:** *Actions → auto-repair → Run workflow*.
- **Live test:** *Actions → selftest-repair → Run workflow*. It recreates the
  2026-11-16 Vals breakage on a throwaway branch and checks that the agent's
  fix arrives as a verified PR. Each run costs one agent call.

## Self-Test (`selftest.yml`)

Run **Actions → selftest → Run workflow** after changing `refresh.yml` or the
repository's Actions settings. It calls the production `refresh.yml` three
times against a throwaway `selftest/base` branch (never `main`):

1. Simulated outage of `docs.x.ai` and `scale.com` plus an injected
   single-source tier flip. It checks that the held commit lands on the base
   branch, the review PR contains only the tier flip, the failed sources'
   data is carried forward, `validate` goes green on both bot-made heads,
   and an alert issue is opened.
2. The same scenario again. It checks that no new commit is made, the PR is
   unchanged, and the alert is not re-posted.
3. A run against the real sources in `mode=direct`, which makes the result
   independent of today's live data, with both idle thresholds set to 0. It
   checks that the review PR is closed as superseded, the alert is closed as
   recovered, the `--touch` re-verification bumps every `checkedAt`, and a
   heartbeat commit is pushed. If a real source is down at that moment, the alert correctly stays
   open and the self-test fails.

The branches, PRs, issues, and label it creates are deleted at the end. It
takes about 5 minutes.

## Compatibility

`model-facts.json` keeps the POSA `version: 1` field shape (`version`,
`date`, `models[].vendorId/model/name/efforts/values[]`). The `vendorId`
semantics differ from POSA's current bundle (maker here vs. harness there);
whether and how POSA consumes this repo is decided in POSA's own
implementation.
