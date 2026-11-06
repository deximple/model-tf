# Sources

Collectors that keep `model-facts.json` and `model-tiers.json` current. The
scheduled workflow (`.github/workflows/refresh.yml`) runs them weekly,
validates the output, and commits directly to `main` when safe (or opens a PR
when a single-source tier flip requires human review).

## Automated Collectors

| Script | Feeds | Sources |
|---|---|---|
| `fetch_vendor_facts.py` | `model-facts.json` | Official docs: Anthropic (`platform.claude.com`), OpenAI/Codex (`developers.openai.com`, `learn.chatgpt.com`), Google Gemini (`ai.google.dev`), xAI Grok (`docs.x.ai`), Z.ai GLM (`docs.z.ai`), OpenCode Go (`opencode.ai/docs/go/`) |
| `fetch_artificial_analysis.py` | `model-tiers.json` | 4 benchmark leaderboards: Artificial Analysis (`artificialanalysis.ai`), Terminal-Bench (`tbench.ai`), Vals AI (`vals.ai`), Scale AI SEAL (`scale.com/leaderboard`) |
| `check_staleness.py` | Summary + auto-merge gate + staleness report | compares against `HEAD` (`--diff-head`), evaluates single- vs multi-source tier shifts (`--gate-out`), and scans `checkedAt` fields |

### `fetch_vendor_facts.py`

- Parses 6 official documentation sources in priority order (1st-party model
  makers first, OpenCode Go catalog last for `monthlyLimitUSD` and remaining
  catalog models).
- Discovers newly added models automatically and maps each model to its maker
  `vendorId` (`MAKER_BY_MODEL` + prefix rules).
- Preserves any hand-curated models or values whose `source` URL is outside the
  managed vendor documentation URLs.
- Preserves existing `checkedAt` and bundle `date` when values have not changed
  (pass `--touch` to force-bump).

### `fetch_artificial_analysis.py`

- Combines 4 independent benchmark leaderboards into `model-tiers.json`:
  1. **Artificial Analysis** — RSC JSON `initialModels` (`Intelligence Index` + `Terminal-Bench 4.0` + `FrontierCode 2.0`)
  2. **Stanford / Laude Terminal-Bench** — agentic terminal coding accuracy per `(model, reasoning_effort)`
  3. **Vals AI** — enterprise & coding `Vals Index`
  4. **Scale AI SEAL** — `SWE Atlas`, `MCP Atlas`, `HLE Diamond`, `MultiChallenge`, `Humanity's Sixth Sense`
- Computes separate competition ranks (`1..N`) for `simpleCoding` and
  `reasoning`.
- Matches models via `NAME_MAP` first, then automatic normalized name/id lookup
  against `model-facts.json`.

Run locally:

```sh
python3 sources/fetch_vendor_facts.py                  # updates model-facts.json on change
python3 sources/fetch_artificial_analysis.py           # updates model-tiers.json on change
python3 sources/check_staleness.py --diff-head         # prints diff summary + stale items
```
