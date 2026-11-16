#!/usr/bin/env python3
"""Assertions for .github/workflows/selftest.yml (live, against GitHub).

selftest.yml runs the production refresh.yml three times against a throwaway
``selftest/base`` branch and calls this script after each pass:

  cleanup      remove leftovers (branches, PRs, alert issues, label)
  after-pass1  simulated outage (docs.x.ai, scale.com) + injected single-source
               tier flip: base got the held commit, review PR holds only the
               tier flip, outage data carried forward, alert issue opened,
               validate checks ran green on both bot-made heads
  after-pass2  same scenario again: no churn commit, PR content unchanged,
               alert not re-posted (fingerprint dedupe)
  after-pass3  healthy run: review PR auto-closed + branch deleted, alert
               auto-closed, heartbeat commit pushed, sandbox data valid

Needs: git (full clone), gh (GH_TOKEN), GITHUB_SHA (commit the sandbox was
seeded from), GITHUB_REPOSITORY. Writes stage outputs to GITHUB_OUTPUT and a
check table to GITHUB_STEP_SUMMARY. Exits 1 if any check fails.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
BASE = os.environ.get("SELFTEST_BASE", "selftest/base")
PR_BRANCH = f"{BASE}-review"
LABEL = "refresh-alert-selftest"
BOT = "github-actions[bot]"
XAI_SOURCE = "https://docs.x.ai/docs/models"
SCALE_SOURCE = "https://scale.com/leaderboard"


def run(*args: str, check: bool = True) -> str:
    proc = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def gh_json(*args: str) -> Any:
    out = run("gh", *args)
    return json.loads(out) if out else None


def git_json(ref: str, path: str) -> dict[str, Any] | None:
    proc = subprocess.run(["git", "show", f"{ref}:{path}"], cwd=ROOT, capture_output=True, text=True)
    return json.loads(proc.stdout) if proc.returncode == 0 else None


def remote_branch_exists(branch: str) -> bool:
    return bool(run("git", "ls-remote", "--heads", "origin", branch, check=False))


def fetch() -> None:
    for branch in (BASE, PR_BRANCH):
        if remote_branch_exists(branch):
            run("git", "fetch", "-q", "--force", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}")


def tier_diffs(a: dict[str, Any], b: dict[str, Any]) -> list[tuple[str, str, str]]:
    """(key, effort, field) differences between two model-tiers documents."""
    out: list[tuple[str, str, str]] = []
    for k in sorted(set(a) | set(b)):
        if k != "entries" and a.get(k) != b.get(k):
            out.append(("<top>", "", k))
    ea = {(e["key"], e["effort"]): e for e in a.get("entries", [])}
    eb = {(e["key"], e["effort"]): e for e in b.get("entries", [])}
    for ident in sorted(set(ea) | set(eb)):
        if ident not in eb:
            out.append((*ident, "removed"))
            continue
        if ident not in ea:
            out.append((*ident, "added"))
            continue
        x, y = ea[ident], eb[ident]
        for field in ("tier", "rank"):
            if x[field] != y[field]:
                out.append((*ident, field))
        if x["evidence"] != y["evidence"]:
            strip = lambda ev: [{k: v for k, v in e.items() if k != "checkedAt"} for e in ev]  # noqa: E731
            same_but_dates = strip(x["evidence"]) == strip(y["evidence"])
            out.append((*ident, "evidence.checkedAt" if same_but_dates else "evidence"))
    return out


def wait_for_check(sha: str, name: str = "validate", timeout_s: int = 480) -> str:
    """Poll check runs on ``sha`` until ``name`` completes; return its conclusion."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        runs = gh_json(
            "api", f"repos/{os.environ['GITHUB_REPOSITORY']}/commits/{sha}/check-runs?check_name={name}"
        )["check_runs"]
        if runs and all(r["status"] == "completed" for r in runs):
            conclusions = {r["conclusion"] for r in runs}
            return "success" if conclusions == {"success"} else ",".join(sorted(conclusions))
        time.sleep(15)
    return "timed out"


class Checks:
    def __init__(self, stage: str) -> None:
        self.stage = stage
        self.rows: list[tuple[bool, str, str]] = []

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.rows.append((bool(ok), name, detail))
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
        return bool(ok)

    def finish(self, outputs: dict[str, str] | None = None) -> None:
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as fh:
                fh.write(f"## Self-test {self.stage}\n\n| result | check | detail |\n|---|---|---|\n")
                for ok, name, detail in self.rows:
                    fh.write(f"| {'✅' if ok else '❌'} | {name} | {detail.replace('|', '/')} |\n")
                fh.write("\n")
        out_path = os.environ.get("GITHUB_OUTPUT")
        if out_path and outputs:
            with open(out_path, "a", encoding="utf-8") as fh:
                for k, v in outputs.items():
                    fh.write(f"{k}={v}\n")
        failed = [name for ok, name, _ in self.rows if not ok]
        if failed:
            sys.exit(f"{len(failed)} self-test check(s) failed: {failed}")


def open_prs() -> list[dict[str, Any]]:
    return gh_json(
        "pr", "list", "--head", PR_BRANCH, "--state", "open",
        "--json", "number,baseRefName,headRefOid,files,title",
    ) or []


def alert_issues(state: str) -> list[dict[str, Any]]:
    return gh_json(
        "issue", "list", "--label", LABEL, "--state", state,
        "--json", "number,body,comments,state,title",
    ) or []


def cleanup() -> None:
    for pr in open_prs():
        run("gh", "pr", "close", str(pr["number"]), "--comment", "Self-test cleanup.", check=False)
    for branch in (PR_BRANCH, BASE):
        if remote_branch_exists(branch):
            run("git", "push", "-q", "origin", "--delete", branch, check=False)
    for issue in alert_issues("open"):
        run("gh", "issue", "close", str(issue["number"]), "--comment", "Self-test cleanup.", check=False)
    run("gh", "label", "delete", LABEL, "--yes", check=False)
    print("self-test sandbox cleaned up")


def after_pass1() -> None:
    c = Checks("pass 1 — outage + single-source tier flip")
    origin = os.environ["GITHUB_SHA"]
    fetch()
    base_ref = f"origin/{BASE}"
    base_sha = run("git", "rev-parse", base_ref)
    author = run("git", "log", "-1", "--format=%an", base_ref)
    c.check(
        "held refresh committed to sandbox base by the bot",
        base_sha != origin and author == BOT
        and subprocess.run(["git", "merge-base", "--is-ancestor", origin, base_ref], cwd=ROOT).returncode == 0,
        f"{base_sha[:7]} by {author}",
    )

    prs = open_prs()
    pr = prs[0] if len(prs) == 1 else None
    c.check("review PR opened by the workflow (Actions may create PRs)", pr is not None, f"{len(prs)} open")
    outputs: dict[str, str] = {"base_sha": base_sha}
    if pr is None:
        c.finish(outputs)
        return
    outputs["pr_number"] = str(pr["number"])
    c.check("review PR targets the sandbox base", pr["baseRefName"] == BASE, pr["baseRefName"])
    files = sorted(f["path"] for f in pr["files"])
    c.check("review PR changes only model-tiers.json", files == ["model-tiers.json"], ", ".join(files))

    base_tiers = git_json(base_ref, "model-tiers.json") or {}
    pr_tiers = git_json(f"origin/{PR_BRANCH}", "model-tiers.json") or {}
    origin_tiers = git_json(origin, "model-tiers.json") or {}
    diffs = tier_diffs(base_tiers, pr_tiers)
    fields = {d[2] for d in diffs if d[0] != "<top>"}
    top = {d[2] for d in diffs if d[0] == "<top>"}
    flipped = sorted({(d[0], d[1]) for d in diffs if d[2] == "tier"})
    c.check(
        "PR diff is exactly the held tier flip",
        bool(flipped) and fields <= {"tier", "evidence.checkedAt"} and top <= {"date"},
        f"flipped={flipped} fields={sorted(fields)} top={sorted(top)}",
    )
    origin_by = {(e["key"], e["effort"]): e for e in origin_tiers.get("entries", [])}
    base_by = {(e["key"], e["effort"]): e for e in base_tiers.get("entries", [])}
    c.check(
        "flipped entry kept its previous tier on base",
        all(base_by[i]["tier"] == origin_by[i]["tier"] for i in flipped if i in origin_by),
    )
    outputs["pr_tiers_blob"] = run("git", "rev-parse", f"origin/{PR_BRANCH}:model-tiers.json")
    outputs["flipped"] = json.dumps(flipped)

    origin_facts = git_json(origin, "model-facts.json") or {"models": []}
    base_facts = git_json(base_ref, "model-facts.json") or {"models": []}
    xai_origin = {v["id"]: v for m in origin_facts["models"] for v in m["values"] if v["source"] == XAI_SOURCE}
    xai_base = {v["id"]: v for m in base_facts["models"] for v in m["values"] if v["source"] == XAI_SOURCE}
    c.check(
        "failed vendor (xAI) values carried forward unchanged",
        bool(xai_origin) and xai_origin == xai_base,
        f"{len(xai_origin)} values",
    )
    scale_kept = scale_total = 0
    for ident, entry in origin_by.items():
        if tuple(ident) in {tuple(f) for f in flipped} or ident not in base_by:
            continue
        old_ev = [e for e in entry["evidence"] if e["source"] == SCALE_SOURCE]
        if not old_ev:
            continue
        scale_total += 1
        if old_ev[0] in base_by[ident]["evidence"]:
            scale_kept += 1
    c.check(
        "failed leaderboard (Scale) evidence carried forward",
        scale_total > 0 and scale_kept == scale_total,
        f"{scale_kept}/{scale_total} entries",
    )

    issues = alert_issues("open")
    issue = issues[0] if len(issues) == 1 else None
    c.check("alert issue opened", issue is not None, f"{len(issues)} open")
    if issue is not None:
        outputs["issue_number"] = str(issue["number"])
        c.check(
            "alert names both simulated outages",
            "`facts/xai`" in issue["body"] and "`tiers/scale`" in issue["body"],
        )
        c.check("alert has no follow-up comments yet", len(issue["comments"]) == 0, str(len(issue["comments"])))

    c.check("validate check green on bot commit (base)", wait_for_check(base_sha) == "success", base_sha[:7])
    c.check("validate check green on review PR head", wait_for_check(pr["headRefOid"]) == "success", pr["headRefOid"][:7])
    c.finish(outputs)


def after_pass2() -> None:
    c = Checks("pass 2 — same scenario again (no churn, dedupe)")
    fetch()
    base_sha = run("git", "rev-parse", f"origin/{BASE}")
    c.check("no churn commit on base while review is pending", base_sha == os.environ["PASS1_BASE_SHA"], base_sha[:7])
    prs = open_prs()
    c.check("review PR still open", len(prs) == 1 and str(prs[0]["number"]) == os.environ["PASS1_PR"])
    blob = run("git", "rev-parse", f"origin/{PR_BRANCH}:model-tiers.json", check=False)
    c.check("review PR content unchanged", blob == os.environ["PASS1_PR_BLOB"], blob[:7])
    issues = alert_issues("open")
    c.check(
        "alert not re-posted for the same problems",
        len(issues) == 1 and str(issues[0]["number"]) == os.environ["PASS1_ISSUE"] and len(issues[0]["comments"]) == 0,
        f"{len(issues)} open, comments={[len(i['comments']) for i in issues]}",
    )
    c.finish()


def after_pass3() -> None:
    c = Checks("pass 3 — healthy run (auto-close + heartbeat)")
    fetch()
    pr = gh_json("pr", "view", os.environ["PASS1_PR"], "--json", "state,comments")
    c.check(
        "review PR auto-closed as superseded",
        pr["state"] == "CLOSED" and any("Superseded" in cm["body"] for cm in pr["comments"]),
        pr["state"],
    )
    c.check("review branch deleted", not remote_branch_exists(PR_BRANCH))
    issue = gh_json("issue", "view", os.environ["PASS1_ISSUE"], "--json", "state,comments")
    c.check(
        "alert issue auto-closed on recovery",
        issue["state"] == "CLOSED" and bool(issue["comments"]) and "Recovered" in issue["comments"][-1]["body"],
        issue["state"],
    )
    base_ref = f"origin/{BASE}"
    subject = run("git", "log", "-1", "--format=%s", base_ref)
    beat = run("git", "show", f"{base_ref}:.github/refresh-heartbeat", check=False)
    c.check(
        "heartbeat commit pushed when base looked idle",
        subject.startswith("chore: refresh heartbeat") and "actions/runs/" in beat,
        subject,
    )
    with tempfile.TemporaryDirectory() as tmp:
        wt = Path(tmp) / "wt"
        run("git", "worktree", "add", "-q", "--detach", str(wt), base_ref)
        proc = subprocess.run([sys.executable, str(wt / "scripts" / "validate.py")], capture_output=True, text=True)
        run("git", "worktree", "remove", "--force", str(wt), check=False)
    c.check("sandbox data passes validate.py", proc.returncode == 0, proc.stdout.strip() or proc.stderr.strip())
    c.check("no review PR left open", not open_prs())
    c.finish()


STAGES = {
    "cleanup": cleanup,
    "after-pass1": after_pass1,
    "after-pass2": after_pass2,
    "after-pass3": after_pass3,
}

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in STAGES:
        sys.exit(f"usage: {sys.argv[0]} {{{'|'.join(STAGES)}}}")
    STAGES[sys.argv[1]]()
