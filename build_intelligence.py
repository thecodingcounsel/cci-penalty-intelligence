"""Builds data/legal_intelligence.csv from the already-ingested corpus.

Reads data/orders.csv (for order_id + provenance already captured by the
penalty pipeline) and data/text/<order_id>.txt (the extracted order text),
and writes one enriched row per order via src/intelligence.py. This is a
separate, re-runnable step from ingest.py - it never re-crawls or
re-downloads anything, it only re-derives the interpretive layer from text
already on disk.

Two modes:
- Full rebuild (default, run()/run(incremental=False)): re-derives every
  row from scratch. Uses each order's local PDF (data/pdfs/<id>.pdf) to
  look up which page a provenance excerpt came from - only safe to run
  where those PDFs are actually present (i.e. locally after ingest.py/
  update_corpus.py has downloaded them). Intended for manual runs, e.g.
  after improving the extraction patterns in src/intelligence.py.
- Incremental (run(incremental=True)): only computes intelligence for
  order_ids not already in legal_intelligence.csv, and leaves every
  existing row byte-for-byte untouched. This is what the scheduled
  updater (update_corpus.py) uses - in a CI checkout, data/pdfs/ is
  intentionally absent (gitignored, too large for git) EXCEPT for
  whatever a same-run ingest just downloaded, so a full rebuild there
  would silently blank out intelligence_source_page for every pre-
  existing row. Incremental mode only ever needs the PDF for a brand-new
  order, which was just downloaded in this same run and is present.

Usage:
    python build_intelligence.py                # full rebuild (local use)
    python build_intelligence.py --incremental   # new orders only (CI use)
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from src.intelligence import LEGAL_INTELLIGENCE_COLUMNS, build_case_intelligence
from src.pdf_extract import extract_text

ORDERS_PATH = "data/orders.csv"
TEXT_DIR = Path("data/text")
PDF_DIR = Path("data/pdfs")
OUTPUT_PATH = "data/legal_intelligence.csv"


def _build_row(order: pd.Series) -> dict:
    order_id = order["order_id"]
    text_path = TEXT_DIR / f"{order_id}.txt"
    text = text_path.read_text(encoding="utf-8") if text_path.exists() else ""

    pdf_path = PDF_DIR / f"{order_id}.pdf"
    page_texts = extract_text(str(pdf_path))["page_texts"] if pdf_path.exists() else []

    penalty_source_text = order.get("penalty_source_text", "")
    penalty_source_text = "" if pd.isna(penalty_source_text) else str(penalty_source_text)

    return build_case_intelligence(order_id, text, page_texts, penalty_source_text)


def run(incremental: bool = False) -> pd.DataFrame:
    orders = pd.read_csv(ORDERS_PATH)

    if not incremental:
        rows = [_build_row(order) for _, order in orders.iterrows()]
        df = pd.DataFrame(rows)[LEGAL_INTELLIGENCE_COLUMNS]
        df.to_csv(OUTPUT_PATH, index=False)
        return df

    existing = (
        pd.read_csv(OUTPUT_PATH) if Path(OUTPUT_PATH).exists()
        else pd.DataFrame(columns=LEGAL_INTELLIGENCE_COLUMNS)
    )
    existing_ids = set(existing["order_id"].astype(str)) if "order_id" in existing.columns else set()

    new_orders = orders[~orders["order_id"].astype(str).isin(existing_ids)]
    if new_orders.empty:
        return existing

    new_rows = [_build_row(order) for _, order in new_orders.iterrows()]
    combined = pd.concat([existing, pd.DataFrame(new_rows)[LEGAL_INTELLIGENCE_COLUMNS]], ignore_index=True)
    combined.to_csv(OUTPUT_PATH, index=False)
    return combined


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--incremental", action="store_true",
        help="only compute intelligence for order_ids not already in legal_intelligence.csv",
    )
    args = parser.parse_args()

    df = run(incremental=args.incremental)
    print(f"Wrote {len(df)} rows to {OUTPUT_PATH}")
    for col in ["conduct_type", "transaction_type", "voluntary_disclosure", "cooperation", "penalty_reduced"]:
        non_blank = (df[col] != "").sum()
        print(f"  {col}: {non_blank}/{len(df)} populated")
