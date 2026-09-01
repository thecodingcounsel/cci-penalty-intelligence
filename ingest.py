"""Real-data ingestion pipeline runner for CCI Section 43A/44 orders.

Runs discovery -> dedup -> PDF download -> text extraction -> objective
field extraction -> write data/orders.csv, for as many orders as are
published on the official CCI listing. This is a script, not part of the
app - app.py only ever reads the CSV this produces; it never imports
crawler.py, pdf_extract.py, or extract_fields.py.

Usage:
    python ingest.py            # ingest the FULL current corpus
    python ingest.py --count 10 # ingest only the 10 most recent orders (for a quick test run)

Errors and low-confidence extractions are logged to data/ingestion_errors.csv
rather than stopping the run - one bad order never blocks the rest.
"""

from __future__ import annotations

import argparse
import traceback
from datetime import date
from pathlib import Path

import pandas as pd

from src.crawler import DiscoveredOrder, dedupe_orders, discover_all_orders, discover_orders, download_pdfs
from src.data import REQUIRED_COLUMNS
from src.extract_fields import extract_penalty_info
from src.pdf_extract import extract_text, save_text

INTERPRETIVE_COLUMNS = [
    "conduct_type",
    "transaction_type",
    "sector",
    "notification_issue",
    "gun_jumping_type",
    "voluntary_disclosure",
    "cooperation",
    "mitigating_factors",
    "aggravating_factors",
]

DATA_PATH = "data/orders.csv"
PDF_DIR = "data/pdfs"
TEXT_DIR = "data/text"
FAKE_SAMPLE_BACKUP_PATH = "data/sample_orders.csv"
ERROR_LOG_PATH = "data/ingestion_errors.csv"


def backup_fake_rows_if_present(data_path: str, backup_path: str) -> int:
    """If data/orders.csv still has fake `[FAKE SAMPLE]` rows in it, back them up and
    report how many were found. Returns 0 if the file doesn't exist or has none.
    """
    path = Path(data_path)
    if not path.exists():
        return 0
    try:
        existing = pd.read_csv(path)
    except Exception:
        return 0
    if "case_name" not in existing.columns:
        return 0

    fake_mask = existing["case_name"].astype(str).str.contains(r"\[FAKE SAMPLE\]", na=False, regex=True)
    if not fake_mask.any():
        return 0

    existing[fake_mask].to_csv(backup_path, index=False)
    return int(fake_mask.sum())


def build_row(order: DiscoveredOrder, penalty_info: dict, pdf_status: str, text_info: dict) -> dict:
    issues = list(order.issues) + list(penalty_info["issues"])
    if pdf_status not in ("downloaded", "skipped_exists"):
        issues.append(f"PDF not available locally (status: {pdf_status})")
    if text_info.get("error"):
        issues.append(f"PDF text extraction error: {text_info['error']}")
    if text_info.get("likely_scanned"):
        issues.append("extraction_failed: PDF has little/no extractable text - may be scanned; OCR not implemented")

    notes = f"Auto-extracted by ingestion pipeline on {date.today().isoformat()}; unverified."
    if issues:
        notes += " ISSUES: " + " | ".join(issues)

    row = {
        "order_id": order.order_id,
        "combination_registration_no": order.combination_registration_no,
        "case_name": order.case_name,
        "decision_date": order.decision_date,
        "provision": order.provision,
        "penalty_43a": penalty_info["penalty_43a"],
        "penalty_44": penalty_info["penalty_44"],
        "penalty_45": penalty_info["penalty_45"],
        "total_penalty": penalty_info["total_penalty"],
        "penalty_source_page": penalty_info["penalty_source_page"],
        "penalty_source_text": penalty_info["penalty_source_text"],
        "source_url": order.source_url,
        "pdf_url": order.pdf_url,
        "include_default": True,
        "is_outlier": False,
        "verified": False,
        "notes": notes,
    }
    for col in INTERPRETIVE_COLUMNS:
        row[col] = ""
    return row


def run(count: int | None) -> dict:
    """Runs the full pipeline. Returns a stats dict used to print the final report."""
    error_log: list[dict] = []

    fake_backed_up = backup_fake_rows_if_present(DATA_PATH, FAKE_SAMPLE_BACKUP_PATH)
    if fake_backed_up:
        print(f"[0/5] Backed up {fake_backed_up} fake sample row(s) still in {DATA_PATH} to {FAKE_SAMPLE_BACKUP_PATH}.")
    else:
        print(f"[0/5] No fake sample rows found in {DATA_PATH} - nothing to back up.")

    print("[1/5] Discovering orders from the official CCI 43A/44 listing (paginating as needed)...")
    if count is None:
        orders, total_reported = discover_all_orders()
    else:
        orders, total_reported = discover_orders(count), count
    print(f"      Retrieved {len(orders)} order rows (CCI reports {total_reported} total).")

    orders, duplicates_removed = dedupe_orders(orders)
    print(f"      {len(orders)} unique orders after de-duplication ({duplicates_removed} duplicate(s) removed).")

    for order in orders:
        for issue in order.issues:
            error_log.append({"order_id": order.order_id, "stage": "discovery", "issue": issue})

    print(f"[2/5] Downloading PDFs to {PDF_DIR}/ (skipping any already present)...")
    download_results = download_pdfs(orders, out_dir=PDF_DIR)
    downloaded = sum(1 for r in download_results.values() if r["status"] == "downloaded")
    skipped = sum(1 for r in download_results.values() if r["status"] == "skipped_exists")
    failed_downloads = sum(1 for r in download_results.values() if r["status"] not in ("downloaded", "skipped_exists"))
    print(f"      {downloaded} newly downloaded, {skipped} already present, {failed_downloads} failed.")
    for order_id, result in download_results.items():
        if result["status"] not in ("downloaded", "skipped_exists"):
            error_log.append({
                "order_id": order_id, "stage": "download",
                "issue": f"status={result['status']} error={result['error']}",
            })

    print(f"[3/5] Extracting text to {TEXT_DIR}/ and parsing objective penalty fields...")
    rows = []
    extraction_ok = 0
    likely_scanned_count = 0
    penalty_43a_count = penalty_44_count = penalty_45_count = 0
    blank_penalty_count = 0

    for order in orders:
        try:
            dl = download_results.get(order.order_id, {"status": "no_pdf_url", "path": None})
            if dl["path"]:
                text_info = extract_text(dl["path"])
                save_text(text_info["text"], order.order_id, out_dir=TEXT_DIR)
            else:
                text_info = {"text": "", "page_texts": [], "likely_scanned": False, "error": "no PDF downloaded"}

            if text_info.get("error"):
                error_log.append({"order_id": order.order_id, "stage": "text_extraction", "issue": text_info["error"]})
            elif text_info.get("likely_scanned"):
                likely_scanned_count += 1
                error_log.append({
                    "order_id": order.order_id, "stage": "text_extraction",
                    "issue": "extraction_failed: little/no extractable text, possibly scanned",
                })
            elif dl.get("path"):
                extraction_ok += 1

            penalty_info = extract_penalty_info(text_info["text"], text_info.get("page_texts", []), order.provision)
            for issue in penalty_info["issues"]:
                error_log.append({"order_id": order.order_id, "stage": "penalty_extraction", "issue": issue})

            if penalty_info["penalty_43a"] is not None:
                penalty_43a_count += 1
            if penalty_info["penalty_44"] is not None:
                penalty_44_count += 1
            if penalty_info["penalty_45"] is not None:
                penalty_45_count += 1
            if penalty_info["total_penalty"] is None:
                blank_penalty_count += 1

            row = build_row(order, penalty_info, dl["status"], text_info)
            rows.append(row)
            status = "OK" if not (order.issues or penalty_info["issues"]) else "REVIEW"
            print(f"      {order.order_id}: 43A={penalty_info['penalty_43a']} 44={penalty_info['penalty_44']} 45={penalty_info['penalty_45']} [{status}]")

        except Exception as exc:  # a single order's unexpected failure must never abort the batch
            error_log.append({
                "order_id": order.order_id, "stage": "unexpected_error",
                "issue": f"{exc.__class__.__name__}: {exc}",
            })
            print(f"      {order.order_id}: UNEXPECTED ERROR - {exc} (logged, continuing)")
            traceback.print_exc()
            rows.append(build_row(order, {
                "penalty_43a": None, "penalty_44": None, "penalty_45": None, "total_penalty": None,
                "penalty_source_page": None, "penalty_source_text": "",
                "issues": [f"unexpected pipeline error: {exc}"],
            }, "failed", {"error": None, "likely_scanned": False}))

    df = pd.DataFrame(rows)[REQUIRED_COLUMNS]

    print(f"[4/5] Writing {len(df)} rows to {DATA_PATH} ...")
    df.to_csv(DATA_PATH, index=False)

    print(f"[5/5] Writing {len(error_log)} logged issue(s) to {ERROR_LOG_PATH} ...")
    pd.DataFrame(error_log, columns=["order_id", "stage", "issue"]).to_csv(ERROR_LOG_PATH, index=False)
    print("      Done.")

    return {
        "total_discovered": len(orders) + duplicates_removed,
        "duplicates_removed": duplicates_removed,
        "unique_orders": len(orders),
        "pdfs_downloaded_new": downloaded,
        "pdfs_already_present": skipped,
        "pdf_download_failures": failed_downloads,
        "text_extraction_ok": extraction_ok,
        "text_extraction_failures": likely_scanned_count,
        "rows_written": len(df),
        "penalty_43a_count": penalty_43a_count,
        "penalty_44_count": penalty_44_count,
        "penalty_45_count": penalty_45_count,
        "blank_or_uncertain_penalty_count": blank_penalty_count,
        "error_log_entries": len(error_log),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--count", type=int, default=None,
        help="number of most-recent orders to ingest (default: all available orders)",
    )
    args = parser.parse_args()

    stats = run(args.count)

    print()
    print("=== Ingestion summary ===")
    for key, value in stats.items():
        print(f"{key}: {value}")
