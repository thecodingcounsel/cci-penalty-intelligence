"""Data ingestion layer for CCI Penalty Intelligence.

This is the ONLY module that knows where the data lives and what format it
is in. In V1 that's data/orders.csv. In V2 this file is the one place that
changes to read from SQLite instead — src/analytics.py and app.py should
never need to change, because both only ever see the clean DataFrame that
load_orders() returns.
"""

from __future__ import annotations

import pandas as pd

REQUIRED_COLUMNS = [
    "order_id",
    "combination_registration_no",
    "case_name",
    "decision_date",
    "provision",
    "penalty_43a",
    "penalty_44",
    "penalty_45",
    "total_penalty",
    "penalty_source_page",
    "penalty_source_text",
    "conduct_type",
    "transaction_type",
    "sector",
    "notification_issue",
    "gun_jumping_type",
    "voluntary_disclosure",
    "cooperation",
    "mitigating_factors",
    "aggravating_factors",
    "source_url",
    "pdf_url",
    "include_default",
    "is_outlier",
    "verified",
    "notes",
]

NUMERIC_COLUMNS = ["penalty_43a", "penalty_44", "penalty_45", "total_penalty"]
BOOLEAN_COLUMNS = ["include_default", "is_outlier", "verified"]
TEXT_COLUMNS = [
    c for c in REQUIRED_COLUMNS
    if c not in NUMERIC_COLUMNS + BOOLEAN_COLUMNS + ["decision_date"]
]


class DataValidationError(Exception):
    """Raised when the source data is missing required columns."""


def load_orders(path: str = "data/orders.csv") -> pd.DataFrame:
    """Load, validate and clean the orders dataset.

    Always returns a DataFrame with every column in REQUIRED_COLUMNS present
    and correctly typed (dates parsed, penalties numeric, flags boolean),
    even if the source file has blank cells or malformed values. Bad or
    missing values become NaN / False / "" rather than raising, so a messy
    row never crashes the app - it just won't count toward numeric stats.
    """
    df = pd.read_csv(path)

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise DataValidationError(
            f"{path} is missing required columns: {', '.join(missing)}"
        )

    df = df[REQUIRED_COLUMNS].copy()

    df["decision_date"] = pd.to_datetime(df["decision_date"], errors="coerce")
    df["year"] = df["decision_date"].dt.year

    for col in NUMERIC_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    for col in BOOLEAN_COLUMNS:
        df[col] = _to_bool(df[col])

    for col in TEXT_COLUMNS:
        df[col] = df[col].fillna("").astype(str).str.strip()

    return df


def _to_bool(series: pd.Series) -> pd.Series:
    """Parse True/False/Yes/No-like text into real booleans, defaulting anything unclear to False."""
    return (
        series.astype(str)
        .str.strip()
        .str.lower()
        .map({"true": True, "1": True, "yes": True, "false": False, "0": False, "no": False})
        .fillna(False)
    )


INTELLIGENCE_OVERLAP_COLUMNS = [
    "conduct_type", "transaction_type", "notification_issue", "gun_jumping_type",
    "voluntary_disclosure", "cooperation", "mitigating_factors", "aggravating_factors",
]
INTELLIGENCE_ONLY_TEXT_COLUMNS = [
    "penalty_rationale", "legal_issue_summary", "outcome_summary", "date_of_closing",
    "date_of_notification", "closed_before_approval", "control_exercised_before_approval",
    "information_rights_exercised", "penalty_reduced", "intelligence_source_page",
    "intelligence_source_text", "intelligence_issues",
]
INTELLIGENCE_NUMERIC_COLUMNS = ["delay_duration_days", "transaction_value"]


def load_legal_intelligence(path: str = "data/legal_intelligence.csv") -> pd.DataFrame:
    """Load the enrichment layer produced by build_intelligence.py. Cleaned the same way
    as load_orders(): required columns validated, numerics coerced, blanks normalized.
    """
    from src.intelligence import LEGAL_INTELLIGENCE_COLUMNS  # local import avoids a hard dependency for callers that only need orders.csv

    df = pd.read_csv(path)
    missing = [c for c in LEGAL_INTELLIGENCE_COLUMNS if c not in df.columns]
    if missing:
        raise DataValidationError(f"{path} is missing required columns: {', '.join(missing)}")

    df = df[LEGAL_INTELLIGENCE_COLUMNS].copy()
    for col in INTELLIGENCE_NUMERIC_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    text_cols = [c for c in LEGAL_INTELLIGENCE_COLUMNS if c not in INTELLIGENCE_NUMERIC_COLUMNS + ["order_id"]]
    for col in text_cols:
        df[col] = df[col].fillna("").astype(str).str.strip()

    return df


def load_full_dataset(
    orders_path: str = "data/orders.csv", intelligence_path: str = "data/legal_intelligence.csv"
) -> pd.DataFrame:
    """Load orders.csv and left-join legal_intelligence.csv onto it by order_id.

    Degrades gracefully if legal_intelligence.csv doesn't exist yet or is invalid -
    the app still works with orders.csv alone, just without the enrichment columns.
    Where both files define the same interpretive field (e.g. conduct_type),
    legal_intelligence.csv's value wins whenever it has one; orders.csv's value
    (usually blank, but honoured if a lawyer has hand-edited it directly) is the
    fallback.
    """
    orders = load_orders(orders_path)
    try:
        intelligence = load_legal_intelligence(intelligence_path)
    except (FileNotFoundError, DataValidationError):
        intelligence = pd.DataFrame(columns=["order_id"])

    merged = orders.merge(intelligence, on="order_id", how="left", suffixes=("", "_intel"))

    for col in INTELLIGENCE_OVERLAP_COLUMNS:
        intel_col = f"{col}_intel"
        if intel_col not in merged.columns:
            continue
        intel_values = merged[intel_col].fillna("").astype(str).str.strip()
        merged[col] = intel_values.where(intel_values != "", merged[col])
        merged.drop(columns=[intel_col], inplace=True)

    for col in INTELLIGENCE_ONLY_TEXT_COLUMNS:
        if col in merged.columns:
            merged[col] = merged[col].fillna("").astype(str).str.strip()
        else:
            merged[col] = ""

    for col in INTELLIGENCE_NUMERIC_COLUMNS:
        if col in merged.columns:
            merged[col] = pd.to_numeric(merged[col], errors="coerce")
        else:
            merged[col] = pd.NA

    return merged


def add_quality_flags(df: pd.DataFrame) -> pd.DataFrame:
    """Derive the quality-indicator badges shown in the UI. Never marks a case
    human-verified unless the `verified` column itself says so - automatically
    extracted data is never allowed to masquerade as manually checked.
    """
    df = df.copy()
    df["source_text_available"] = df["penalty_source_text"].astype(str).str.strip() != ""
    df["ocr_required"] = df["notes"].astype(str).str.contains("may be scanned", case=False, na=False)
    df["penalty_extracted"] = df["total_penalty"].notna()
    has_conduct = df.get("conduct_type", "").astype(str).str.strip() != ""
    has_summary = df.get("legal_issue_summary", "").astype(str).str.strip() != ""
    df["legal_intelligence_available"] = has_conduct | has_summary
    df["human_verified"] = df["verified"] == True  # noqa: E712 - explicit, not truthy-coerced
    return df


def get_filter_options(df: pd.DataFrame) -> dict:
    """Return sorted unique values for each sidebar filter, with blanks dropped."""

    def text_options(col: str) -> list:
        values = df[col].dropna().unique().tolist()
        return sorted(v for v in values if str(v).strip() != "")

    years = df["year"].dropna().unique().tolist()

    return {
        "provision": text_options("provision"),
        "year": sorted(int(y) for y in years),
        "conduct_type": text_options("conduct_type"),
        "transaction_type": text_options("transaction_type"),
        "sector": text_options("sector"),
        "voluntary_disclosure": text_options("voluntary_disclosure") if "voluntary_disclosure" in df.columns else [],
        "cooperation": text_options("cooperation") if "cooperation" in df.columns else [],
    }
