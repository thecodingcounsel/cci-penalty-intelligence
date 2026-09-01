"""Incremental corpus updater for CCI Penalty Intelligence.

Revisits the official CCI Section 43A/44 listing, finds orders not already
in data/orders.csv (by order_id), and processes ONLY those: downloads their
PDFs, extracts text, extracts objective penalty fields, and appends the new
rows. Existing rows are never re-downloaded, re-parsed, or overwritten.

This is NOT real-time updating - it is a manual/scheduled batch step you
(or a cron job in a future version) run periodically. The UI shows when it
was last run via data/corpus_metadata.json.

Usage:
    python update_corpus.py
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from build_intelligence import run as build_intelligence
from ingest import DATA_PATH, ERROR_LOG_PATH, PDF_DIR, TEXT_DIR, build_row
from src.crawler import dedupe_orders, discover_all_orders, download_pdfs
from src.data import REQUIRED_COLUMNS
from src.extract_fields import extract_penalty_info
from src.pdf_extract import extract_text, save_text

METADATA_PATH = "data/corpus_metadata.json"


def run() -> dict:
    existing = pd.read_csv(DATA_PATH) if Path(DATA_PATH).exists() else pd.DataFrame(columns=REQUIRED_COLUMNS)
    known_ids = set(existing["order_id"].astype(str)) if "order_id" in existing.columns else set()
    print(f"[1/4] {len(known_ids)} orders already in {DATA_PATH}.")

    print("[2/4] Checking the official CCI listing for new orders...")
    all_orders, total_reported = discover_all_orders()
    all_orders, _ = dedupe_orders(all_orders)
    new_orders = [o for o in all_orders if o.order_id and o.order_id not in known_ids]
    print(f"      CCI reports {total_reported} orders total; {len(new_orders)} are new.")

    error_log_rows = []
    new_rows = []

    if new_orders:
        print(f"[3/4] Downloading and processing {len(new_orders)} new order(s)...")
        download_results = download_pdfs(new_orders, out_dir=PDF_DIR)

        for order in new_orders:
            dl = download_results.get(order.order_id, {"status": "no_pdf_url", "path": None})
            if dl["path"]:
                text_info = extract_text(dl["path"])
                save_text(text_info["text"], order.order_id, out_dir=TEXT_DIR)
            else:
                text_info = {"text": "", "page_texts": [], "likely_scanned": False, "error": "no PDF downloaded"}

            penalty_info = extract_penalty_info(text_info["text"], text_info.get("page_texts", []), order.provision)
            row = build_row(order, penalty_info, dl["status"], text_info)
            new_rows.append(row)

            for issue in order.issues + penalty_info["issues"]:
                error_log_rows.append({"order_id": order.order_id, "stage": "update_corpus", "issue": issue})
            if dl["status"] not in ("downloaded", "skipped_exists"):
                error_log_rows.append({
                    "order_id": order.order_id, "stage": "download",
                    "issue": f"status={dl['status']} error={dl.get('error')}",
                })
            print(f"      {order.order_id}: {order.case_name[:50]} - penalty_43a={penalty_info['penalty_43a']}")
    else:
        print("[3/4] No new orders - nothing to download.")

    if new_rows:
        combined = pd.concat([existing, pd.DataFrame(new_rows)[REQUIRED_COLUMNS]], ignore_index=True)
        combined = combined.drop_duplicates(subset="order_id", keep="first")
        combined.to_csv(DATA_PATH, index=False)
        print(f"      Appended {len(new_rows)} row(s) to {DATA_PATH} ({len(combined)} total).")

        if error_log_rows:
            prior_errors = pd.read_csv(ERROR_LOG_PATH) if Path(ERROR_LOG_PATH).exists() else pd.DataFrame(
                columns=["order_id", "stage", "issue"])
            pd.concat([prior_errors, pd.DataFrame(error_log_rows)], ignore_index=True).to_csv(
                ERROR_LOG_PATH, index=False)

        print("[4/4] Rebuilding legal intelligence layer for the full corpus...")
        build_intelligence()
    else:
        print("[4/4] Nothing new - legal intelligence layer unchanged.")

    total_orders = len(existing) + len(new_rows)
    metadata = {
        "last_updated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_orders": total_orders,
        "new_orders_this_run": len(new_rows),
        "cci_reported_total": total_reported,
    }
    Path(METADATA_PATH).write_text(json.dumps(metadata, indent=2))

    return metadata


if __name__ == "__main__":
    result = run()
    print()
    print("=== Update summary ===")
    for key, value in result.items():
        print(f"{key}: {value}")
