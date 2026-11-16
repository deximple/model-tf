#!/usr/bin/env python3
"""Self-test only: recreate the real Vals AI breakage of 2026-11-16.

Used by .github/workflows/selftest-repair.yml on a throwaway branch so the
auto-repair agent faces a genuine failure against the live site: the
collector points at the old hub URL and only understands the old page layout,
exactly as before the fix, so the Vals source reports "parsed 0 rows".

Edits in place:
  - sources/fetch_artificial_analysis.py: VALS_URL back to the hub page, and
    the parser branch for the current ValsIndexSection layout removed
  - tests/test_repo.py: the unit test for the current layout removed, and the
    regression test of this script (meaningless once the code is broken)
  - model-tiers.json: Vals evidence cited the hub URL back then, so it does
    again (otherwise the reverted code would treat it as hand-curated)

Usage: python3 scripts/selftest_break_vals.py
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COLLECTOR = ROOT / "sources" / "fetch_artificial_analysis.py"
TESTS = ROOT / "tests" / "test_repo.py"
TIERS = ROOT / "model-tiers.json"
NEW_VALS_URL = "https://www.vals.ai/benchmarks/vals_index"
OLD_VALS_URL = "https://www.vals.ai/benchmarks"

NEW_LAYOUT_BRANCH = '''    for props in _astro_props(page, "LeaderboardItems"):
        for list_key in ("regularLeaderboardItems", "openSourceLeaderboardItems"):
            for elem in _astro_value(props.get(list_key)) or []:
                obj = _astro_value(elem)
                if isinstance(obj, dict):
                    add(_astro_value(obj.get("model")), _astro_value(obj.get("accuracy")))
    if out:
        return out

'''


def replace_once(text: str, old: str, new: str, what: str) -> str:
    if text.count(old) != 1:
        sys.exit(f"selftest_break_vals: expected exactly one {what}; found {text.count(old)}")
    return text.replace(old, new)


def break_collector(src: str) -> str:
    src = replace_once(
        src,
        'VALS_URL = "https://www.vals.ai/benchmarks/vals_index"',
        'VALS_URL = "https://www.vals.ai/benchmarks"',
        "VALS_URL definition",
    )
    return replace_once(src, NEW_LAYOUT_BRANCH, "", "current-layout parser branch")


REMOVED_TESTS = (
    "test_parses_current_vals_index_page_layout",
    "test_selftest_breakage_still_applies_to_current_code",
)


def break_tests(src: str) -> str:
    for name in REMOVED_TESTS:
        pattern = re.compile(rf"    def {name}\(self\) -> None:\n.*?(?=\n    def )", re.S)
        src, n = pattern.subn("", src)
        if n != 1:
            sys.exit(f"selftest_break_vals: expected one {name}; found {n}")
    return src


def break_tiers(src: str) -> str:
    quoted = f'"{NEW_VALS_URL}"'
    if quoted not in src:
        sys.exit("selftest_break_vals: no Vals evidence with the current URL in model-tiers.json")
    return src.replace(quoted, f'"{OLD_VALS_URL}"')


def main() -> None:
    COLLECTOR.write_text(break_collector(COLLECTOR.read_text(encoding="utf-8")), encoding="utf-8")
    TESTS.write_text(break_tests(TESTS.read_text(encoding="utf-8")), encoding="utf-8")
    TIERS.write_text(break_tiers(TIERS.read_text(encoding="utf-8")), encoding="utf-8")
    print("selftest: Vals collector reverted to the pre-2026-11-16 URL and layout")


if __name__ == "__main__":
    main()
