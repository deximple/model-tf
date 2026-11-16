#!/usr/bin/env python3
"""Assertions for .github/workflows/selftest-repair.yml (live, against GitHub).

  cleanup       remove leftovers: selftest/repair-base, auto-repair/selftest/*
                branches and PRs, [selftest] alert issues
  after-repair  the agent's fix arrived as a verified review PR: right base,
                only sources/ + tests/, labelled, validate green on its head,
                the Vals parser on the PR head reads the live page again, the
                outcome was reported on the alert issue, and a second run was
                deduplicated instead of calling the agent again

Env: GITHUB_REPOSITORY, GH_TOKEN, REPAIR_STATUS, REPAIR_AGAIN_STATUS,
     ALERT_ISSUE. Exits 1 if any check fails.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from selftest_verify import Checks, gh_json, remote_branch_exists, run, wait_for_check  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BASE = "selftest/repair-base"
PR_BRANCH = f"auto-repair/{BASE}/tiers-vals"
LABEL = "refresh-alert-selftest"


def cleanup() -> None:
    prs = gh_json("pr", "list", "--state", "open", "--json", "number,headRefName") or []
    for pr in prs:
        if pr["headRefName"].startswith("auto-repair/selftest/"):
            run("gh", "pr", "close", str(pr["number"]), "--comment", "Self-test cleanup.", check=False)
    heads = run("git", "ls-remote", "--heads", "origin", check=False).splitlines()
    for line in heads:
        ref = line.split("\t", 1)[-1].removeprefix("refs/heads/")
        if ref == BASE or ref.startswith("auto-repair/selftest/"):
            run("git", "push", "-q", "origin", "--delete", ref, check=False)
    issues = gh_json("issue", "list", "--label", LABEL, "--state", "open", "--json", "number") or []
    for issue in issues:
        run("gh", "issue", "close", str(issue["number"]), "--comment", "Self-test cleanup.", check=False)
    print("auto-repair self-test sandbox cleaned up")


def after_repair() -> None:
    c = Checks("auto-repair — agent fixes the real Vals breakage")
    c.check("auto-repair verdict is 'fixed'", os.environ.get("REPAIR_STATUS") == "fixed", os.environ.get("REPAIR_STATUS", ""))
    prs = gh_json(
        "pr", "list", "--head", PR_BRANCH, "--state", "open",
        "--json", "number,baseRefName,headRefOid,files,labels,title,body",
    ) or []
    pr = prs[0] if len(prs) == 1 else None
    c.check("review PR opened", pr is not None, f"{len(prs)} open on {PR_BRANCH}")
    if pr is not None:
        files = sorted(f["path"] for f in pr["files"])
        c.check("PR targets the broken sandbox base", pr["baseRefName"] == BASE, pr["baseRefName"])
        c.check(
            "PR changes only sources/ and tests/",
            bool(files) and all(p.startswith(("sources/", "tests/")) for p in files),
            ", ".join(files),
        )
        c.check("PR labelled auto-repair", any(lb["name"] == "auto-repair" for lb in pr["labels"]))
        c.check(
            "PR body carries the workflow's own verification",
            "Verified by the workflow" in pr["body"] and "`tiers/vals`" in pr["body"],
        )
        c.check("validate check green on the PR head", wait_for_check(pr["headRefOid"]) == "success", pr["headRefOid"][:7])
        with tempfile.TemporaryDirectory() as tmp:
            wt = Path(tmp) / "wt"
            run("git", "fetch", "-q", "origin", f"+refs/heads/{PR_BRANCH}:refs/remotes/origin/{PR_BRANCH}")
            run("git", "worktree", "add", "-q", "--detach", str(wt), f"origin/{PR_BRANCH}")
            probe = (
                "import json, sys; sys.path.insert(0, 'sources'); import fetch_artificial_analysis as aa;"
                "rows, err = aa._safe_fetch_list('Vals AI', aa.parse_vals_benchmarks, aa.VALS_URL);"
                "facts = json.load(open('model-facts.json'));"
                "idx = aa.build_auto_name_index(facts['models']);"
                "m = [r for r in rows if aa.resolve_model(aa.parse_name(r['model_label'])[0], idx)];"
                "print(json.dumps({'rows': len(rows), 'matched': len(m), 'error': err, 'url': aa.VALS_URL}))"
            )
            proc = subprocess.run([sys.executable, "-c", probe], cwd=wt, capture_output=True, text=True)
            run("git", "worktree", "remove", "--force", str(wt), check=False)
        try:
            res = json.loads(proc.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            res = {"rows": 0, "matched": 0, "error": proc.stderr.strip()[-300:]}
        c.check(
            "fixed parser reads the live Vals page again",
            res.get("rows", 0) >= 10 and res.get("matched", 0) >= 10 and not res.get("error"),
            json.dumps(res),
        )
    issue_no = os.environ.get("ALERT_ISSUE", "")
    if issue_no:
        issue = gh_json("issue", "view", issue_no, "--json", "comments")
        bodies = [cm["body"] for cm in issue["comments"]]
        c.check(
            "outcome reported on the alert issue (with dedupe marker)",
            any("auto-repair-fp:" in b and "proposed a verified fix" in b for b in bodies),
            f"{len(bodies)} comment(s)",
        )
    c.check(
        "second run deduplicated (no second agent call)",
        os.environ.get("REPAIR_AGAIN_STATUS") in ("already-proposed", "already-attempted"),
        os.environ.get("REPAIR_AGAIN_STATUS", ""),
    )
    c.check("broken base branch still exists (fix not pushed to it)", remote_branch_exists(BASE))
    c.finish()


STAGES = {"cleanup": cleanup, "after-repair": after_repair}

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in STAGES:
        sys.exit(f"usage: {sys.argv[0]} {{{'|'.join(STAGES)}}}")
    STAGES[sys.argv[1]]()
