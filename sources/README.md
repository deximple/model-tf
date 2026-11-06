# Sources

Collectors that keep `model-facts.json` and `model-tiers.json` current. The
scheduled workflow (`.github/workflows/refresh.yml`) runs them weekly and opens
a pull request when they change anything; nothing is pushed to `main` directly.

## Automated

| Script | Feeds | Source |
|---|---|---|
| `fetch_vendor_facts.py` | `model-facts.json` | Official docs: `platform.claude.com`, `developers.openai.com`, `learn.chatgpt.com`, `opencode.ai/docs/go/` |
| `fetch_artificial_analysis.py` | `model-tiers.json` | Artificial Analysis LLM leaderboard (`artificialanalysis.ai/leaderboards/models`) |
| `check_staleness.py` | PR summary + staleness report | compares against `HEAD` (`--diff-head`) and scans `checkedAt` fields |

### `fetch_vendor_facts.py`

- Parses Anthropic's `models/overview.md` and `build-with-claude/effort.md`,
  OpenAI's `api/docs/models` (+ per-model pages) and `learn.chatgpt.com/docs/models`,
  and OpenCode Go's `docs/go/` tables.
- Discovers newly added models automatically and maps each model to its maker
  `vendorId` (`MAKER_BY_MODEL` + prefix rules).
- Preserves any hand-curated models or values whose `source` URL is outside the
  managed vendor documentation URLs.
- Preserves existing `checkedAt` and bundle `date` when values have not changed
  (pass `--touch` to force-bump).

### `fetch_artificial_analysis.py`

- Extracts the embedded Next.js RSC model payload (`initialModels`, falling back
  to the HTML table if needed).
- Computes separate competition ranks (`1..N`) for `simpleCoding`
  (`(Terminal-Bench 4.0 + FrontierCode 2.0) / 2`) and `reasoning`
  (`Intelligence Index`).
- Matches models via `NAME_MAP` first, then automatic normalized name/id lookup
  against `model-facts.json` so newly added models are picked up automatically.
- Rewrites only AA-benchmark-sourced entries; entries with vendor/community
  evidence are preserved. Keeps `checkedAt` and `date` unchanged when no tier,
  rank, or score note changed.

Run locally:

```sh
python3 sources/fetch_vendor_facts.py                  # updates model-facts.json on change
python3 sources/fetch_artificial_analysis.py           # updates model-tiers.json on change
python3 sources/check_staleness.py --diff-head         # prints PR diff summary + stale items
```

## Additional manual sources

For models or values from documentation pages not yet covered by
`fetch_vendor_facts.py`, verify against the maker's own docs, set `source` and
`checkedAt`, and open a PR (`fetch_vendor_facts.py` preserves values from
unmanaged `source` URLs):

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
