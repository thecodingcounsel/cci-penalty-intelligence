"""Lightweight smoke test for CCI Penalty Intelligence.

Run before trusting a freshly-ingested corpus (locally, or as a gate in the
scheduled GitHub Actions updater before it's allowed to commit/push):

    python smoke_test.py

Checks that the app itself loads without error, the corpus count is derived
live from the data (never hardcoded), there are no duplicate order_ids, and
core penalty statistics compute without raising. This is a smoke test, not
a full test suite - it exists to catch "the update broke something" before
that something reaches production, not to validate every extraction rule.
"""

from __future__ import annotations

import sys

from streamlit.testing.v1 import AppTest

from src.analytics import compute_penalty_stats
from src.data import load_full_dataset


def main() -> int:
    failures: list[str] = []

    df = load_full_dataset()
    print(f"Loaded {len(df)} orders from data/orders.csv (count is always read live, never hardcoded).")

    if len(df) == 0:
        failures.append("corpus is empty")

    dupes = df["order_id"][df["order_id"].duplicated()]
    if not dupes.empty:
        failures.append(f"duplicate order_id(s): {sorted(dupes.unique())}")

    try:
        stats = compute_penalty_stats(df, "penalty_43a")
        if stats["count"] <= 0:
            failures.append("compute_penalty_stats ran but found zero penalty_43a values")
    except Exception as exc:  # noqa: BLE001 - want to catch and report, not just crash
        failures.append(f"compute_penalty_stats raised: {exc}")

    at = AppTest.from_file("app.py")
    at.run(timeout=60)
    if at.exception:
        for exc in at.exception:
            failures.append(f"app.py raised on load: {exc}")

    if failures:
        print("SMOKE TEST FAILED:")
        for f in failures:
            print(" -", f)
        return 1

    print(f"SMOKE TEST PASSED - {len(df)} orders, all checks green.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
