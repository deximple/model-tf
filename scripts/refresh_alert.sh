#!/usr/bin/env bash
# Report refresh health and open / update / close the "refresh-alert" issue.
#
# Called by .github/workflows/refresh.yml with `if: always()`. Requires `gh`
# (authenticated via GH_TOKEN, repository via GH_REPO) and `jq`.
#
# Environment:
#   JOB_STATUS   job.status of the refresh job (success | failure | cancelled)
#   PR_OUTCOME   outcome of the review-PR step (success | failure | skipped | "")
#   RUN_URL      link to the workflow run
#   STATUS_DIR   directory holding status-facts.json / status-tiers.json
#   GITHUB_STEP_SUMMARY (optional) markdown summary file
#
# Exit code is 0 unless a gh call itself fails.
set -euo pipefail

label="refresh-alert"
status_dir="${STATUS_DIR:-${RUNNER_TEMP:-.}}"
summary="${GITHUB_STEP_SUMMARY:-/dev/null}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
problems="$work/problems.md"
: > "$problems"

# 1. Per-source health table (always shown in the run summary).
{
  echo "## Source health"
  echo
  echo "| collector | source | status |"
  echo "|---|---|---|"
} >> "$summary"
found_status=false
for name in status-facts.json status-tiers.json; do
  f="$status_dir/$name"
  if [ ! -f "$f" ]; then
    continue
  fi
  found_status=true
  jq -r '.collector as $c | .sources | to_entries[] | "| \($c) | \(.key) | \(.value | gsub("[\\r\\n]+"; " ") | gsub("\\|"; "/")) |"' "$f" >> "$summary"
  jq -r '.collector as $c | .sources | to_entries[] | select(.value != "ok") | "- `\($c)/\(.key)`: \(.value | gsub("[\\r\\n]+"; " "))"' "$f" >> "$problems"
done
if [ "$found_status" = false ]; then
  echo "| - | - | no status files (collectors did not run) |" >> "$summary"
fi
echo >> "$summary"

# 2. Job-level problems.
if [ "${JOB_STATUS:-success}" != "success" ]; then
  echo "- Refresh job status: **${JOB_STATUS}** — a step failed, nothing was committed. See the run log." >> "$problems"
fi
if [ "${PR_OUTCOME:-}" = "failure" ]; then
  echo "- Opening the tier review PR failed. If the log says \"GitHub Actions is not permitted to create or approve pull requests\", enable it under Settings → Actions → General → Workflow permissions." >> "$problems"
fi

gh label create "$label" --color B60205 --description "Automated data refresh needs attention" --force >/dev/null 2>&1 || true
# Look up the open alert; retry once. If GitHub cannot answer, fail this step
# rather than guess "none open" and file a duplicate issue.
lookup() { gh issue list --label "$label" --state open --json number --jq '.[0].number // empty'; }
if ! existing="$(lookup)"; then
  sleep "${ALERT_RETRY_DELAY:-5}"
  if ! existing="$(lookup)"; then
    echo "::error::could not query open refresh-alert issues; skipping issue update to avoid duplicates"
    exit 1
  fi
fi

# 3. Healthy run: close a lingering alert.
if [ ! -s "$problems" ]; then
  echo "All sources healthy."
  if [ -n "$existing" ]; then
    gh issue close "$existing" --comment "Recovered: ${RUN_URL:-this run} completed with every source healthy."
  fi
  exit 0
fi

# 4. Problems: annotate the run, then open or update the single alert issue.
while IFS= read -r line; do
  msg="${line#- }"
  msg="${msg//'%'/'%25'}"
  echo "::warning::${msg}"
done < "$problems"
{ echo "## Refresh health problems"; cat "$problems"; echo; } >> "$summary"

# Fingerprint = which sources/steps fail (not the error text), so a long
# outage yields one comment instead of one per day.
fp="$(sed -E 's/: .*//' "$problems" | sort | sha256sum | cut -c1-16)"
body="$work/alert.md"
{
  echo "Run: ${RUN_URL:-unknown}"
  echo
  cat "$problems"
  echo
  echo "Failed sources keep their previous values (old \`checkedAt\`) until they recover; healthy sources were still refreshed. This issue closes automatically on the next fully healthy run."
  echo
  echo "To debug locally: \`python3 sources/fetch_vendor_facts.py --status-out /tmp/facts.json\` and \`python3 sources/fetch_artificial_analysis.py --status-out /tmp/tiers.json\`."
  echo
  echo "<!-- refresh-alert-fp:${fp} -->"
} > "$body"

if [ -z "$existing" ]; then
  gh issue create --title "refresh: automated data refresh needs attention" --label "$label" --body-file "$body"
  exit 0
fi
last="$(gh issue view "$existing" --json body,comments \
  --jq '([.body] + [.comments[].body]) | map(select(test("refresh-alert-fp:"))) | last // ""')"
if printf '%s' "$last" | grep -q "refresh-alert-fp:${fp}"; then
  echo "Alert #${existing} already describes these problems; not commenting again."
else
  gh issue comment "$existing" --body-file "$body"
fi
