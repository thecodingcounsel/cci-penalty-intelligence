"""Order discovery and PDF download for CCI Section 43A/44 orders.

This is the only module that talks to cci.gov.in. It calls the same JSON
endpoint the official orders page's own table uses (a server-side
DataTables feed) - no HTML scraping, no invented data. src/data.py,
src/analytics.py and app.py never make network calls; only this module and
ingest.py do.
"""

from __future__ import annotations

import html
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import truststore

truststore.inject_into_ssl()  # cci.gov.in omits its intermediate cert; this validates via the OS trust store instead, same as a browser would

import requests

BASE_URL = "https://www.cci.gov.in"
LISTING_URL = f"{BASE_URL}/combination/orders-section43a_44"
ORDER_DETAIL_URL_TEMPLATE = f"{BASE_URL}/combination/order/details/order/{{order_ref}}/0/orders-section43a_44"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}

REQUEST_TIMEOUT_SECONDS = 30
DOWNLOAD_TIMEOUT_SECONDS = 60
POLITE_DELAY_SECONDS = 0.5
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 3


def _get_with_retries(url: str, **kwargs) -> requests.Response:
    """A plain requests.get() with a few retries on transient network/timeout errors -
    cci.gov.in occasionally times out under repeated requests. Raises the last
    exception if every attempt fails.
    """
    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(url, **kwargs)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    raise last_exc


@dataclass
class DiscoveredOrder:
    """Objectively identifiable metadata for one order, exactly as published by CCI - nothing inferred or guessed."""

    order_ref: str  # CCI's own internal numeric id for this order
    order_id: str  # our stable row id, e.g. "CCI-1760"
    combination_registration_no: str
    case_name: str
    decision_date: str  # ISO YYYY-MM-DD, or "" if it could not be parsed
    provision: str
    source_url: str
    pdf_url: str
    issues: list = field(default_factory=list)  # human-readable notes on anything not captured


DEFAULT_PAGE_SIZE = 25


def discover_orders(count: int | None = 10, page_size: int = DEFAULT_PAGE_SIZE) -> list[DiscoveredOrder]:
    """Fetch orders from the live CCI 43A/44 listing, newest first.

    `count=10` (the V1.1 default) fetches only the 10 most recent orders.
    `count=None` pages through the entire listing and returns every order
    CCI currently has published, however many that is - use
    `discover_all_orders()` if you also want the total record count CCI
    itself reports, for a completeness check.
    """
    rows, _total = _fetch_rows(count=count, page_size=page_size)
    return [_parse_row(row) for row in rows]


def discover_all_orders(page_size: int = DEFAULT_PAGE_SIZE) -> tuple[list[DiscoveredOrder], int]:
    """Fetch every order in the live listing. Returns (orders, total_reported_by_cci) so a
    caller can confirm nothing was missed (e.g. len(orders) == total_reported_by_cci).
    """
    rows, total = _fetch_rows(count=None, page_size=page_size)
    return [_parse_row(row) for row in rows], total


def dedupe_orders(orders: list[DiscoveredOrder]) -> tuple[list[DiscoveredOrder], int]:
    """Remove duplicate orders, keeping the first occurrence of each.

    Keyed on order_id (CCI's own internal id - the most stable identifier
    available) with source_url or combination_registration_no as a
    fallback for the rare row where order_id could not be built.
    Returns (deduplicated_orders, number_of_duplicates_removed).
    """
    seen: dict[str, DiscoveredOrder] = {}
    duplicates = 0
    for order in orders:
        key = order.order_id or order.source_url or order.combination_registration_no
        if not key:
            key = f"__no_key__{id(order)}"  # never collides - keeps unkeyable rows rather than silently dropping them
        if key in seen:
            duplicates += 1
            continue
        seen[key] = order
    return list(seen.values()), duplicates


def _fetch_rows(count: int | None, page_size: int) -> tuple[list[dict], int]:
    """Page through the DataTables JSON endpoint. Returns (raw_rows, total_records_reported_by_cci)."""
    all_rows: list[dict] = []
    total = None
    start = 0

    while True:
        remaining = None if count is None else count - len(all_rows)
        if remaining is not None and remaining <= 0:
            break
        length = page_size if remaining is None else min(page_size, remaining)

        params = {
            "draw": "1",
            "start": str(start),
            "length": str(length),
            "order[0][column]": "0",
            "order[0][dir]": "desc",
            "columns[0][data]": "DT_RowIndex",
            "columns[1][data]": "combination_no",
            "columns[2][data]": "description",
            "columns[3][data]": "section",
            "columns[4][data]": "decision_date",
            "columns[5][data]": "order_files",
        }
        response = _get_with_retries(LISTING_URL, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT_SECONDS)
        payload = response.json()

        data = payload.get("data", [])
        total = payload.get("recordsTotal", total if total is not None else len(data))
        all_rows.extend(data)
        start += len(data)

        if not data:
            break
        if total is not None and start >= total:
            break
        time.sleep(POLITE_DELAY_SECONDS)

    return all_rows, (total if total is not None else len(all_rows))


def _parse_row(row: dict) -> DiscoveredOrder:
    issues: list[str] = []

    order_ref = str(row.get("id") or "").strip()
    order_id = f"CCI-{order_ref}" if order_ref else ""
    if not order_ref:
        issues.append("missing CCI internal id - cannot build source/pdf URLs reliably")

    combination_no = html.unescape(str(row.get("combination_no") or "")).strip()
    if not combination_no:
        issues.append("combination_registration_no not found in listing data")

    case_name = html.unescape(str(row.get("description") or row.get("party_name") or "")).strip()
    if not case_name:
        issues.append("case_name not found in listing data")

    provision = str(row.get("section") or "").strip()
    if not provision:
        issues.append("provision/section not found in listing data")

    decision_date = _parse_listing_date(row.get("decision_date"))
    if not decision_date:
        issues.append(f"decision_date could not be parsed from raw value {row.get('decision_date')!r}")

    pdf_url = _extract_pdf_url(row.get("order_file_content"))
    if not pdf_url:
        issues.append("no PDF file found in order_file_content")

    source_url = ORDER_DETAIL_URL_TEMPLATE.format(order_ref=order_ref) if order_ref else ""
    if not source_url:
        issues.append("could not build source_url - missing order_ref")

    return DiscoveredOrder(
        order_ref=order_ref,
        order_id=order_id,
        combination_registration_no=combination_no,
        case_name=case_name,
        decision_date=decision_date,
        provision=provision,
        source_url=source_url,
        pdf_url=pdf_url,
        issues=issues,
    )


def _parse_listing_date(raw) -> str:
    """CCI's listing sends dates as DD/MM/YYYY. Returns ISO YYYY-MM-DD, or '' if the format doesn't match."""
    if not raw:
        return ""
    match = re.match(r"^(\d{2})/(\d{2})/(\d{4})$", str(raw).strip())
    if not match:
        return ""
    day, month, year = match.groups()
    return f"{year}-{month}-{day}"


def _extract_pdf_url(order_file_content) -> str:
    """order_file_content is an HTML-entity-encoded JSON array, e.g.
    '[{"title":"Order","file_name":"images/caseorders/en/order123.pdf","file_size":"306.87"}]'.
    Returns the first PDF's full URL, or '' if none is present or the field is malformed.
    """
    if not order_file_content:
        return ""
    try:
        files = json.loads(html.unescape(str(order_file_content)))
    except (ValueError, TypeError):
        return ""

    for entry in files:
        file_name = str(entry.get("file_name") or "").strip()
        if file_name.lower().endswith(".pdf"):
            return f"{BASE_URL}/{file_name}"
    return ""


def download_pdfs(orders: list[DiscoveredOrder], out_dir: str = "data/pdfs") -> dict:
    """Download each order's PDF to '<out_dir>/<order_id>.pdf'. Skips a file that already exists
    rather than re-downloading it.

    Returns {order_id: {"path": str|None, "status": "downloaded"|"skipped_exists"|"failed"|"no_pdf_url", "error": str|None}}.
    """
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    results: dict = {}
    for order in orders:
        if not order.order_id:
            continue

        if not order.pdf_url:
            results[order.order_id] = {"path": None, "status": "no_pdf_url", "error": None}
            continue

        dest = out_path / f"{order.order_id}.pdf"
        if dest.exists():
            results[order.order_id] = {"path": str(dest), "status": "skipped_exists", "error": None}
            continue

        try:
            response = _get_with_retries(order.pdf_url, headers=HEADERS, timeout=DOWNLOAD_TIMEOUT_SECONDS)
            dest.write_bytes(response.content)
            results[order.order_id] = {"path": str(dest), "status": "downloaded", "error": None}
        except requests.RequestException as exc:
            results[order.order_id] = {"path": None, "status": "failed", "error": str(exc)}

        time.sleep(POLITE_DELAY_SECONDS)

    return results
