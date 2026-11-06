# Sources

Collectors that keep `model-facts.json` and `model-tiers.json` current. The
scheduled workflow (`.github/workflows/refresh.yml`) runs them weekly and opens
a pull request when they change anything; nothing is pushed to `main` directly.

## Automated

| Script | Feeds | Source |
|---|---|---|
| `fetch_artificial_analysis.py` | `model-tiers.json` | Artificial Analysis LLM leaderboard (`artificialanalysis.ai/leaderboards/models`) |
| `check_staleness.py` | report only | scans `checkedAt` fields in both JSON files |

`fetch_artificial_analysis.py` rewrites only entries whose evidence is
exclusively the AA Intelligence Index benchmark. Entries carrying vendor or
community evidence, or hand-written notes, are preserved. If no entry's tier,
rank, or score note changed and `factsDate` matches `model-facts.json`,
existing `checkedAt` and bundle `date` values are kept so scheduled runs do not
open no-op PRs. Pass `--touch` to force-bump `checkedAt` and `date` to today.

Tier bands (`FRONTIER_MIN = 50`, `MID_MIN = 25`) and the shared composite
competition rank (1..N across tracked setups) are provisional; adjust them in
the script and let the PR review settle the result.

Run locally:

```sh
python3 sources/fetch_artificial_analysis.py           # rewrites model-tiers.json on change
python3 sources/fetch_artificial_analysis.py --touch   # force-bump checkedAt and date
python3 sources/check_staleness.py --days 30           # prints stale checkedAt items
```

## Manual sources

Facts values are verified by hand against the maker's own documentation. The
`source` URL on each value records where it was checked. Per-maker starting
points:

- Anthropic Claude — `https://platform.claude.com/docs/en/models/overview`
- OpenAI — `https://developers.openai.com/api/docs/models`
- Google — `https://ai.google.dev/gemini-api/docs/models`
- xAI — `https://docs.x.ai/docs/models`
- Z.ai / Zhipu — `https://docs.z.ai/guides/llm/glm-4.6`
- Moonshot — `https://platform.moonshot.ai/docs`
- DeepSeek — `https://api-docs.deepseek.com/quick_start/pricing`
- Alibaba Qwen — `https://www.alibabacloud.com/help/en/model-studio/models`
- MiniMax — `https://platform.minimax.io/docs/guides/models-introduction`
- Xiaomi MiMo — `https://platform.xiaomimimo.com`
- StepFun — `https://platform.stepfun.com/docs`
- Tencent Hunyuan — `https://cloud.tencent.com/document/product/1729`
- Meituan LongCat — `https://longcat.chat`
- Meta — `https://ai.meta.com`
- OpenCode catalog (plan-qualified values) — `https://opencode.ai/docs/go/`

When a vendor page changes a value: update the number/text, set `checkedAt` to
the verification date, and open a PR. Value changes go through the same
review path as tier changes.
