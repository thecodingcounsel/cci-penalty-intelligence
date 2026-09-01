"""Builds data/legal_intelligence.csv from the already-ingested corpus.

Reads data/orders.csv (for order_id + provenance already captured by the
penalty pipeline) and data/text/<order_id>.txt (the extracted order text),
and writes one enriched row per order via src/intelligence.py. This is a
separate, re-runnable step from ingest.py - it never re-crawls or
re-downloads anything, it only re-derives the interpretive layer from text
already on disk.

Usage:
    python build_intelligence.py
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.intelligence import LEGAL_INTELLIGENCE_COLUMNS, build_case_intelligence
from src.pdf_extract import extract_text

ORDERS_PATH = "data/orders.csv"
TEXT_DIR = Path("data/text")
OUTPUT_PATH = "data/legal_intelligence.csv"


def run() -> pd.DataFrame:
    orders = pd.read_csv(ORDERS_PATH)
    rows = []

    for _, order in orders.iterrows():
        order_id = order["order_id"]
        text_path = TEXT_DIR / f"{order_id}.txt"
        text = text_path.read_text(encoding="utf-8") if text_path.exists() else ""

        pdf_path = Path("data/pdfs") / f"{order_id}.pdf"
        page_texts = extract_text(str(pdf_path))["page_texts"] if pdf_path.exists() else []

        penalty_source_text = order.get("penalty_source_text", "")
        penalty_source_text = "" if pd.isna(penalty_source_text) else str(penalty_source_text)

        row = build_case_intelligence(order_id, text, page_texts, penalty_source_text)
        rows.append(row)

    df = pd.DataFrame(rows)[LEGAL_INTELLIGENCE_COLUMNS]
    df.to_csv(OUTPUT_PATH, index=False)
    return df


if __name__ == "__main__":
    df = run()
    print(f"Wrote {len(df)} rows to {OUTPUT_PATH}")
    for col in ["conduct_type", "transaction_type", "voluntary_disclosure", "cooperation", "penalty_reduced"]:
        non_blank = (df[col] != "").sum()
        print(f"  {col}: {non_blank}/{len(df)} populated")
